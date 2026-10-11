#!/usr/bin/env python3
"""端到端冒烟测试：把每个 HTTP 接口都真实打一遍。

用法：
    python server/app.py --port 8788          # 先启动服务
    python tests/smoke_test.py --base http://127.0.0.1:8788

测试名用 ASCII 输出（避免 Windows 控制台编码问题），中文详情写入 tests/report.txt。
"""

import argparse
import json
import os
import random
import string
import sys
import urllib.error
import urllib.request

RESULTS = []
DETAILS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    DETAILS.append("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + detail) if detail else ""))
    line = "%-46s %s" % (name, "PASS" if ok else "FAIL")
    if detail and not ok:
        line += "   " + detail
    try:
        print(line)
    except UnicodeEncodeError:
        # Windows 控制台常是 GBK，模型输出里的 −、→、ξ 等字符编码不了，
        # 不能让「打印日志」把整个测试跑挂掉。
        enc = sys.stdout.encoding or "utf-8"
        print(line.encode(enc, "replace").decode(enc, "replace"))
    return ok


def req(base, method, path, body=None, token=None, timeout=30):
    """返回 (status, payload)。payload 是 dict，或在无法解析 JSON 时是文本。"""
    data = None
    headers = {"Accept": "application/json", "X-Request-Id": "t"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    try:
        return status, json.loads(raw.decode("utf-8"))
    except Exception:
        return status, raw.decode("utf-8", "replace")


def rand_name(prefix):
    return prefix + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))


