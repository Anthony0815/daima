#!/usr/bin/env python3
"""命令行重置账号密码 —— 忘记密码时的后门。

用法：
    python tools/reset_password.py --list
    python tools/reset_password.py admin 'NewPass123'
    python tools/reset_password.py --db path/to/app.db admin 'NewPass123'

注意：这是本机命令行工具，能改任何账号的密码，所以**不要把它暴露到网络上**。
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

import store  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, "data", "app.db")


def say(text):
    """Windows 控制台常是 GBK，emoji 与中文标点会让 print 抛异常。
    命令行工具绝不能因为「打印失败」而在做完操作之后崩掉。"""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(text.encode(enc, "replace").decode(enc, "replace"))


def main():
    ap = argparse.ArgumentParser(description="重置账号密码")
    ap.add_argument("username", nargs="?", help="要重置的账号")
    ap.add_argument("new_password", nargs="?", help="新密码（至少6位，需含字母和数字）")
    ap.add_argument("--db", default=os.environ.get("CALCULUS_DB") or DEFAULT_DB)
    ap.add_argument("--list", action="store_true", help="列出所有账号")
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        say("❌ 数据库不存在：%s" % args.db)
        say("   先启动一次服务（python server/app.py）让程序建库。")
        return 1
    store.init(args.db, seed_admin=False)

    if args.list or not args.username:
        rows = store.q("SELECT username, nickname, role, disabled, created_at FROM users ORDER BY created_at")
        if not rows:
            say("（没有任何账号）")
        else:
            say("%-20s %-16s %-8s %s" % ("账号", "昵称", "角色", "状态"))
            for r in rows:
                say("%-20s %-16s %-8s %s" % (
                    r["username"], r["nickname"] or "-", r["role"],
                    "已禁用" if r["disabled"] else "正常"))
        store.close_all()
        return 0

    if not args.new_password:
        say("❌ 请提供新密码：python tools/reset_password.py %s '新密码'" % args.username)
        store.close_all()
        return 1

    user = store.get_user_by_name(args.username)
    if not user:
        say("❌ 账号不存在：%s（用 --list 查看现有账号）" % args.username)
        store.close_all()
        return 1
    if not store.password_strong(args.new_password):
        say("❌ 密码强度不足：至少 6 位，且同时包含字母和数字")
        store.close_all()
        return 1

    store.set_password(user["user_id"], args.new_password)
    store.revoke_user_tokens(user["user_id"])
    store.log_event("password_reset_cli", {"username": user["username"]})
    store.close_all()
    say("✅ 已重置账号「%s」的密码，并吊销其全部登录令牌（各端需重新登录）。" % args.username)
    return 0


if __name__ == "__main__":
    sys.exit(main())
