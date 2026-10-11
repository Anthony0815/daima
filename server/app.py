#!/usr/bin/env python3
"""微积分题库与考试模拟系统 —— HTTP 服务入口。

零第三方依赖：只用 Python 标准库（http.server / sqlite3 / json / urllib）。
运行：  python server/app.py --port 8788
"""

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import content  # noqa: E402
import exam  # noqa: E402
import limits  # noqa: E402
import llm  # noqa: E402
import qa  # noqa: E402
import store  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
DEFAULT_DB = os.path.join(ROOT, "data", "app.db")

API_VERSION = "1.0 公测版"

# 公开部署的自我保护参数
MAX_BODY_BYTES = int(os.environ.get("CALCULUS_MAX_BODY") or 64 * 1024)
MAX_CONCURRENT = int(os.environ.get("CALCULUS_MAX_CONCURRENT") or 32)
_SEM = threading.BoundedSemaphore(MAX_CONCURRENT)

# 只对「花钱或耗资源」的接口套用较严的每用户频率限制，
# 普通浏览（看知识库、翻成绩）不该被限得太死。
EXPENSIVE_PATHS = (
    re.compile(r"^/api/v1/qa/sessions/[\w\-]+/ask$"),
    re.compile(r"^/api/v1/exam/papers$"),
    re.compile(r"^/api/v1/exam/wrong-questions/retry$"),
)
# 注册与登录是机器人刷号的主要入口，按 IP 分开卡（注册更紧）
REGISTER_PATH = re.compile(r"^/api/v1/auth/register$")
LOGIN_PATH = re.compile(r"^/api/v1/auth/login$")
ERROR_TEXT = {
    "AUTH_INVALID_CREDENTIAL": "账号或密码错误",
    "AUTH_REQUIRED": "请先登录",
    "AUTH_TOKEN_EXPIRED": "登录已过期，请重新登录",
    "AUTH_USERNAME_INVALID": "用户名不合法（3-32字符，仅含字母数字下划线和中文）",
    "AUTH_USERNAME_TAKEN": "用户名已被占用",
    "AUTH_PASSWORD_WEAK": "密码强度不足（至少6位且含字母和数字）",
    "AUTH_ACCOUNT_DISABLED": "账号已被禁用",
    "FORBIDDEN_NOT_ADMIN": "该功能仅管理员可用",
    "FORBIDDEN_NOT_OWNER": "无权访问他人数据",
    "SESSION_NOT_FOUND": "记录不存在",
    "SESSION_NOT_OWNED": "无权访问该记录",
    "QA_QUESTION_TOO_LONG": "问题过长，请精简后重试",
    "QA_NO_RELEVANT_CONTENT": "知识库中未找到相关内容，请尝试其他问法",
    "QA_OUT_OF_SCOPE": "该问题超出微积分题库范围",
    "LLM_UNAVAILABLE_FALLBACK": "智能回答暂时不可用，以下为知识库原文组织结果",
    "EXAM_RULE_INVALID": "组卷规则不合法，请检查题量与题型分布",
    "EXAM_INSUFFICIENT_QUESTIONS": "当前可生成题目不足，请放宽抽题范围或减少题量",
    "EXAM_TIME_UP": "考试时间已到，已自动交卷",
    "PAPER_ALREADY_SUBMITTED": "该试卷已提交，如需重考请重新组卷",
    "PAPER_NOT_IN_PROGRESS": "试卷当前不可作答",
    "SYS_INTERNAL_ERROR": "服务内部错误，请稍后重试",
    "SYS_RATE_LIMITED": "操作过于频繁，请稍后再试",
    "SYS_OVERLOADED": "当前访问人数过多，请稍后重试",
    "SYS_BODY_TOO_LARGE": "请求内容过大",
    "LLM_QUOTA_EXCEEDED_USER": "你今天的额度已用完，已改为给出手册原文依据",
    "LLM_QUOTA_EXCEEDED_GLOBAL": "本站今日额度已达上限，已自动切回本地检索模式",
    "NOT_FOUND": "接口不存在",
}