def parse_sse(text):
    """把 SSE 响应体解析成 (event, data) 列表。"""
    frames = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except Exception:
                    data = None
        if event:
            frames.append((event, data))
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8788")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    # ---------------------------------------------------- 公开接口
    st, about = req(base, "GET", "/api/v1/about")
    check("about.public_200", st == 200 and isinstance(about, dict), str(st))
    check("about.questions_101", about.get("content", {}).get("questions") == 101,
          json.dumps(about.get("content", {}), ensure_ascii=False))
    # 本测试在两种模式下都要能跑：keyword_template（未接大模型）或 openai_compatible
    llm_mode = (about.get("llm") or {}).get("mode")
    uses_llm = llm_mode == "openai_compatible"
    check("about.llm_mode_reported", llm_mode in ("keyword_template", "openai_compatible"), str(llm_mode))
    if uses_llm:
        check("about.llm_reports_model_and_base",
              bool((about["llm"] or {}).get("model")) and bool((about["llm"] or {}).get("base")),
              json.dumps(about.get("llm"), ensure_ascii=False))
    check("about.no_api_key_leak",
          "sk-" not in json.dumps(about, ensure_ascii=False) and "api_key" not in about.get("llm", {}),
          "公开接口不得出现密钥")
    print("      当前回答模式：%s%s" % (llm_mode, "（会真实调用大模型，产生少量费用）" if uses_llm else ""))

    # ---------------------------------------------------- 公开部署相关
    st, health = req(base, "GET", "/api/v1/health")
    check("health.public_200", st == 200 and health.get("status") == "ok", str(st))
    check("health.reports_limits",
          isinstance(health.get("limits"), dict) and health["limits"].get("quota_user_daily") is not None,
          json.dumps(health.get("limits"), ensure_ascii=False))

    st, usage = req(base, "GET", "/api/v1/usage")
    check("usage.public_200", st == 200 and "global_used" in usage, json.dumps(usage, ensure_ascii=False)[:160])
    check("usage.reports_remaining", "user_remaining" in usage and "global_remaining" in usage,
          json.dumps(usage, ensure_ascii=False)[:160])

    st, docs = req(base, "GET", "/api/v1/kb/documents")
    check("kb.documents_6", st == 200 and docs.get("total") == 6, str(st))
    check("kb.documents_have_sections", all(d.get("sections") for d in docs.get("items", [])))

    doc_id = docs["items"][0]["doc_id"]
    st, doc = req(base, "GET", "/api/v1/kb/documents/%s" % doc_id)
    check("kb.document_detail", st == 200 and len(doc.get("content_blocks", [])) > 3, str(st))
    st, miss = req(base, "GET", "/api/v1/kb/documents/nope")
    check("kb.document_404", st == 404 and miss.get("code") == "SESSION_NOT_FOUND", str(st))

    st, srch = req(base, "GET", "/api/v1/kb/search?q=%E6%8B%89%E6%A0%BC%E6%9C%97%E6%97%A5")
    check("kb.search_hits_lagrange", st == 200 and len(srch.get("items", [])) > 0,
          json.dumps(srch.get("items", [])[:1], ensure_ascii=False))
    check("kb.search_ranks_relevant",
          bool(srch.get("items")) and "拉格朗日" in (
              srch["items"][0]["text"] + srch["items"][0]["section"]),
          json.dumps(srch.get("items", [])[:1], ensure_ascii=False))
    st, empty = req(base, "GET", "/api/v1/kb/search?q=")
    check("kb.search_empty_query_ok", st == 200 and empty.get("items") == [])

    st, pts = req(base, "GET", "/api/v1/kb/points")
    check("kb.points_nonempty", st == 200 and len(pts.get("items", [])) == 55,
          str(len(pts.get("items", []))))

    st, stats = req(base, "GET", "/api/v1/kb/stats")
    check("kb.stats_200", st == 200 and stats.get("content", {}).get("blocks") == 163, str(st))

    # ---------------------------------------------------- 注册与登录
    username = rand_name("stu")
    st, reg = req(base, "POST", "/api/v1/auth/register",
                  {"username": username, "password": "pass1234",
                   "nickname": "测试同学", "contact": "test@example.com"})
    check("auth.register_201", st == 201 and reg.get("token"), str(st))
    user_token = reg.get("token", "")
    user_id = reg.get("user", {}).get("user_id")

    st, dup = req(base, "POST", "/api/v1/auth/register",
                  {"username": username, "password": "pass1234"})
    check("auth.register_duplicate_rejected", st == 400 and dup.get("code") == "AUTH_USERNAME_TAKEN", str(st))

    st, weak = req(base, "POST", "/api/v1/auth/register",
                   {"username": rand_name("w"), "password": "abcdef"})
    check("auth.register_weak_password", st == 400 and weak.get("code") == "AUTH_PASSWORD_WEAK", str(st))

    st, bad = req(base, "POST", "/api/v1/auth/register",
                  {"username": "a b", "password": "pass1234"})
    check("auth.register_bad_username", st == 400 and bad.get("code") == "AUTH_USERNAME_INVALID", str(st))

    st, badlogin = req(base, "POST", "/api/v1/auth/login",
                       {"username": username, "password": "wrong9999"})
    check("auth.login_wrong_password_401", st == 401 and badlogin.get("code") == "AUTH_INVALID_CREDENTIAL", str(st))

    st, login = req(base, "POST", "/api/v1/auth/login",
                    {"username": username, "password": "pass1234"})
    check("auth.login_ok", st == 200 and login.get("token"), str(st))
    user_token = login.get("token", user_token)

    st, admin_login = req(base, "POST", "/api/v1/auth/login",
                          {"username": "admin", "password": "admin123"})
    check("auth.admin_login_ok", st == 200 and admin_login.get("user", {}).get("role") == "admin", str(st))
    admin_token = admin_login.get("token", "")

    st, noauth = req(base, "GET", "/api/v1/auth/profile")
    check("auth.profile_requires_token", st == 401 and noauth.get("code") == "AUTH_REQUIRED", str(st))

    st, prof = req(base, "GET", "/api/v1/auth/profile", token=user_token)
    check("auth.profile_200", st == 200 and prof.get("user", {}).get("user_id") == user_id, str(st))

    st, upd = req(base, "PUT", "/api/v1/auth/profile",
                  {"nickname": "改过的昵称", "contact": "new@example.com"}, token=user_token)
    check("auth.profile_update", st == 200 and upd.get("user", {}).get("nickname") == "改过的昵称", str(st))

    st, badtok = req(base, "GET", "/api/v1/auth/profile", token="not-a-real-token")
    check("auth.bad_token_401", st == 401, str(st))

    # 超大请求体必须被挡住（否则一个 Content-Length 就能吃掉内存）
    limit = (health.get("limits") or {}).get("max_body") or 65536
    st, big = req(base, "PUT", "/api/v1/auth/profile",
                  {"nickname": "x" * (limit + 5000)}, token=user_token)
    check("api.oversized_body_rejected", st == 413 and big.get("code") == "SYS_BODY_TOO_LARGE", str(st))

    # ---------------------------------------------------- 修改密码
    st, badold = req(base, "POST", "/api/v1/auth/password",
                     {"old_password": "totally-wrong", "new_password": "newpass456"}, token=user_token)
    check("auth.password_wrong_old_401",
          st == 401 and badold.get("code") == "AUTH_INVALID_CREDENTIAL", str(st))

    st, weakpw = req(base, "POST", "/api/v1/auth/password",
                     {"old_password": "pass1234", "new_password": "abcdef"}, token=user_token)
    check("auth.password_weak_rejected", st == 400 and weakpw.get("code") == "AUTH_PASSWORD_WEAK", str(st))

    st, samepw = req(base, "POST", "/api/v1/auth/password",
                     {"old_password": "pass1234", "new_password": "pass1234"}, token=user_token)
    check("auth.password_same_rejected", st == 400, str(st))

    st, changed = req(base, "POST", "/api/v1/auth/password",
                      {"old_password": "pass1234", "new_password": "newpass456"}, token=user_token)
    check("auth.password_changed", st == 200 and changed.get("relogin") is True, str(st))

    st, revoked = req(base, "GET", "/api/v1/auth/profile", token=user_token)
    check("auth.password_change_revokes_tokens", st == 401, str(st))

    st, oldlogin = req(base, "POST", "/api/v1/auth/login",
                       {"username": username, "password": "pass1234"})
    check("auth.old_password_rejected", st == 401, str(st))

    st, newlogin = req(base, "POST", "/api/v1/auth/login",
                       {"username": username, "password": "newpass456"})
    check("auth.new_password_works", st == 200 and newlogin.get("token"), str(st))
    user_token = newlogin.get("token", "")   # 后续用例改用新令牌

    # ---------------------------------------------------- 问答（含 SSE）
    st, sess = req(base, "POST", "/api/v1/qa/sessions", {}, token=user_token)
    check("qa.create_session_201", st == 201 and sess.get("session_id"), str(st))
    sid = sess.get("session_id", "")

    st, slist = req(base, "GET", "/api/v1/qa/sessions", token=user_token)
    check("qa.list_sessions", st == 200 and any(x["session_id"] == sid for x in slist.get("items", [])), str(st))

    st, raw = req(base, "POST", "/api/v1/qa/sessions/%s/ask" % sid,
                  {"question": "拉格朗日中值定理成立需要哪些条件？", "deep_think": False, "workflow": "auto"},
                  token=user_token)
    frames = parse_sse(raw) if isinstance(raw, str) else []
    events = [f[0] for f in frames]
    check("qa.sse_200", st == 200, str(st))
    check("qa.sse_has_stage", "stage" in events, str(events[:6]))
    check("qa.sse_has_citation", "citation" in events, str(events[:6]))
    check("qa.sse_has_delta", "delta" in events, str(events[:6]))
    check("qa.sse_has_done", "done" in events, str(events[:6]))
    answer_text = "".join(f[1]["text"] for f in frames if f[0] == "delta")
    check("qa.answer_is_substantive", len(answer_text) > 30, answer_text[:120])
    # 模板模式能断言确切措辞；大模型模式输出不固定，只断言确实答到了点子上
    if uses_llm:
        check("qa.answer_on_topic",
              any(k in answer_text for k in ("连续", "可导", "中值")), answer_text[:160])
        check("qa.llm_stage_present",
              any(f[0] == "stage" and "大模型" in (f[1].get("name") or "") for f in frames),
              str([f[1] for f in frames if f[0] == "stage"]))
    else:
        check("qa.answer_mentions_conditions", "连续" in answer_text and "可导" in answer_text,
              answer_text[:160])
    cits = [f[1] for f in frames if f[0] == "citation"]
    check("qa.citation_points_to_ch3",
          bool(cits) and any("第三章" in c["doc_title"] for c in cits[0]["items"]),
          json.dumps(cits[:1], ensure_ascii=False)[:200])

    st, msgs = req(base, "GET", "/api/v1/qa/sessions/%s/messages" % sid, token=user_token)
    check("qa.messages_persisted", st == 200 and len(msgs.get("items", [])) == 2,
          str(len(msgs.get("items", []))))
    check("qa.message_has_citation_meta",
          bool(msgs.get("items")) and msgs["items"][1].get("meta", {}).get("mode") == llm_mode,
          json.dumps(msgs.get("items", [{}])[-1:], ensure_ascii=False)[:200])

    st, oos = req(base, "POST", "/api/v1/qa/sessions/%s/ask" % sid,
                  {"question": "今天天气怎么样适合去海边吗", "workflow": "kb"}, token=user_token)
    oframes = parse_sse(oos) if isinstance(oos, str) else []
    oevents = [f[0] for f in oframes]
    check("qa.out_of_scope_error_frame", "error" in oevents, str(oevents[:6]))
    check("qa.out_of_scope_code",
          any(f[0] == "error" and f[1].get("code") == "QA_NO_RELEVANT_CONTENT" for f in oframes),
          json.dumps(oframes[-1:], ensure_ascii=False)[:200])

    st, chatmode = req(base, "POST", "/api/v1/qa/sessions/%s/ask" % sid,
                       {"question": "你好", "workflow": "chat"}, token=user_token)
    cframes = parse_sse(chatmode) if isinstance(chatmode, str) else []
    check("qa.chat_workflow_no_citation", "citation" not in [f[0] for f in cframes],
          str([f[0] for f in cframes]))
    check("qa.chat_workflow_done",
          any(f[0] == "done" and f[1].get("workflow") == "chat" for f in cframes))

    # 越权访问他人会话
    st, other = req(base, "POST", "/api/v1/auth/register",
                    {"username": rand_name("other"), "password": "pass1234"})
    other_token = other.get("token", "")
    st, denied = req(base, "GET", "/api/v1/qa/sessions/%s/messages" % sid, token=other_token)
    check("qa.cross_user_denied", st == 404 and denied.get("code") in ("SESSION_NOT_FOUND", "SESSION_NOT_OWNED"),
          str(st))

    # 迁移接口
    st, mig = req(base, "POST", "/api/v1/qa/local-migrate",
                  {"sessions": [{"title": "本地会话", "messages": [
                      {"role": "user", "content": "本地提问"},
                      {"role": "assistant", "content": "本地回答"}]}]}, token=user_token)
    check("qa.local_migrate", st == 200 and mig.get("migrated") == 1, str(st))
    st, migp = req(base, "POST", "/api/v1/exam/papers/migrate", {"papers": []}, token=user_token)
    check("exam.papers_migrate", st == 200 and migp.get("migrated") == 0, str(st))

    st, dele = req(base, "DELETE", "/api/v1/qa/sessions/%s" % sid, token=user_token)
    check("qa.delete_session", st == 200, str(st))
    st, gone = req(base, "GET", "/api/v1/qa/sessions/%s/messages" % sid, token=user_token)
    check("qa.deleted_session_404", st == 404, str(st))

    # ---------------------------------------------------- 考试
    st, meta = req(base, "GET", "/api/v1/exam/metadata")
    check("exam.metadata_200", st == 200 and meta.get("total_points") == 101, str(st))
    check("exam.metadata_types", meta.get("question_types") == ["单选题", "多选题", "判断题", "填空题"])
    check("exam.metadata_kb_ready", meta.get("kb_ready") is True)
    check("exam.metadata_levels",
          {x["level"]: x["available_points"] for x in meta.get("scope_levels", [])} == {1: 17, 2: 54, 3: 30},
          json.dumps(meta.get("scope_levels"), ensure_ascii=False))

    st, r0 = req(base, "POST", "/api/v1/exam/papers",
                 {"types": {"单选题": 0, "多选题": 0, "判断题": 0, "填空题": 0}}, token=user_token)
    check("exam.rule_all_zero_rejected", st == 400 and r0.get("code") == "EXAM_RULE_INVALID", str(st))

    st, r1 = req(base, "POST", "/api/v1/exam/papers",
                 {"types": {"单选题": 30}}, token=user_token)
    check("exam.rule_too_many_rejected", st == 400 and r1.get("code") == "EXAM_RULE_INVALID", str(st))

    # 填空题全库仅 12 道，要 20 道必然抽不出来
    st, r2 = req(base, "POST", "/api/v1/exam/papers",
                 {"types": {"填空题": 20}}, token=user_token)
    check("exam.rule_insufficient_pool", st == 400 and r2.get("code") == "EXAM_INSUFFICIENT_QUESTIONS",
          json.dumps(r2, ensure_ascii=False)[:160])

    st, r3 = req(base, "POST", "/api/v1/exam/papers",
                 {"types": {"单选题": 2}, "levels": {"1": 2, "2": 2, "3": 0}}, token=user_token)
    check("exam.rule_level_overflow_rejected", st == 400 and r3.get("code") == "EXAM_RULE_INVALID", str(st))

    st, r4 = req(base, "POST", "/api/v1/exam/papers",
                 {"types": {"单选题": 1}, "chapters": ["不存在的章节"]}, token=user_token)
    check("exam.rule_unknown_chapter", st == 400 and r4.get("code") == "EXAM_RULE_INVALID", str(st))

    # 正确组卷
    cfg = {"title": "冒烟测试卷", "types": {"单选题": 4, "多选题": 2, "判断题": 2, "填空题": 2},
           "levels": {"1": 2, "2": 4, "3": 2}, "duration_minutes": 10}
    st, paper = req(base, "POST", "/api/v1/exam/papers", cfg, token=user_token)
    check("exam.create_paper_201", st == 201 and len(paper.get("questions", [])) == 10, str(st))
    check("exam.paper_score_math", paper.get("total_score") == 4 * 4 + 2 * 5 + 2 * 3 + 2 * 4,
          str(paper.get("total_score")))
    check("exam.paper_hides_answers",
          all("answer" not in q for q in paper.get("questions", [])),
          json.dumps(paper.get("questions", [])[:1], ensure_ascii=False)[:200])
    pid = paper.get("paper_id", "")

    st, got = req(base, "GET", "/api/v1/exam/papers/%s" % pid, token=user_token)
    check("exam.get_paper", st == 200 and got.get("paper_id") == pid, str(st))
    check("exam.paper_has_timer", isinstance(got.get("remaining_seconds"), int) and got["remaining_seconds"] > 500,
          str(got.get("remaining_seconds")))

    st, denied2 = req(base, "GET", "/api/v1/exam/papers/%s" % pid, token=other_token)
    check("exam.cross_user_paper_denied", st == 404, str(st))

    # 防作弊：未交卷的题绝不能查答案
    probe_qid = paper["questions"][0]["question_id"]
    st, leak = req(base, "GET", "/api/v1/exam/questions/%s/analysis" % probe_qid, token=user_token)
    check("exam.analysis_blocked_before_submit", st == 403, str(st))
    st, leak2 = req(base, "GET", "/api/v1/exam/questions/%s/analysis" % probe_qid, token=other_token)
    check("exam.analysis_blocked_for_other_user", st == 403, str(st))

    # 保存草稿（先乱填几题，验证草稿确实能存进去）
    partial = {paper["questions"][0]["question_id"]: "A"}
    st, draft = req(base, "PUT", "/api/v1/exam/papers/%s/drafts" % pid, {"answers": partial}, token=user_token)
    check("exam.save_drafts", st == 200 and draft.get("saved") == 1, str(st))

    # 全部留空交卷 → 应得 0 分，并在报告里合法地拿到答案与解析
    st, sub = req(base, "POST", "/api/v1/exam/papers/%s/submit" % pid, {"answers": {}}, token=user_token)
    check("exam.submit_all_blank_zero", st == 200 and sub.get("score") == 0 and sub.get("wrong") == 10,
          json.dumps(sub, ensure_ascii=False))
    check("exam.submit_counts", sub.get("correct") == 0 and sub.get("question_count") == 10,
          json.dumps(sub, ensure_ascii=False))

    st, resub = req(base, "POST", "/api/v1/exam/papers/%s/submit" % pid, {"answers": {}}, token=user_token)
    check("exam.resubmit_rejected", st == 400 and resub.get("code") == "PAPER_ALREADY_SUBMITTED", str(st))

    st, rep = req(base, "GET", "/api/v1/exam/papers/%s/report" % pid, token=user_token)
    check("exam.report_ready", st == 200 and rep.get("ready") is True, str(st))
    check("exam.report_has_analysis",
          all(q.get("analysis") and (q.get("answer") is not None or q.get("accepted"))
              for q in rep.get("questions", [])))
    check("exam.report_chapters", len(rep.get("chapters", [])) > 0)
    check("exam.report_suggestions", isinstance(rep.get("suggestions"), list) and rep["suggestions"])

    # 交卷之后就合法了
    st, ana_ok = req(base, "GET", "/api/v1/exam/questions/%s/analysis" % probe_qid, token=user_token)
    check("exam.analysis_allowed_after_submit",
          st == 200 and (ana_ok.get("answer") is not None or ana_ok.get("accepted")), str(st))
    check("exam.analysis_related_questions",
          isinstance(ana_ok.get("related"), list) and len(ana_ok["related"]) > 0)

    # 错题本里已经合法持有答案 → 错题重做全对 → 验证判分闭环能拿满分
    st, wrong1 = req(base, "GET", "/api/v1/exam/wrong-questions", token=user_token)
    check("exam.wrong_book_has_all_ten", wrong1.get("total") == 10, str(wrong1.get("total")))
    known = {x["question_id"]: x for x in wrong1.get("items", [])}

    st, retry1 = req(base, "POST", "/api/v1/exam/wrong-questions/retry", {"limit": 10}, token=user_token)
    check("exam.retry_paper_created", st == 201 and len(retry1.get("questions", [])) == 10, str(st))
    retry_answers = {}
    for q in retry1["questions"]:
        src = known[q["question_id"]]
        retry_answers[q["question_id"]] = (src.get("accepted") or [""])[0] if q["type"] == "填空题" else src.get("answer", "")
    st, sub_full = req(base, "POST", "/api/v1/exam/papers/%s/submit" % retry1["paper_id"],
                       {"answers": retry_answers}, token=user_token)
    check("exam.retry_all_correct_full_score",
          st == 200 and sub_full.get("score") == retry1["total_score"] and sub_full.get("correct") == 10,
          json.dumps(sub_full, ensure_ascii=False) + " total=%s" % retry1.get("total_score"))

    st, rep2 = req(base, "GET", "/api/v1/exam/papers/%s/report" % retry1["paper_id"], token=user_token)
    check("exam.retry_report_grade", rep2.get("grade") == "优秀" and rep2.get("ratio") == 1.0,
          json.dumps({k: rep2.get(k) for k in ("score", "ratio", "grade")}, ensure_ascii=False))
    check("exam.report_strong_points", len(rep2.get("strong_points", [])) > 0)

    st, hist = req(base, "GET", "/api/v1/exam/papers", token=user_token)
    check("exam.history_has_paper", st == 200 and any(p["paper_id"] == pid for p in hist.get("items", [])), str(st))
    check("exam.history_stats", hist.get("stats", {}).get("best_ratio") == 1.0,
          json.dumps(hist.get("stats"), ensure_ascii=False))

    # 全错交卷 → 错题本与错题重做
    st, paper2 = req(base, "POST", "/api/v1/exam/papers",
                     {"title": "错题测试卷", "types": {"单选题": 3, "多选题": 2, "判断题": 2}},
                     token=user_token)
    pid2 = paper2.get("paper_id", "")
    st, sub2 = req(base, "POST", "/api/v1/exam/papers/%s/submit" % pid2, {"answers": {}}, token=user_token)
    check("exam.paper2_all_blank_zero", st == 200 and sub2.get("score") == 0 and sub2.get("wrong") == 7,
          json.dumps(sub2, ensure_ascii=False))

    # 错题本已有 paper1 的 10 条，paper2 会新增或累加（可能有重叠，所以只断言下界）
    st, wrong = req(base, "GET", "/api/v1/exam/wrong-questions", token=user_token)
    total_before = wrong.get("total")
    check("exam.wrong_book_filled", st == 200 and total_before >= 10,
          "错题本 %s 条（paper1 已贡献 10 条）" % total_before)
    check("exam.wrong_book_has_analysis",
          all(q.get("analysis") and (q.get("answer") is not None or q.get("accepted"))
              for q in wrong.get("items", [])))
    known2 = {x["question_id"]: x for x in wrong.get("items", [])}

    st, retry = req(base, "POST", "/api/v1/exam/wrong-questions/retry", {"limit": 5}, token=user_token)
    check("exam.wrong_retry_201", st == 201 and len(retry.get("questions", [])) == 5, str(st))
    retry_pid = retry.get("paper_id", "")

    # 错题重做全部答对：错题本里的条目只会减少或不增（连续答对两次才移出，所以最多减少 5）
    retry_answers = {}
    for q in retry["questions"]:
        src = known2[q["question_id"]]
        retry_answers[q["question_id"]] = ((src.get("accepted") or [""])[0] if q["type"] == "填空题"
                                           else src.get("answer", ""))
    st, sub_retry = req(base, "POST", "/api/v1/exam/papers/%s/submit" % retry_pid,
                        {"answers": retry_answers}, token=user_token)
    check("exam.wrong_retry_all_correct", st == 200 and sub_retry.get("score") == retry["total_score"],
          json.dumps(sub_retry, ensure_ascii=False))
    st, wrong_after = req(base, "GET", "/api/v1/exam/wrong-questions", token=user_token)
    check("exam.wrong_book_never_grows_on_correct",
          wrong_after.get("total") <= total_before,
          "答对后错题本从 %s 变成 %s（不该增长）" % (total_before, wrong_after.get("total")))

    st, ana1 = req(base, "GET", "/api/v1/exam/questions/q-0001/analysis", token=user_token)
    check("exam.question_analysis_403_for_untouched_bank", st == 403,
          "题库里从未做过的题不应能查答案，实际 %s" % st)
    st, ana404 = req(base, "GET", "/api/v1/exam/questions/nope/analysis", token=user_token)
    check("exam.question_analysis_404", st == 404, str(st))

    # 管理员可以查任意题（用于运维与出题质检）
    st, ana_admin = req(base, "GET", "/api/v1/exam/questions/q-0001/analysis", token=admin_token)
    check("exam.analysis_allowed_for_admin", st == 200 and ana_admin.get("answer") is not None, str(st))

    st, noauth_paper = req(base, "POST", "/api/v1/exam/papers", {"types": {"单选题": 1}})
    check("exam.create_requires_login", st == 401, str(st))

    # ---------------------------------------------------- 管理员
    st, m = req(base, "GET", "/api/v1/admin/stats", token=admin_token)
    check("admin.stats_200", st == 200 and m.get("users", 0) >= 3, str(st))
    check("admin.stats_has_events", m.get("events", {}).get("paper_submitted", 0) >= 3,
          json.dumps(m.get("events"), ensure_ascii=False))

    st, aud = req(base, "GET", "/api/v1/system/audit-logs?size=5", token=admin_token)
    check("admin.audit_logs", st == 200 and len(aud.get("items", [])) > 0, str(st))

    st, metrics = req(base, "GET", "/api/v1/system/metrics", token=admin_token)
    check("admin.metrics_200", st == 200 and metrics.get("questions") == 101, str(st))

    st, forbidden = req(base, "GET", "/api/v1/admin/stats", token=user_token)
    check("admin.normal_user_403", st == 403 and forbidden.get("code") == "FORBIDDEN_NOT_ADMIN", str(st))

    st, unauth = req(base, "GET", "/api/v1/system/metrics")
    check("admin.metrics_requires_login", st == 401, str(st))

    # ---------------------------------------------------- 静态资源与错误处理
    st, html = req(base, "GET", "/")
    check("static.index_served", st == 200 and "<div id=\"app\"" in html, str(st)[:80])
    st, js = req(base, "GET", "/assets/app.js")
    check("static.appjs_served", st == 200 and "streamAsk" in js, str(st)[:80])
    st, css = req(base, "GET", "/assets/styles.css")
    check("static.css_served", st == 200 and "--brand" in css, str(st)[:80])
    st, spa = req(base, "GET", "/some/deep/link")
    check("static.spa_fallback", st == 200 and "<div id=\"app\"" in spa, str(st)[:80])
    st, nf = req(base, "GET", "/api/v1/nope")
    check("api.unknown_404", st == 404 and nf.get("code") == "NOT_FOUND", str(st))
    st, wrongm = req(base, "DELETE", "/api/v1/about")
    check("api.wrong_method_405", st == 405, str(st))

    # ---------------------------------------------------- 登出
    st, out = req(base, "POST", "/api/v1/auth/logout", token=user_token)
    check("auth.logout_200", st == 200, str(st))
    st, revoked = req(base, "GET", "/api/v1/auth/profile", token=user_token)
    check("auth.token_revoked_after_logout", st == 401, str(st))

    # ---------------------------------------------------- 汇总
    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print("\n%s" % ("=" * 60))
    print("TOTAL %d / %d passed" % (passed, total))
    print("=" * 60)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("端到端冒烟测试报告：%d / %d 通过\n" % (passed, total))
        f.write("目标服务：%s\n\n" % base)
        f.write("\n".join(DETAILS))
        f.write("\n")
    print("详细报告（UTF-8）：%s" % out_path)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
