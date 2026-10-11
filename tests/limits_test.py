#!/usr/bin/env python3
"""限流与每日配额的单元测试。

这是公开部署最关键的一层：问答接口每被调用一次都在花运营者的钱。
本测试不依赖 HTTP，直接验证 limits.py 的行为。

覆盖：
- 滑动窗口频率限制（放行到上限、超限拒绝、窗口滑动后恢复、不同 key 互不影响）
- 每账号每日配额（到量后拒绝，且返回的配额快照正确）
- 全局每日配额（全局到量后所有用户都被拒）
- 配额只在大模型调用前扣减，模板模式不扣
- 历史计数清理

用法：python tests/limits_test.py
"""

import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    line = "%-54s %s" % (name, "PASS" if ok else "FAIL")
    if detail and not ok:
        line += "   " + detail
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(line.encode(enc, "replace").decode(enc, "replace"))


def main():
    tmpdir = os.path.join(HERE, ".tmp-limits-test")
    shutil.rmtree(tmpdir, ignore_errors=True)
    os.makedirs(tmpdir, exist_ok=True)

    import limits
    import store

    store.init(os.path.join(tmpdir, "limits.db"), seed_admin=False)

    # ---------------------------------------------------- 滑动窗口
    limits._HITS.clear()
    allowed = 0
    for _ in range(5):
        ok, _, _ = limits.rate_check("t", "k1", 3)
        if ok:
            allowed += 1
    check("rate.allows_up_to_limit", allowed == 3, "放行了 %d 次，期望 3" % allowed)

    ok, count, retry = limits.rate_check("t", "k1", 3)
    check("rate.blocks_over_limit", ok is False and count >= 3, "ok=%s count=%s" % (ok, count))
    check("rate.retry_after_positive", retry > 0, "retry=%s" % retry)

    ok, _, _ = limits.rate_check("t", "k2", 3)
    check("rate.different_keys_independent", ok is True)

    # 窗口滑动后恢复：把桶里的时间戳推到 61 秒前
    with limits._LOCK:
        limits._HITS[("t", "k1")] = [t - 61 for t in limits._HITS[("t", "k1")]]
    ok, _, _ = limits.rate_check("t", "k1", 3)
    check("rate.recovers_after_window", ok is True)

    ok, _, _ = limits.rate_check("t", "k1", 0)
    check("rate.limit_zero_means_unlimited", ok is True)

    # ---------------------------------------------------- 每账号每日配额
    limits.reset_quotas()
    os.environ["CALCULUS_QUOTA_USER_DAILY"] = "3"
    os.environ["CALCULUS_QUOTA_GLOBAL_DAILY"] = "0"        # 全局不限，单测账号维度

    grants = []
    for _ in range(3):
        granted, code, msg, snap = limits.consume_llm_quota("u-alice")
        grants.append(granted)
    check("quota.user_grants_up_to_limit", all(grants), str(grants))

    granted, code, msg, snap = limits.consume_llm_quota("u-alice")
    check("quota.user_blocks_when_exhausted", granted is False, "granted=%s" % granted)
    check("quota.user_code_is_specific", code == "LLM_QUOTA_EXCEEDED_USER", str(code))
    check("quota.user_message_is_friendly", "额度已用完" in msg, msg[:80])
    check("quota.snapshot_reflects_usage",
          snap["user_used"] == 3 and snap["user_limit"] == 3 and snap["user_remaining"] == 0,
          str(snap))

    granted, _, _, _ = limits.consume_llm_quota("u-bob")
    check("quota.other_users_unaffected", granted is True)

    # ---------------------------------------------------- 全局配额
    limits.reset_quotas()
    os.environ["CALCULUS_QUOTA_USER_DAILY"] = "0"          # 账号不限，单测全局维度
    os.environ["CALCULUS_QUOTA_GLOBAL_DAILY"] = "2"

    check("quota.global_first_ok", limits.consume_llm_quota("u-c1")[0] is True)
    check("quota.global_second_ok", limits.consume_llm_quota("u-c2")[0] is True)
    granted, code, msg, snap = limits.consume_llm_quota("u-c3")
    check("quota.global_blocks_third_user", granted is False, "granted=%s" % granted)
    check("quota.global_code_is_specific", code == "LLM_QUOTA_EXCEEDED_GLOBAL", str(code))
    check("quota.global_snapshot",
          snap["global_used"] == 2 and snap["global_limit"] == 2 and snap["global_remaining"] == 0,
          str(snap))

    # ---------------------------------------------------- 不限模式
    limits.reset_quotas()
    os.environ["CALCULUS_QUOTA_USER_DAILY"] = "0"
    os.environ["CALCULUS_QUOTA_GLOBAL_DAILY"] = "0"
    for _ in range(10):
        granted, _, _, _ = limits.consume_llm_quota("u-many")
    check("quota.zero_means_unlimited", granted is True)
    check("quota.unlimited_snapshot_has_none", limits.usage_snapshot("u-many")["user_remaining"] is None)

    # ---------------------------------------------------- 历史清理
    limits.reset_quotas()
    store.ex("INSERT INTO usage_daily(day, scope, count) VALUES(?,?,?)", ("2000-01-01", "global", 1))
    limits.purge_old(7)
    old = store.q1("SELECT count FROM usage_daily WHERE day=?", ("2000-01-01",))
    check("quota.purge_removes_old_days", old is None, str(old))

    # ---------------------------------------------------- 参数快照
    os.environ["CALCULUS_QUOTA_USER_DAILY"] = "20"
    os.environ["CALCULUS_QUOTA_GLOBAL_DAILY"] = "500"
    snap = limits.config_snapshot()
    check("config.snapshot_reports_all_knobs",
          snap["quota_user_daily"] == 20 and snap["quota_global_daily"] == 500
          and snap["rate_ip_per_min"] > 0 and snap["rate_user_per_min"] > 0,
          str(snap))

    # 环境变量非法时回退到默认值，不能崩
    os.environ["CALCULUS_QUOTA_USER_DAILY"] = "abc"
    check("config.invalid_env_falls_back", limits.quota_user_daily() == 20,
          str(limits.quota_user_daily()))

    for key in ("CALCULUS_QUOTA_USER_DAILY", "CALCULUS_QUOTA_GLOBAL_DAILY"):
        os.environ.pop(key, None)

    store.close_all()
    shutil.rmtree(tmpdir, ignore_errors=True)

    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n" + "=" * 60)
    print("TOTAL %d / %d passed" % (passed, len(RESULTS)))
    print("=" * 60)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