class ApiError(Exception):
    def __init__(self, code, status=400, message=None, details=None):
        super().__init__(message or ERROR_TEXT.get(code, code))
        self.code = code
        self.status = status
        self.message = message or ERROR_TEXT.get(code, code)
        self.details = details or {}


# ---------------------------------------------------------------- 处理函数
# 每个 handler 接收 ctx(dict)，返回 (status, payload)。SSE 路由返回生成器。

def h_about(ctx):
    return 200, {
        "version": API_VERSION,
        "changelog": [
            "1.0 公测版：题库 + 考试模拟 + 知识库问答",
            "关键词检索 + 模板回答，可切换大模型",
            "公开部署加固：限流、每日额度、答案防泄露",
        ],
        "license": "MIT",
        "author_zh": "DSH 构建",
        "topic": "高等数学 · 微积分",
        "llm": llm.describe(),
        "content": content.summary(),
        "limits": limits.usage_snapshot(ctx["user"]["user_id"] if ctx.get("user") else None),
    }


def h_health(ctx):
    """给 PaaS 健康检查用的轻量接口：不查库、不做鉴权。"""
    return 200, {
        "status": "ok",
        "version": API_VERSION,
        "questions": len(content.STATE["questions"]),
        "llm_mode": llm.describe()["mode"],
        "day": limits.today(),
        "limits": dict(limits.config_snapshot(), max_body=MAX_BODY_BYTES, max_concurrent=MAX_CONCURRENT),
    }


def h_usage(ctx):
    return 200, limits.usage_snapshot(ctx["user"]["user_id"] if ctx.get("user") else None)


def h_register(ctx):
    body = ctx["body"] or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not store.username_valid(username):
        raise ApiError("AUTH_USERNAME_INVALID")
    if not store.password_strong(password):
        raise ApiError("AUTH_PASSWORD_WEAK")
    if store.get_user_by_name(username):
        raise ApiError("AUTH_USERNAME_TAKEN")
    user = store.create_user(
        username, password,
        nickname=(body.get("nickname") or "").strip() or username,
        contact=(body.get("contact") or "").strip(),
    )
    token = store.issue_token(user["user_id"])
    store.log_event("user_registered", {"username": username})
    return 201, {"token": token, "user": store.public_user(user)}


def h_login(ctx):
    body = ctx["body"] or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = store.get_user_by_name(username)
    if not user or not store.verify_password(password, user["password_hash"], user["salt"]):
        raise ApiError("AUTH_INVALID_CREDENTIAL", 401)
    if user["disabled"]:
        raise ApiError("AUTH_ACCOUNT_DISABLED", 403)
    token = store.issue_token(user["user_id"])
    store.log_event("user_login", {"username": username})
    return 200, {"token": token, "user": store.public_user(user)}


def h_logout(ctx):
    store.revoke_token(ctx["token"])
    return 200, {"ok": True}


def h_profile_get(ctx):
    return 200, {"user": store.public_user(ctx["user"])}


def h_profile_put(ctx):
    body = ctx["body"] or {}
    nickname = (body.get("nickname") or "").strip()[:32]
    contact = (body.get("contact") or "").strip()[:64]
    store.ex("UPDATE users SET nickname=?, contact=? WHERE user_id=?",
             (nickname or ctx["user"]["nickname"], contact, ctx["user"]["user_id"]))
    return 200, {"user": store.public_user(store.get_user(ctx["user"]["user_id"]))}


def h_password_change(ctx):
    """修改自己的密码。改完吊销该用户全部令牌，前后端都需重新登录。"""
    body = ctx["body"] or {}
    old = body.get("old_password") or ""
    new = body.get("new_password") or ""
    user = ctx["user"]
    if not store.verify_password(old, user["password_hash"], user["salt"]):
        raise ApiError("AUTH_INVALID_CREDENTIAL", 401, "原密码不正确")
    if not store.password_strong(new):
        raise ApiError("AUTH_PASSWORD_WEAK")
    if old == new:
        raise ApiError("EXAM_RULE_INVALID", 400, "新密码不能与原密码相同")
    store.set_password(user["user_id"], new)
    store.revoke_user_tokens(user["user_id"])
    store.log_event("password_changed", {"username": user["username"]})
    return 200, {"ok": True, "relogin": True, "message": "密码已修改，请用新密码重新登录"}


