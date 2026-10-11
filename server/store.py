"""SQLite 存储层：用户、令牌、问答会话、试卷、错题与事件日志。

设计要点：
- 每线程一条连接（ThreadingHTTPServer 下天然安全），WAL 模式提升并发读性能。
- 时间统一存 epoch 浮点秒，出口再转 ISO 字符串，避免时区歧义。
- isolation_level=None（自动提交），单条语句即一次事务，够用且不易死锁。
"""

import hashlib
import json
import os
import secrets
import sqlite3
import string
import threading
import time
import uuid

_DB_PATH = None
_CONNS = {}
_LOCK = threading.Lock()

TOKEN_TTL_SECONDS = 7 * 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id       TEXT PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  salt          TEXT NOT NULL,
  nickname      TEXT NOT NULL DEFAULT '',
  contact       TEXT NOT NULL DEFAULT '',
  role          TEXT NOT NULL DEFAULT 'user',
  disabled      INTEGER NOT NULL DEFAULT 0,
  created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
  token      TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  revoked    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS qa_sessions (
  session_id TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  title      TEXT NOT NULL DEFAULT '新的对话',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_messages (
  message_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role       TEXT NOT NULL,
  content    TEXT NOT NULL,
  citations  TEXT NOT NULL DEFAULT '[]',
  meta       TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
  paper_id         TEXT PRIMARY KEY,
  user_id          TEXT NOT NULL,
  title            TEXT NOT NULL,
  description      TEXT NOT NULL DEFAULT '',
  config           TEXT NOT NULL DEFAULT '{}',
  question_ids     TEXT NOT NULL DEFAULT '[]',
  duration_minutes INTEGER NOT NULL DEFAULT 10,
  status           TEXT NOT NULL DEFAULT '进行中',
  total_score      REAL NOT NULL DEFAULT 0,
  score            REAL,
  elapsed_seconds  INTEGER,
  drafts           TEXT NOT NULL DEFAULT '{}',
  created_at       REAL NOT NULL,
  started_at       REAL NOT NULL,
  submitted_at     REAL
);

CREATE TABLE IF NOT EXISTS paper_answers (
  paper_id    TEXT NOT NULL,
  question_id TEXT NOT NULL,
  answer      TEXT NOT NULL DEFAULT '',
  is_correct  INTEGER NOT NULL DEFAULT 0,
  score       REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (paper_id, question_id)
);

CREATE TABLE IF NOT EXISTS wrong_questions (
  user_id        TEXT NOT NULL,
  question_id    TEXT NOT NULL,
  wrong_count    INTEGER NOT NULL DEFAULT 1,
  correct_streak INTEGER NOT NULL DEFAULT 0,
  last_wrong_at  REAL NOT NULL,
  cleared        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, question_id)
);

CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_daily (
  day   TEXT NOT NULL,
  scope TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, scope)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON qa_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_papers_user ON papers(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, created_at DESC);
