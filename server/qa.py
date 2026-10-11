"""问答：会话管理、检索、回答组织与 SSE 帧生成。

帧协议与原站保持一致：stage / citation / delta / done / error。
"""

import json
import time

import content
import limits
import llm
import store

MAX_QUESTION_LEN = 500


def create_session(user_id, title="新的对话"):
    sid = store.new_id("s")
    ts = store.now()
    store.ex(
        "INSERT INTO qa_sessions(session_id, user_id, title, created_at, updated_at) VALUES(?,?,?,?,?)",
        (sid, user_id, title, ts, ts),
    )
    return {"session_id": sid, "title": title, "created_at": store.iso(ts), "message_count": 0}


def list_sessions(user_id):
    rows = store.q(
        "SELECT s.*, (SELECT COUNT(*) FROM qa_messages m WHERE m.session_id=s.session_id) AS message_count"
        " FROM qa_sessions s WHERE s.user_id=? ORDER BY s.updated_at DESC LIMIT 100",
        (user_id,),
    )
    return [{
        "session_id": r["session_id"],
        "title": r["title"],
        "message_count": r["message_count"],
        "updated_at": store.iso(r["updated_at"]),
    } for r in rows]


def owns(session_id, user):
    row = store.q1("SELECT * FROM qa_sessions WHERE session_id=?", (session_id,))
    if not row:
        return None, "SESSION_NOT_FOUND"
    if user and row["user_id"] != user["user_id"] and user["role"] != "admin":
        return None, "SESSION_NOT_OWNED"
    return row, None


def get_messages(session_id, user):
    row, err = owns(session_id, user)
    if err:
        return None, err
    rows = store.q(
        "SELECT * FROM qa_messages WHERE session_id=? ORDER BY created_at ASC, rowid ASC",
        (session_id,),
    )
    items = []
    for r in rows:
        items.append({
            "message_id": r["message_id"],
            "role": r["role"],
            "content": r["content"],
            "citations": json.loads(r["citations"] or "[]"),
            "meta": json.loads(r["meta"] or "{}"),
            "created_at": store.iso(r["created_at"]),
        })
    return {"session_id": session_id, "title": row["title"], "items": items}, None


def _append(session_id, role, content, citations=None, meta=None):
    mid = store.new_id("m")
    store.ex(
        "INSERT INTO qa_messages(message_id, session_id, role, content, citations, meta, created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (mid, session_id, role, content,
         json.dumps(citations or [], ensure_ascii=False),
         json.dumps(meta or {}, ensure_ascii=False), store.now()),
    )
    return mid


def _retrieve(question, deep_think, workflow):
    top_k = 6 if deep_think else 4
    if workflow == "chat":
        return [], []
    contexts = content.search(question, top_k=top_k)
    related = content.related_questions(question, top_k=3)
    return contexts, related


def _citations(contexts):
    out = []
    seen = set()
    for c in contexts:
        key = (c["doc_id"], c["anchor"])
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "doc_id": c["doc_id"],
            "doc_title": c["doc_title"],
            "section": c["section"],
            "anchor": c["anchor"],
            "excerpt": c["text"][:120] + ("…" if len(c["text"]) > 120 else ""),
            "score": c["score"],
        })
    return out