def h_kb_documents(ctx):
    items = content.documents_public()
    return 200, {"items": items, "total": len(items)}


def h_kb_document(ctx):
    doc = content.document_public(ctx["params"]["doc_id"])
    if not doc:
        raise ApiError("SESSION_NOT_FOUND", 404, "章节不存在")
    return 200, doc


def h_kb_search(ctx):
    query = (ctx["query"].get("q") or [""])[0]
    if not query.strip():
        return 200, {"items": [], "enabled": True}
    hits = content.search(query, top_k=8)
    for h in hits:
        h["citation"] = "《%s》· %s" % (h["doc_title"], h["section"])
    return 200, {"items": hits, "enabled": True, "query": query}


def h_kb_points(ctx):
    return 200, {"items": content.points_public()}


def h_kb_stats(ctx):
    return 200, {
        "content": content.summary(),
        "llm": llm.describe(),
        "documents": content.documents_public(),
    }


def h_qa_create(ctx):
    return 201, qa.create_session(ctx["user"]["user_id"])


def h_qa_list(ctx):
    return 200, {"items": qa.list_sessions(ctx["user"]["user_id"])}


def h_qa_messages(ctx):
    data, err = qa.get_messages(ctx["params"]["session_id"], ctx["user"])
    if err:
        raise ApiError(err, 404)
    return 200, data


def h_qa_delete(ctx):
    err = qa.delete_session(ctx["params"]["session_id"], ctx["user"])
    if err:
        raise ApiError(err, 404)
    return 200, {"ok": True}


def h_qa_migrate(ctx):
    body = ctx["body"] or {}
    moved = qa.migrate_local(ctx["user"], body.get("sessions"))
    return 200, {"migrated": moved}


def h_qa_ask(ctx):
    body = ctx["body"] or {}
    return "sse", qa.ask(
        ctx["user"], ctx["params"]["session_id"],
        body.get("question") or "",
        bool(body.get("deep_think")),
        body.get("workflow") or "auto",
    )


def h_exam_metadata(ctx):
    return 200, exam.metadata()


def h_exam_create(ctx):
    paper, code, detail = exam.create_paper(ctx["user"], ctx["body"] or {})
    if code:
        raise ApiError(code, 400, detail)
    return 201, paper


def h_exam_get(ctx):
    paper = exam.get_paper(ctx["params"]["paper_id"], ctx["user"])
    if not paper:
        raise ApiError("SESSION_NOT_FOUND", 404, "试卷不存在")
    return 200, paper


def h_exam_drafts(ctx):
    body = ctx["body"] or {}
    data, code, detail = exam.save_drafts(ctx["params"]["paper_id"], ctx["user"], body.get("answers"))
    if code:
        raise ApiError(code, 400, detail)
    return 200, data


def h_exam_submit(ctx):
    body = ctx["body"] or {}
    data, code = exam.submit_paper(
        ctx["params"]["paper_id"], ctx["user"], body.get("answers"), bool(body.get("auto"))
    )
    if code:
        raise ApiError(code, 400)
    return 200, data


def h_exam_report(ctx):
    data = exam.report(ctx["params"]["paper_id"], ctx["user"])
    if not data:
        raise ApiError("SESSION_NOT_FOUND", 404, "试卷不存在")
    return 200, data


def h_exam_history(ctx):
    return 200, exam.history(
        ctx["user"]["user_id"],
        ctx["query"].get("page", ["1"])[0],
        ctx["query"].get("size", ["20"])[0],
    )


def h_exam_wrong(ctx):
    return 200, exam.wrong_questions(ctx["user"]["user_id"], ctx["query"].get("limit", ["50"])[0])


