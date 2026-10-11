"""限流与每日配额：保护大模型调用不被刷爆。

公开部署时这是**最关键**的一层。问答接口每被调用一次都从运营者的
DeepSeek 余额里扣一次钱，没有这层保护，一个循环脚本就能在一夜之间刷光余额。

两层防护：

1. **频率限制（滑动窗口）**
   内存实现，按用户与按 IP 各一条。防的是「同一秒发几百个请求」。

2. **每日配额（SQLite 持久化）**
   按账号和全局各一条，写入数据库，所以重启不会清零。
   超额时**不调用大模型**，直接降级回本地模板回答——既不花钱，也不给用户报错。

环境变量：

    CALCULUS_QUOTA_USER_DAILY    每账号每日大模型问答次数，默认 20（0 表示不限制）
    CALCULUS_QUOTA_GLOBAL_DAILY  全局每日大模型问答次数，默认 500（0 表示不限制）
    CALCULUS_RATE_USER_PER_MIN   每用户每分钟请求数，默认 6
    CALCULUS_RATE_IP_PER_MIN     每 IP 每分钟请求数，默认 20
"""

import os
import threading
import time

import store

_LOCK = threading.Lock()
_HITS = {}          # (scope, key) -> [timestamps]
_LAST_SWEEP = [0.0]


def _int_env(name, default):
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def quota_user_daily():
    return _int_env("CALCULUS_QUOTA_USER_DAILY", 20)


def quota_global_daily():
    return _int_env("CALCULUS_QUOTA_GLOBAL_DAILY", 500)


def rate_user_per_min():
    """花钱/耗资源接口的每用户频率。

    给得比较宽松（30/分），因为真正的费用护栏是**每日配额**；
    这一层只是阻止「一瞬间打几百次」的爆发式滥用。
    """
    return _int_env("CALCULUS_RATE_USER_PER_MIN", 30)


def rate_ip_per_min():
    """每 IP 总频率。一次页面加载就有 3~4 个请求，卡太死会误伤正常用户。"""
    return _int_env("CALCULUS_RATE_IP_PER_MIN", 240)


def rate_register_per_min():
    """注册的每 IP 频率。这是机器人刷号的主要入口，卡得最紧。"""
    return _int_env("CALCULUS_RATE_REGISTER_PER_MIN", 8)


def rate_login_per_min():
    """登录的每 IP 频率。要容忍用户输错密码重试，所以比注册宽松。"""
    return _int_env("CALCULUS_RATE_LOGIN_PER_MIN", 20)


def today():
    return time.strftime("%Y-%m-%d", time.gmtime())


# ---------------------------------------------------------------- 频率限制

def _sweep(now):
    """定期清掉过期的窗口，避免字典无限增长。"""
    if now - _LAST_SWEEP[0] < 300:
        return
    _LAST_SWEEP[0] = now
    cutoff = now - 120
    for key in list(_HITS.keys()):
        bucket = _HITS[key]
        if not bucket or bucket[-1] < cutoff:
            _HITS.pop(key, None)


def rate_check(scope, key, limit, window=60):
    """滑动窗口计数。返回 (是否允许, 当前次数, 建议重试秒数)。"""
    if limit <= 0:
        return True, 0, 0
    now = time.time()
    with _LOCK:
        _sweep(now)
        bucket = _HITS.setdefault((scope, key), [])
        while bucket and bucket[0] <= now - window:
            bucket.pop(0)
        if len(bucket) >= limit:
            retry_after = max(1, int(window - (now - bucket[0])))
            return False, len(bucket), retry_after
        bucket.append(now)
        return True, len(bucket), 0


# ---------------------------------------------------------------- 每日配额

def _peek(day, scope):
    row = store.q1("SELECT count FROM usage_daily WHERE day=? AND scope=?", (day, scope))
    return row["count"] if row else 0


def _incr(day, scope):
    store.ex(
        "INSERT INTO usage_daily(day, scope, count) VALUES(?,?,1)"
        " ON CONFLICT(day, scope) DO UPDATE SET count=count+1",
        (day, scope),
    )
    return _peek(day, scope)


def config_snapshot():
    """当前生效的护栏参数，给 /health 与启动日志用。"""
    return {
        "quota_user_daily": quota_user_daily(),
        "quota_global_daily": quota_global_daily(),
        "rate_user_per_min": rate_user_per_min(),
        "rate_ip_per_min": rate_ip_per_min(),
        "rate_register_per_min": rate_register_per_min(),
        "rate_login_per_min": rate_login_per_min(),
    }


def usage_snapshot(user_id=None):
    """给界面展示用的额度快照。"""
    day = today()
    global_used = _peek(day, "global")
    user_used = _peek(day, "user:%s" % user_id) if user_id else 0
    ug, gg = quota_user_daily(), quota_global_daily()
    return {
        "day": day,
        "user_used": user_used,
        "user_limit": ug,
        "user_remaining": max(0, ug - user_used) if ug > 0 else None,
        "global_used": global_used,
        "global_limit": gg,
        "global_remaining": max(0, gg - global_used) if gg > 0 else None,
    }


def consume_llm_quota(user_id):
    """为大模型调用扣一次配额。

    返回 (allowed, code, message, snapshot)。
    任何一项超额都返回 allowed=False，调用方应当**改用本地模板回答**，不要报错给用户。
    """
    ug, gg = quota_user_daily(), quota_global_daily()
    day = today()

    if ug > 0 and _peek(day, "user:%s" % user_id) >= ug:
        snap = usage_snapshot(user_id)
        return False, "LLM_QUOTA_EXCEEDED_USER", (
            "你今天的大模型问答额度已用完（%d/%d 次）。"
            "本次改为直接给出手册原文依据，明天 00:00（UTC）恢复。" % (ug, ug)), snap

    if gg > 0 and _peek(day, "global") >= gg:
        snap = usage_snapshot(user_id)
        return False, "LLM_QUOTA_EXCEEDED_GLOBAL", (
            "本站今天的大模型调用总量已达上限（%d 次），为避免费用失控已自动切回本地检索模式。"
            "明天 00:00（UTC）恢复。" % gg), snap

    _incr(day, "user:%s" % user_id)
    _incr(day, "global")
    return True, None, None, usage_snapshot(user_id)


def reset_quotas(day=None, user_id=None):
    """测试与运维用：清掉配额计数。"""
    day = day or today()
    if user_id:
        store.ex("DELETE FROM usage_daily WHERE day=? AND scope=?", (day, "user:%s" % user_id))
    else:
        store.ex("DELETE FROM usage_daily WHERE day=?", (day,))
    return True


def purge_old(days=7):
    """清理历史计数，避免表无限增长。"""
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    store.ex("DELETE FROM usage_daily WHERE day < ?", (cutoff,))