def ask(user, session_id, question, deep_think=False, workflow="auto"):
    """生成器：产出 (event, data) 二元组。"""
    question = (question or "").strip()
    if not question:
        yield ("error", {"code": "QA_QUESTION_TOO_LONG", "message": "问题不能为空"})
        return
    if len(question) > MAX_QUESTION_LEN:
        yield ("error", {"code": "QA_QUESTION_TOO_LONG",
                         "message": "问题过长（上限 %d 字），请精简后重试" % MAX_QUESTION_LEN})
        return
    row, err = owns(session_id, user)
    if err:
        yield ("error", {"code": err, "message": "会话不可用"})
        return

    if workflow not in ("auto", "kb", "chat"):
        workflow = "auto"

    yield ("stage", {"name": "理解问题", "detail": "解析关键词与知识点"})
    time.sleep(0.05)
    contexts, related = _retrieve(question, deep_think, workflow)

    effective = workflow
    if workflow == "auto":
        effective = "kb" if contexts else "chat"

    if effective == "kb" and not contexts:
        _append(session_id, "user", question)
        msg = ("知识库中没有检索到相关内容。本题库覆盖：函数与极限、导数与微分、"
               "微分中值定理与导数应用、不定积分、定积分及其应用、多元函数微分学。"
               "请尝试换用课本里的术语提问。")
        _append(session_id, "assistant", msg, [], {"mode": "no_content", "workflow": effective})
        yield ("error", {"code": "QA_NO_RELEVANT_CONTENT", "message": msg})
        return

    yield ("stage", {"name": "检索知识库",
                     "detail": "命中 %d 个知识块、%d 道相关题" % (len(contexts), len(related))})
    if contexts:
        yield ("citation", {"items": _citations(contexts)})

    yield ("stage", {"name": "组织回答", "detail": "按原文依据生成结构化回答"})

    provider = llm.get_provider()
    fallback_reason = None
    parts = []
    quota = limits.usage_snapshot(user["user_id"])

    def chunked(text, size=26):
        for i in range(0, len(text), size):
            yield text[i:i + size]

    if provider.name == "keyword_template":
        whole = provider.compose(question, contexts, related, deep_think, effective)
        parts.append(whole)
        for piece in chunked(whole):
            yield ("delta", {"text": piece})
            time.sleep(0.012)
    else:
        # 先扣配额再调用。额度用完时**不报错**，直接降级成本地模板回答——
        # 既保护了运营者的钱包，用户也仍然拿得到知识库里的原文依据。
        allowed, qcode, qmsg, quota = limits.consume_llm_quota(user["user_id"])
        if not allowed:
            fallback_reason = qmsg
            template_text = llm.TemplateProvider().compose(
                question, contexts, related, deep_think, effective)
            whole = "> %s\n\n%s" % (qmsg, template_text)
            parts.append(whole)
            yield ("error", {"code": qcode, "message": qmsg, "quota": quota})
            for piece in chunked(whole):
                yield ("delta", {"text": piece})
                time.sleep(0.012)
        else:
            yield ("stage", {"name": "调用大模型",
                             "detail": provider.label + ("（流式）" if provider.streaming else "")})
            try:
                if provider.streaming:
                    for piece in provider.compose_stream(question, contexts, related, deep_think, effective):
                        parts.append(piece)
                        yield ("delta", {"text": piece})
                else:
                    whole = provider.compose(question, contexts, related, deep_think, effective)
                    parts.append(whole)
                    for piece in chunked(whole):
                        yield ("delta", {"text": piece})
                        time.sleep(0.012)
            except llm.LLMUnavailable as e:
                fallback_reason = str(e)
                if parts:
                    # 已经吐出一部分内容，保留它并如实说明中断，而不是丢弃后重来
                    note = "\n\n> 大模型连接中断（%s），以上为已生成的部分内容。" % fallback_reason
                    parts.append(note)
                    yield ("delta", {"text": note})
                    yield ("error", {"code": "LLM_UNAVAILABLE_FALLBACK", "message": fallback_reason})
                else:
                    template_text = llm.TemplateProvider().compose(
                        question, contexts, related, deep_think, effective)
                    whole = ("> 智能回答暂时不可用，以下为知识库原文组织结果。（%s）\n\n%s"
                             % (fallback_reason, template_text))
                    parts.append(whole)
                    yield ("error", {"code": "LLM_UNAVAILABLE_FALLBACK", "message": fallback_reason})
                    for piece in chunked(whole):
                        yield ("delta", {"text": piece})
                        time.sleep(0.012)
            except Exception as e:
                yield ("error", {"code": "SYS_INTERNAL_ERROR",
                                 "message": "生成回答失败：%s" % type(e).__name__})
                return

    text = "".join(parts)
    if not text.strip():
        yield ("error", {"code": "SYS_INTERNAL_ERROR", "message": "生成内容为空"})
        return

    user_msg_id = _append(session_id, "user", question)
    meta = {
        "mode": provider.name,
        "mode_label": getattr(provider, "label", provider.name),
        "workflow": effective,
        "deep_think": bool(deep_think),
        "fallback_reason": fallback_reason,
        "contexts": len(contexts),
        "quota": quota,
    }
    msg_id = _append(session_id, "assistant", text, _citations(contexts), meta)
    store.ex("UPDATE qa_sessions SET updated_at=? WHERE session_id=?", (store.now(), session_id))
    _maybe_retitle(session_id, row["title"], question)
    store.log_event("qa_asked", {"session_id": session_id, "mode": provider.name, "deep_think": bool(deep_think)})

    yield ("done", {
        "message_id": msg_id,
        "user_message_id": user_msg_id,
        "session_id": session_id,
        "mode": provider.name,
        "mode_label": meta["mode_label"],
        "workflow": effective,
        "chars": len(text),
        "fallback_reason": fallback_reason,
        "quota": quota,
    })


def _maybe_retitle(session_id, current, question):
    if current and current != "新的对话":
        return
    title = question[:24] + ("…" if len(question) > 24 else "")
    store.ex("UPDATE qa_sessions SET title=? WHERE session_id=?", (title, session_id))


def delete_session(session_id, user):
    row, err = owns(session_id, user)
    if err:
        return err
    store.ex("DELETE FROM qa_messages WHERE session_id=?", (session_id,))
    store.ex("DELETE FROM qa_sessions WHERE session_id=?", (session_id,))
    return None


def migrate_local(user, sessions):
    """匿名期间本地会话导入账号。"""
    moved = 0
    for s in sessions or []:
        title = (s.get("title") or "迁移对话")[:60]
        new = create_session(user["user_id"], title)
        for m in s.get("messages") or []:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                _append(new["session_id"], m["role"], m["content"],
                        m.get("citations") or [], {"migrated": True})
        moved += 1
    return moved