def h_exam_wrong_retry(ctx):
    body = ctx["body"] or {}
    paper, code = exam.retry_wrong(ctx["user"], body.get("limit") or 10)
    if code:
        raise ApiError(code, 400, "错题本是空的，先去考一次吧")
    return 201, paper


def h_exam_analysis(ctx):
    data, code = exam.question_analysis(ctx["params"]["question_id"], ctx["user"])
    if code == "SESSION_NOT_FOUND":
        raise ApiError(code, 404, "题目不存在")
    if code:
        # 防作弊：只有交过卷或错题本里有的题才给答案
        raise ApiError(code, 403, "该题不在你已交卷的试卷或错题本中，无法查看解析")
    return 200, data


def h_exam_migrate(ctx):
    body = ctx["body"] or {}
    moved = exam.migrate_local_papers(ctx["user"], body.get("papers"))
    return 200, {"migrated": moved}


def h_metrics(ctx):
    return 200, {
        "users": store.q1("SELECT COUNT(*) AS c FROM users")["c"],
        "sessions": store.q1("SELECT COUNT(*) AS c FROM qa_sessions")["c"],
        "messages": store.q1("SELECT COUNT(*) AS c FROM qa_messages")["c"],
        "papers": store.q1("SELECT COUNT(*) AS c FROM papers")["c"],
        "submitted": store.q1("SELECT COUNT(*) AS c FROM papers WHERE status='已交卷'")["c"],
        "wrong_entries": store.q1("SELECT COUNT(*) AS c FROM wrong_questions WHERE cleared=0")["c"],
        "questions": len(content.STATE["questions"]),
        "avg_ratio": _avg_ratio(),
        "llm": llm.describe(),
        "content": content.summary(),
        "events": {k: store.count_events([k]) for k in
                   ("user_registered", "user_login", "qa_asked", "paper_created", "paper_submitted",
                    "wrong_retry_created", "admin_import")},
    }


def _avg_ratio():
    rows = store.q("SELECT score, total_score FROM papers WHERE status='已交卷' AND total_score>0")
    if not rows:
        return 0.0
    vals = [(r["score"] or 0) / r["total_score"] for r in rows]
    return round(sum(vals) / len(vals), 4)


def h_audit(ctx):
    limit = int(ctx["query"].get("size", ["12"])[0])
    return 200, {"items": store.recent_events(limit=limit)}


def h_admin_stats(ctx):
    data = h_metrics(ctx)[1]
    data["admin_note"] = "题库由 server/seed/questions.json 提供，修改后重启即可生效。"
    return 200, data


# ---------------------------------------------------------------- 路由表
# (method, 正则, handler, 是否需要登录, 是否需要管理员)