"""


# ---------------------------------------------------------------- 连接管理

def init(db_path, seed_admin=True):
    """建库建表。可重复调用。"""
    global _DB_PATH
    _DB_PATH = db_path
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = connect()
    with _LOCK:
        conn.executescript(SCHEMA)
    if seed_admin:
        ensure_admin()


def connect():
    tid = threading.get_ident()
    conn = _CONNS.get(tid)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH, timeout=20, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _CONNS[tid] = conn
    return conn


def q(sql, args=()):
    """查询，返回 dict 列表。"""
    return [dict(r) for r in connect().execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    """执行写操作，返回 cursor。"""
    with _LOCK:
        return connect().execute(sql, args)


def close_all():
    """关闭本进程打开的所有连接。

    Windows 上未关闭的 SQLite 连接会锁住数据库文件，导致删不掉（rmtree 静默失败）。
    服务退出与使用临时库的测试都应该调用它。
    """
    for conn in list(_CONNS.values()):
        try:
            conn.close()
        except Exception:
            pass
    _CONNS.clear()


def new_id(prefix):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:16])


def now():
    return time.time()


def iso(ts):
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))


# ---------------------------------------------------------------- 口令与令牌

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return dk.hex(), salt


def verify_password(password, password_hash, salt):
    candidate, _ = hash_password(password, salt)
    return secrets.compare_digest(candidate, password_hash)


USERNAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def username_valid(name):
    if not (3 <= len(name) <= 32):
        return False
    for ch in name:
        if ch in USERNAME_OK:
            continue
        if "\u4e00" <= ch <= "\u9fff":  # 中日韩统一表意文字
            continue
        return False
    return True


def password_strong(pw):
    if len(pw) < 6:
        return False
    has_alpha = any(c.isalpha() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    return has_alpha and has_digit


def create_user(username, password, nickname="", contact="", role="user"):
    uid = new_id("u")
    ph, salt = hash_password(password)
    ex(
        "INSERT INTO users(user_id, username, password_hash, salt, nickname, contact, role, disabled, created_at)"
        " VALUES(?,?,?,?,?,?,?,0,?)",
        (uid, username, ph, salt, nickname or username, contact, role, now()),
    )
    return get_user(uid)


def ensure_admin():
    """首次建库时创建管理员账号。

    安全默认：初始密码取自 CALCULUS_ADMIN_PASSWORD；未设置则**随机生成并打印一次**。
    绝不使用 admin/admin123 这种人人皆知的默认口令——公开部署时那等于没有密码。
    """
    row = q1("SELECT user_id FROM users WHERE username=?", ("admin",))
    if row:
        return row["user_id"]

    configured = (os.environ.get("CALCULUS_ADMIN_PASSWORD") or "").strip()
    generated = False
    if configured:
        if not password_strong(configured):
            print("[警告] CALCULUS_ADMIN_PASSWORD 强度不足（至少6位且含字母和数字），已改为随机生成。")
            configured = ""
    if configured:
        password = configured
    else:
        password = _random_password()
        generated = True

    uid = create_user("admin", password, nickname="管理员", role="admin")["user_id"]
    if generated:
        print("=" * 66)
        print("  已创建管理员账号 admin")
        print("  随机初始密码：%s" % password)
        print("  请立即记录，登录后在「账号信息 → 修改密码」里改掉。")
        print("  想指定初始密码：设置环境变量 CALCULUS_ADMIN_PASSWORD 后删库重建。")
        print("=" * 66)
    return uid


def _random_password(length=14):
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if password_strong(pw):
            return pw


def get_user(user_id):
    return q1("SELECT * FROM users WHERE user_id=?", (user_id,))


def get_user_by_name(username):
    return q1("SELECT * FROM users WHERE username=?", (username,))


def public_user(row):
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "nickname": row["nickname"],
        "contact": row["contact"],
        "role": row["role"],
        "created_at": iso(row["created_at"]),
    }


def issue_token(user_id):
    token = secrets.token_urlsafe(32)
    ts = now()
    ex(
        "INSERT INTO tokens(token, user_id, created_at, expires_at, revoked) VALUES(?,?,?,?,0)",
        (token, user_id, ts, ts + TOKEN_TTL_SECONDS),
    )
    return token


def resolve_token(token):
    """返回 user dict 或 None。"""
    if not token:
        return None
    row = q1("SELECT * FROM tokens WHERE token=?", (token,))
    if not row:
        return None
    if row["revoked"]:
        return None
    if row["expires_at"] < now():
        return None
    user = get_user(row["user_id"])
    if not user or user["disabled"]:
        return None
    return user


def revoke_token(token):
    ex("UPDATE tokens SET revoked=1 WHERE token=?", (token,))


def revoke_user_tokens(user_id):
    """吊销某用户的全部令牌（改密码后强制所有端重新登录）。"""
    ex("UPDATE tokens SET revoked=1 WHERE user_id=?", (user_id,))


def set_password(user_id, password):
    ph, salt = hash_password(password)
    ex("UPDATE users SET password_hash=?, salt=? WHERE user_id=?", (ph, salt, user_id))


# ---------------------------------------------------------------- 事件日志

def log_event(kind, payload=None):
    ex(
        "INSERT INTO events(kind, payload, created_at) VALUES(?,?,?)",
        (kind, json.dumps(payload or {}, ensure_ascii=False), now()),
    )


def recent_events(limit=20, kinds=None):
    if kinds:
        marks = ",".join("?" for _ in kinds)
        rows = q(
            "SELECT * FROM events WHERE kind IN (%s) ORDER BY id DESC LIMIT ?" % marks,
            tuple(kinds) + (limit,),
        )
    else:
        rows = q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["created_at"] = iso(r["created_at"])
        try:
            r["payload"] = json.loads(r["payload"])
        except Exception:
            r["payload"] = {}
    return rows


def count_events(kinds):
    marks = ",".join("?" for _ in kinds)
    row = q1("SELECT COUNT(*) AS c FROM events WHERE kind IN (%s)" % marks, tuple(kinds))
    return row["c"] if row else 0