ROUTES = [
    ("GET", r"/api/v1/about", h_about, False, False),
    ("GET", r"/api/v1/health", h_health, False, False),
    ("GET", r"/api/v1/usage", h_usage, False, False),

    ("POST", r"/api/v1/auth/register", h_register, False, False),
    ("POST", r"/api/v1/auth/login", h_login, False, False),
    ("POST", r"/api/v1/auth/logout", h_logout, True, False),
    ("GET", r"/api/v1/auth/profile", h_profile_get, True, False),
    ("PUT", r"/api/v1/auth/profile", h_profile_put, True, False),
    ("POST", r"/api/v1/auth/password", h_password_change, True, False),

    ("GET", r"/api/v1/kb/documents", h_kb_documents, False, False),
    ("GET", r"/api/v1/kb/documents/(?P<doc_id>[\w\-]+)", h_kb_document, False, False),
    ("GET", r"/api/v1/kb/search", h_kb_search, False, False),
    ("GET", r"/api/v1/kb/points", h_kb_points, False, False),
    ("GET", r"/api/v1/kb/stats", h_kb_stats, False, False),

    ("POST", r"/api/v1/qa/sessions", h_qa_create, True, False),
    ("GET", r"/api/v1/qa/sessions", h_qa_list, True, False),
    ("GET", r"/api/v1/qa/sessions/(?P<session_id>[\w\-]+)/messages", h_qa_messages, True, False),
    ("POST", r"/api/v1/qa/sessions/(?P<session_id>[\w\-]+)/ask", h_qa_ask, True, False),
    ("DELETE", r"/api/v1/qa/sessions/(?P<session_id>[\w\-]+)", h_qa_delete, True, False),
    ("POST", r"/api/v1/qa/local-migrate", h_qa_migrate, True, False),

    ("GET", r"/api/v1/exam/metadata", h_exam_metadata, False, False),
    ("GET", r"/api/v1/exam/papers", h_exam_history, True, False),
    ("POST", r"/api/v1/exam/papers", h_exam_create, True, False),
    ("POST", r"/api/v1/exam/papers/migrate", h_exam_migrate, True, False),
    ("GET", r"/api/v1/exam/papers/(?P<paper_id>[\w\-]+)", h_exam_get, True, False),
    ("PUT", r"/api/v1/exam/papers/(?P<paper_id>[\w\-]+)/drafts", h_exam_drafts, True, False),
    ("POST", r"/api/v1/exam/papers/(?P<paper_id>[\w\-]+)/submit", h_exam_submit, True, False),
    ("GET", r"/api/v1/exam/papers/(?P<paper_id>[\w\-]+)/report", h_exam_report, True, False),
    ("GET", r"/api/v1/exam/wrong-questions", h_exam_wrong, True, False),
    ("POST", r"/api/v1/exam/wrong-questions/retry", h_exam_wrong_retry, True, False),
    ("GET", r"/api/v1/exam/questions/(?P<question_id>[\w\-]+)/analysis", h_exam_analysis, True, False),

    ("GET", r"/api/v1/system/metrics", h_metrics, True, True),
    ("GET", r"/api/v1/system/audit-logs", h_audit, True, True),
    ("GET", r"/api/v1/admin/stats", h_admin_stats, True, True),
]

COMPILED = [(m, re.compile("^" + p + "$"), h, a, ad) for (m, p, h, a, ad) in ROUTES]


class Handler(BaseHTTPRequestHandler):
    server_version = "CalculusExam/1.0"
    protocol_version = "HTTP/1.1"

    # -------------------------------------------------- 基础工具

    def log_message(self, fmt, *args):
        if os.environ.get("CALCULUS_QUIET"):
            return
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return None
        if length > MAX_BODY_BYTES:
            # 先把请求体读掉再回应。否则客户端还在写、连接就被关，它可能根本收不到这个 413。
            self._drain(length)
            raise ApiError("SYS_BODY_TOO_LARGE", 413,
                           "请求体超过 %d 字节上限" % MAX_BODY_BYTES)
        raw = self.rfile.read(length)
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "application/json" in ctype:
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                raise ApiError("SYS_INTERNAL_ERROR", 400, "请求体不是合法 JSON")
        return raw

    def _drain(self, length, cap=4 * 1024 * 1024):
        """丢弃请求体（有上限），让客户端能把请求写完再读到我们的响应。"""
        remaining = min(length, cap)
        while remaining > 0:
            try:
                chunk = self.rfile.read(min(65536, remaining))
            except Exception:
                return
            if not chunk:
                return
            remaining -= len(chunk)

    def _client_ip(self):
        """PaaS 后面拿真实客户端 IP。

        只有当 CALCULUS_TRUST_PROXY=1 时才信任 X-Forwarded-For——
        直连场景下这个头是客户端可以随便伪造的。
        """
        if os.environ.get("CALCULUS_TRUST_PROXY", "").lower() in ("1", "true", "yes", "on"):
            xff = self.headers.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[0].strip()
            real = self.headers.get("X-Real-IP")
            if real:
                return real.strip()
        return self.client_address[0]

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error_json(self, exc):
        self._send_json(exc.status, {"code": exc.code, "message": exc.message, "details": exc.details})

    def _write_chunk(self, data):
        self.wfile.write(b"%x\r\n" % len(data))
        if data:
            self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _send_sse(self, gen):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for event, data in gen:
                frame = "event: %s\ndata: %s\n\n" % (event, json.dumps(data, ensure_ascii=False))
                self._write_chunk(frame.encode("utf-8"))
            self._write_chunk(b"")
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -------------------------------------------------- 分发

    def _dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/"):
            return self._dispatch_api(method, path, query)
        if method == "GET":
            return self._serve_static(path)
        raise ApiError("NOT_FOUND", 404)

    def _dispatch_api(self, method, path, query):
        # ---- 并发上限：超出就直接 503，不让线程被无限创建
        if not _SEM.acquire(blocking=False):
            raise ApiError("SYS_OVERLOADED", 503)

        try:
            return self._dispatch_api_inner(method, path, query)
        finally:
            _SEM.release()

    def _dispatch_api_inner(self, method, path, query):
        match = None
        path_exists = False
        for m, rx, handler, need_auth, need_admin in COMPILED:
            found = rx.match(path)
            if found:
                path_exists = True
                if m == method:
                    match = (found, handler, need_auth, need_admin)
                    break
        if not match:
            raise ApiError("NOT_FOUND", 405 if path_exists else 404,
                           "该接口不支持此方法" if path_exists else "接口不存在")

        found, handler, need_auth, need_admin = match
        token = ""
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        user = store.resolve_token(token)

        if need_auth and not user:
            raise ApiError("AUTH_REQUIRED", 401)
        if need_admin and (not user or user["role"] != "admin"):
            raise ApiError("FORBIDDEN_NOT_ADMIN", 403)

        # ---- 频率限制：先按 IP 兜底，再对花钱的接口按账号收紧
        ip = self._client_ip()
        ok, _, retry = limits.rate_check("ip", ip, limits.rate_ip_per_min())
        if not ok:
            raise ApiError("SYS_RATE_LIMITED", 429,
                           "请求过于频繁，请 %d 秒后重试" % retry)

        if REGISTER_PATH.match(path):
            ok, _, retry = limits.rate_check("register", ip, limits.rate_register_per_min())
            if not ok:
                raise ApiError("SYS_RATE_LIMITED", 429,
                               "注册过于频繁，请 %d 秒后重试" % retry)

        if LOGIN_PATH.match(path):
            ok, _, retry = limits.rate_check("login", ip, limits.rate_login_per_min())
            if not ok:
                raise ApiError("SYS_RATE_LIMITED", 429,
                               "登录尝试过于频繁，请 %d 秒后重试" % retry)

        if any(rx.match(path) for rx in EXPENSIVE_PATHS):
            key = user["user_id"] if user else "ip:" + ip
            ok, _, retry = limits.rate_check("user", key, limits.rate_user_per_min())
            if not ok:
                raise ApiError("SYS_RATE_LIMITED", 429,
                               "操作过于频繁，请 %d 秒后重试" % retry)

        ctx = {
            "method": method,
            "path": path,
            "query": query,
            "params": found.groupdict(),
            "body": self._read_body() if method in ("POST", "PUT", "PATCH") else None,
            "user": user,
            "token": token,
            "client_ip": ip,
            "headers": self.headers,
        }
        try:
            result = handler(ctx)
        except ApiError as e:
            return self._send_error_json(e)
        except Exception as e:  # 未预期异常：记日志但只回通用错误
            sys.stderr.write("[ERROR] %s %s -> %s: %s\n" % (method, path, type(e).__name__, e))
            import traceback
            traceback.print_exc(file=sys.stderr)
            return self._send_error_json(ApiError("SYS_INTERNAL_ERROR", 500))

        if isinstance(result, tuple) and result and result[0] == "sse":
            return self._send_sse(result[1])
        status, payload = result
        return self._send_json(status, payload)

    def _serve_static(self, path):
        rel = path.lstrip("/") or "index.html"
        if rel.startswith("api/"):
            raise ApiError("NOT_FOUND", 404)
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)):
            raise ApiError("NOT_FOUND", 403, "越权路径")
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            # 单页应用：未知路径回退到 index.html
            if "." not in os.path.basename(rel):
                full = os.path.join(WEB_DIR, "index.html")
            if not os.path.isfile(full):
                raise ApiError("NOT_FOUND", 404, "文件不存在")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self._guard("GET")

    def do_POST(self):
        self._guard("POST")

    def do_PUT(self):
        self._guard("PUT")

    def do_DELETE(self):
        self._guard("DELETE")

    def _guard(self, method):
        try:
            self._dispatch(method)
        except ApiError as e:
            self._send_error_json(e)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            sys.stderr.write("[FATAL] %s: %s\n" % (type(e).__name__, e))
            try:
                self._send_error_json(ApiError("SYS_INTERNAL_ERROR", 500))
            except Exception:
                pass


def load_env_file(path):
    """从 KEY=VALUE 文件加载配置（通常用来放 CALCULUS_LLM_*）。

    - 已存在的真实环境变量优先，不会被文件覆盖；
    - 只回显加载了几个变量，绝不回显取值，避免密钥进日志；
    - 用 utf-8-sig 读取：Windows 记事本 / PowerShell 的 Set-Content -Encoding utf8
      都会写入 BOM，会把第一行的键名污染成 "\ufeffCALCULUS_LLM_BASE_URL"，导致配置静默失效。
    """
    if not path or not os.path.isfile(path):
        return 0
    count = 0
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lstrip("\ufeff")
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
                count += 1
    return count


def main():
    ap = argparse.ArgumentParser(description="微积分题库与考试模拟系统")
    # PaaS（Render / Railway / Fly 等）用 PORT 注入端口，此时必须监听 0.0.0.0
    default_port = int(os.environ.get("PORT") or os.environ.get("CALCULUS_PORT") or 8788)
    default_host = os.environ.get("CALCULUS_HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    ap.add_argument("--port", type=int, default=default_port)
    ap.add_argument("--host", default=default_host)
    ap.add_argument("--db", default=os.environ.get("CALCULUS_DB") or DEFAULT_DB)
    ap.add_argument("--env-file", default=os.environ.get("CALCULUS_ENV_FILE")
                    or os.path.join(ROOT, "llm.env"),
                    help="大模型配置文件路径，默认项目根目录下的 llm.env")
    args = ap.parse_args()

    loaded = load_env_file(args.env_file)
    if loaded:
        print("已从 %s 加载 %d 项配置（不显示取值）" % (args.env_file, loaded))

    store.init(args.db)
    limits.purge_old(7)
    summary = content.load()
    print("知识库加载完成：%d 章节 / %d 知识块 / %d 道题 / %d 个知识点"
          % (summary["documents"], summary["blocks"], summary["questions"], summary["points"]))
    print("题型分布：%s" % summary["by_type"])
    print("难度分布：%s" % summary["by_level"])
    print("回答模式：%s" % llm.describe()["label"])
    print("每日额度：每账号 %s 次 / 全局 %s 次（0 表示不限）"
          % (limits.quota_user_daily() or "不限", limits.quota_global_daily() or "不限"))
    print("频率限制：每用户 %d 次/分 · 每 IP %d 次/分 · 注册 %d 次/分 · 登录 %d 次/分"
          % (limits.rate_user_per_min(), limits.rate_ip_per_min(),
             limits.rate_register_per_min(), limits.rate_login_per_min()))
    print("并发上限：%d · 请求体上限：%d 字节" % (MAX_CONCURRENT, MAX_BODY_BYTES))
    print("数据库：%s" % args.db)
    print("服务地址：http://%s:%d/" % (args.host, args.port))
    if args.host not in ("127.0.0.1", "localhost"):
        print("警告：正在对外监听。请确认管理员密码已修改，且已设置每日额度。")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
        store.close_all()


if __name__ == "__main__":
    main()
