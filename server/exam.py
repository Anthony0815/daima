"""组卷、判分与成绩：规则校验、试卷生命周期、错题本维护。"""

import json

import content
import store

MAX_QUESTIONS = 20
DEFAULT_DURATION_MINUTES = 10


# ---------------------------------------------------------------- 元数据

def metadata():
    total = len(content.STATE["questions"])
    return {
        "question_types": content.QUESTION_TYPES,
        "type_scores": content.TYPE_SCORE,
        "max_questions": MAX_QUESTIONS,
        "default_duration_minutes": DEFAULT_DURATION_MINUTES,
        "scope_levels": content.questions_by_level_counts(),
        "chapters": [
            {"title": d["title"], "doc_id": d["doc_id"],
             "question_count": sum(1 for x in content.STATE["questions"] if x.get("chapter") == d["title"])}
            for d in content.STATE["documents"]
        ],
        "kb_ready": total > 0,
        "total_points": total,
    }


# ---------------------------------------------------------------- 规则校验

def validate_config(cfg):
    """返回 (normalized, error_code, detail)。"""
    types = cfg.get("types") or {}
    norm_types = {}
    for qtype in content.QUESTION_TYPES:
        raw = types.get(qtype, 0)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return None, "EXAM_RULE_INVALID", "%s 的题量必须是整数" % qtype
        if n < 0:
            return None, "EXAM_RULE_INVALID", "%s 的题量不能为负" % qtype
        if n > MAX_QUESTIONS:
            return None, "EXAM_RULE_INVALID", "%s 的题量超过上限 %d" % (qtype, MAX_QUESTIONS)
        norm_types[qtype] = n
    total = sum(norm_types.values())
    if total <= 0:
        return None, "EXAM_RULE_INVALID", "至少要选 1 道题"
    if total > MAX_QUESTIONS:
        return None, "EXAM_RULE_INVALID", "总题量 %d 超过上限 %d" % (total, MAX_QUESTIONS)

    levels = cfg.get("levels")
    norm_levels = None
    if levels:
        norm_levels = {}
        for lv in ("1", "2", "3"):
            try:
                v = int(levels.get(lv, levels.get(int(lv), 0)) or 0)
            except (TypeError, ValueError):
                return None, "EXAM_RULE_INVALID", "难度配比必须是整数"
            if v < 0:
                return None, "EXAM_RULE_INVALID", "难度配比不能为负"
            norm_levels[lv] = v
        if sum(norm_levels.values()) > total:
            return None, "EXAM_RULE_INVALID", "难度配比的题量之和超过了总题量"

    chapters = cfg.get("chapters") or None
    if chapters:
        valid = {d["title"] for d in content.STATE["documents"]}
        bad = [c for c in chapters if c not in valid]
        if bad:
            return None, "EXAM_RULE_INVALID", "未知章节：%s" % "、".join(bad)

    # 注意不能用 `cfg.get(...) or 默认值`：那样 0 会被当成「未填写」而绕过校验
    raw_duration = cfg.get("duration_minutes")
    if raw_duration is None or raw_duration == "":
        duration = DEFAULT_DURATION_MINUTES
    else:
        try:
            duration = int(raw_duration)
        except (TypeError, ValueError):
            return None, "EXAM_RULE_INVALID", "考试时长必须是整数"
    if not (1 <= duration <= 180):
        return None, "EXAM_RULE_INVALID", "考试时长需在 1~180 分钟之间"

    title = (cfg.get("title") or "").strip()[:60] or "微积分模拟测验"
    return {
        "types": norm_types,
        "levels": norm_levels,
        "chapters": chapters,
        "duration_minutes": duration,
        "title": title,
        "shuffle_seed": store.now(),
    }, None, None


# ---------------------------------------------------------------- 建卷

def create_paper(user, cfg):
    """user 必须是用户字典（get_paper 需要 role 判定归属）。"""
    spec, code, detail = validate_config(cfg)
    if code:
        return None, code, detail
    questions, err = content.draw_questions(spec)
    if err:
        code, qtype, want, got = err
        return None, code, "%s 需要 %d 道，题库仅剩 %d 道可用" % (qtype, want, got)

    paper_id = store.new_id("p")
    total_score = sum(content.TYPE_SCORE.get(x["type"], 0.0) for x in questions)
    ts = store.now()
    store.ex(
        "INSERT INTO papers(paper_id, user_id, title, description, config, question_ids,"
        " duration_minutes, status, total_score, drafts, created_at, started_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            paper_id, user["user_id"], spec["title"],
            "共 %d 题 · 满分 %g 分 · 限时 %d 分钟" % (len(questions), total_score, spec["duration_minutes"]),
            json.dumps(spec, ensure_ascii=False),
            json.dumps([x["question_id"] for x in questions]),
            spec["duration_minutes"], "进行中", total_score, "{}", ts, ts,
        ),
    )
    store.log_event("paper_created", {"paper_id": paper_id, "count": len(questions)})
    return get_paper(paper_id, user), None, None


def _row(paper_id):
    return store.q1("SELECT * FROM papers WHERE paper_id=?", (paper_id,))


def _owned(paper_id, user):
    row = _row(paper_id)
    if not row:
        return None, "SESSION_NOT_FOUND"
    if user and row["user_id"] != user["user_id"] and user["role"] != "admin":
        return None, "SESSION_NOT_OWNED"
    return row, None


def _deadline_info(row):
    """返回 (是否超时, 剩余秒数)。"""
    elapsed = store.now() - row["started_at"]
    remain = row["duration_minutes"] * 60 - elapsed
    return remain <= 0, int(remain)


def get_paper(paper_id, user):
    row, err = _owned(paper_id, user)
    if err:
        return None
    qids = json.loads(row["question_ids"])
    questions = []
    for qid in qids:
        item = content.STATE["question_index"].get(qid)
        if item:
            questions.append(content.question_public(item, with_answer=False))
    over, remain = _deadline_info(row)
    return {
        "paper_id": row["paper_id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "duration_minutes": row["duration_minutes"],
        "total_score": row["total_score"],
        "score": row["score"],
        "created_at": store.iso(row["created_at"]),
        "submitted_at": store.iso(row["submitted_at"]),
        "elapsed_seconds": row["elapsed_seconds"],
        "remaining_seconds": max(0, remain),
        "is_time_up": over,
        "drafts": json.loads(row["drafts"] or "{}"),
        "question_count": len(questions),
        "questions": questions,
        "config": json.loads(row["config"] or "{}"),
    }


def save_drafts(paper_id, user, answers):
    row, err = _owned(paper_id, user)
    if err:
        return None, err, None
    if row["status"] != "进行中":
        return None, "PAPER_NOT_IN_PROGRESS", None
    over, _ = _deadline_info(row)
    if over:
        return None, "EXAM_TIME_UP", None
    valid = set(json.loads(row["question_ids"]))
    clean = {}
    for k, v in (answers or {}).items():
        if k not in valid:
            continue
        item = content.STATE["question_index"].get(k)
        qtype = item["type"] if item else "单选题"
        if qtype == "多选题":
            clean[k] = _norm_multi(v)
        elif qtype in ("单选题", "判断题"):
            clean[k] = str(v or "").strip().upper()
        else:
            # 填空题必须原样保存：套用选项归一化会把 (1/3)sin3x+C 之类的答案彻底破坏
            clean[k] = "" if v is None else str(v)
    store.ex("UPDATE papers SET drafts=? WHERE paper_id=?",
             (json.dumps(clean, ensure_ascii=False), paper_id))
    return {"saved": len(clean)}, None, None


# ---------------------------------------------------------------- 判分

def _norm_multi(ans):
    """多选题答案统一成字母升序去重的字符串。"""
    if isinstance(ans, (list, tuple, set)):
        ans = "".join(str(x) for x in ans)
    return "".join(sorted({c for c in str(ans or "").upper() if c.isalpha()}))


def _norm_text(ans):
    """填空答案归一化：去空白、全角转半角、统一标点与负号。"""
    s = str(ans or "").strip().lower()
    out = []
    for ch in s:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif ch in " \t\u3000":
            continue
        elif ch in "。，、；：":
            out.append({"。": ".", "，": ",", "、": ",", "；": ";", "：": ":"}[ch])
        elif ch in "−–—":
            out.append("-")
        elif ch == "×":
            out.append("*")
        elif ch == "÷":
            out.append("/")
        else:
            out.append(ch)
    return "".join(out)


def grade_question(item, answer):
    """返回 (得分, 是否正确, 是否部分得分)。"""
    full = content.TYPE_SCORE.get(item["type"], 0.0)
    qtype = item["type"]
    if qtype in ("单选题", "判断题"):
        got = _norm_multi(answer) if qtype == "单选题" else str(answer or "").strip().upper()
        want = item["answer"]
        if qtype == "判断题":
            want = str(want).strip().upper()
        return (full, True, False) if got == want else (0.0, False, False)
    if qtype == "多选题":
        got = _norm_multi(answer)
        want = item["answer"]
        if got == want:
            return full, True, False
        if got and set(got) <= set(want):
            # 漏选不倒扣，给一半；错选为零分
            return round(full / 2.0, 2), False, True
        return 0.0, False, False
    # 填空题
    got = _norm_text(answer)
    if not got:
        return 0.0, False, False
    for cand in item.get("accepted", []):
        if _norm_text(cand) == got:
            return full, True, False
    return 0.0, False, False


def submit_paper(paper_id, user, answers=None, auto=False):
    row, err = _owned(paper_id, user)
    if err:
        return None, err
    if row["status"] != "进行中":
        return None, "PAPER_ALREADY_SUBMITTED"
    if answers:
        save_drafts(paper_id, user, answers)
        row = _row(paper_id)

    merged = json.loads(row["drafts"] or "{}")
    qids = json.loads(row["question_ids"])
    over, _ = _deadline_info(row)

    score = 0.0
    correct = 0
    partial = 0
    for qid in qids:
        item = content.STATE["question_index"].get(qid)
        if not item:
            continue
        got, is_correct, is_partial = grade_question(item, merged.get(qid, ""))
        score += got
        if is_correct:
            correct += 1
        elif is_partial:
            partial += 1
        store.ex(
            "INSERT INTO paper_answers(paper_id, question_id, answer, is_correct, score)"
            " VALUES(?,?,?,?,?) ON CONFLICT(paper_id, question_id)"
            " DO UPDATE SET answer=excluded.answer, is_correct=excluded.is_correct, score=excluded.score",
            (paper_id, qid, str(merged.get(qid, "")), 1 if is_correct else 0, got),
        )
        _update_wrong_book(user["user_id"], qid, is_correct)

    elapsed = int(store.now() - row["started_at"])
    store.ex(
        "UPDATE papers SET status=?, score=?, submitted_at=?, elapsed_seconds=?, drafts=? WHERE paper_id=?",
        ("已交卷", score, store.now(), elapsed, json.dumps(merged, ensure_ascii=False), paper_id),
    )
    store.log_event("paper_submitted", {
        "paper_id": paper_id, "score": score, "total": row["total_score"], "auto": bool(auto or over),
    })
    return {
        "paper_id": paper_id,
        "score": score,
        "total_score": row["total_score"],
        "correct": correct,
        "partial": partial,
        "wrong": len(qids) - correct - partial,
        "question_count": len(qids),
        "elapsed_seconds": elapsed,
        "auto_submitted": bool(auto or over),
    }, None


def _update_wrong_book(user_id, qid, is_correct):
    exists = store.q1(
        "SELECT * FROM wrong_questions WHERE user_id=? AND question_id=?", (user_id, qid)
    )
    if is_correct:
        if exists:
            streak = exists["correct_streak"] + 1
            # 连续答对两次即移出错题本（对应原站的错题重做闭环）
            cleared = 1 if streak >= 2 else 0
            store.ex(
                "UPDATE wrong_questions SET correct_streak=?, cleared=? WHERE user_id=? AND question_id=?",
                (streak, cleared, user_id, qid),
            )
        return
    if exists:
        store.ex(
            "UPDATE wrong_questions SET wrong_count=wrong_count+1, correct_streak=0, cleared=0,"
            " last_wrong_at=? WHERE user_id=? AND question_id=?",
            (store.now(), user_id, qid),
        )
    else:
        store.ex(
            "INSERT INTO wrong_questions(user_id, question_id, wrong_count, correct_streak,"
            " last_wrong_at, cleared) VALUES(?,?,1,0,?,0)",
            (user_id, qid, store.now()),
        )


# ---------------------------------------------------------------- 报告与历史

def report(paper_id, user):
    row, err = _owned(paper_id, user)
    if err:
        return None
    if row["status"] == "进行中":
        over, _ = _deadline_info(row)
        if not over:
            return {"paper_id": paper_id, "status": row["status"], "ready": False}
    qids = json.loads(row["question_ids"])
    answers = {a["question_id"]: a for a in store.q(
        "SELECT * FROM paper_answers WHERE paper_id=?", (paper_id,))}

    items = []
    by_point = {}
    by_chapter = {}
    for qid in qids:
        item = content.STATE["question_index"].get(qid)
        if not item:
            continue
        rec = answers.get(qid, {})
        entries = content.question_public(item, with_answer=True)
        entries.update({
            "your_answer": rec.get("answer", ""),
            "is_correct": bool(rec.get("is_correct")),
            "got_score": rec.get("score", 0.0),
        })
        items.append(entries)
        key = item.get("chapter") or "未归类"
        slot = by_chapter.setdefault(key, {"chapter": key, "total": 0, "correct": 0, "score": 0.0, "full": 0.0})
        slot["total"] += 1
        slot["full"] += content.TYPE_SCORE.get(item["type"], 0.0)
        slot["score"] += rec.get("score", 0.0)
        if rec.get("is_correct"):
            slot["correct"] += 1
        for p in item.get("knowledge_points", []):
            ps = by_point.setdefault(p, {"point": p, "total": 0, "correct": 0})
            ps["total"] += 1
            if rec.get("is_correct"):
                ps["correct"] += 1

    for slot in by_chapter.values():
        slot["score"] = round(slot["score"], 2)
        slot["full"] = round(slot["full"], 2)
        slot["accuracy"] = round(slot["correct"] / slot["total"], 4) if slot["total"] else 0.0
    weak = sorted(
        [v for v in by_point.values() if v["correct"] < v["total"]],
        key=lambda v: (v["correct"] / v["total"], -v["total"]),
    )[:8]
    for w in weak:
        w["accuracy"] = round(w["correct"] / w["total"], 4) if w["total"] else 0.0
    strong = sorted(
        [v for v in by_point.values() if v["correct"] == v["total"]],
        key=lambda v: -v["total"],
    )[:8]
    for s in strong:
        s["accuracy"] = 1.0

    score = row["score"] or 0.0
    total = row["total_score"] or 1.0
    ratio = score / total if total else 0.0
    return {
        "paper_id": paper_id,
        "title": row["title"],
        "status": row["status"],
        "ready": True,
        "score": round(score, 2),
        "total_score": round(total, 2),
        "ratio": round(ratio, 4),
        "passed": ratio >= 0.6,
        "grade": _grade(ratio),
        "elapsed_seconds": row["elapsed_seconds"],
        "submitted_at": store.iso(row["submitted_at"]),
        "question_count": len(items),
        "correct_count": sum(1 for x in items if x["is_correct"]),
        "chapters": sorted(by_chapter.values(), key=lambda v: v["chapter"]),
        "weak_points": weak,
        "strong_points": strong,
        "questions": items,
        "suggestions": _suggestions(weak, ratio),
    }


def _grade(ratio):
    if ratio >= 0.9:
        return "优秀"
    if ratio >= 0.8:
        return "良好"
    if ratio >= 0.7:
        return "中等"
    if ratio >= 0.6:
        return "及格"
    return "不及格"


def _suggestions(weak, ratio):
    tips = []
    if ratio < 0.6:
        tips.append("整体正确率偏低，建议先回到「知识库」把对应章节目录过一遍，再重做本套题。")
    for w in weak[:3]:
        tips.append("「%s」错了 %d 题，建议重点复习该知识点后做错题重做。" % (w["point"], w["total"] - w["correct"]))
    if not tips:
        tips.append("本次表现稳定，可挑战更高难度配比或增加题量。")
    return tips


def history(user_id, page=1, size=20):
    page = max(1, int(page or 1))
    size = max(1, min(100, int(size or 20)))
    total = store.q1("SELECT COUNT(*) AS c FROM papers WHERE user_id=?", (user_id,))["c"]
    rows = store.q(
        "SELECT * FROM papers WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (user_id, size, (page - 1) * size),
    )
    items = []
    for r in rows:
        ratio = (r["score"] or 0) / r["total_score"] if r["total_score"] else 0.0
        items.append({
            "paper_id": r["paper_id"],
            "title": r["title"],
            "status": r["status"],
            "score": round(r["score"], 2) if r["score"] is not None else None,
            "total_score": round(r["total_score"], 2),
            "ratio": round(ratio, 4),
            "grade": _grade(ratio) if r["status"] == "已交卷" else None,
            "question_count": len(json.loads(r["question_ids"])),
            "elapsed_seconds": r["elapsed_seconds"],
            "created_at": store.iso(r["created_at"]),
            "submitted_at": store.iso(r["submitted_at"]),
        })
    stats = summary_stats(user_id)
    return {"items": items, "total": total, "page": page, "size": size, "stats": stats}


def summary_stats(user_id):
    rows = store.q(
        "SELECT score, total_score FROM papers WHERE user_id=? AND status='已交卷'", (user_id,)
    )
    if not rows:
        return {"papers": 0, "avg_ratio": 0.0, "best_ratio": 0.0, "wrong_book": wrong_count(user_id)}
    ratios = [(r["score"] or 0) / r["total_score"] for r in rows if r["total_score"]]
    return {
        "papers": len(rows),
        "avg_ratio": round(sum(ratios) / len(ratios), 4) if ratios else 0.0,
        "best_ratio": round(max(ratios), 4) if ratios else 0.0,
        "wrong_book": wrong_count(user_id),
    }


def wrong_count(user_id):
    row = store.q1(
        "SELECT COUNT(*) AS c FROM wrong_questions WHERE user_id=? AND cleared=0", (user_id,)
    )
    return row["c"] if row else 0


def wrong_questions(user_id, limit=50):
    rows = store.q(
        "SELECT * FROM wrong_questions WHERE user_id=? AND cleared=0 ORDER BY last_wrong_at DESC LIMIT ?",
        (user_id, int(limit)),
    )
    items = []
    for r in rows:
        item = content.STATE["question_index"].get(r["question_id"])
        if not item:
            continue
        pub = content.question_public(item, with_answer=True)
        pub.update({
            "wrong_count": r["wrong_count"],
            "correct_streak": r["correct_streak"],
            "last_wrong_at": store.iso(r["last_wrong_at"]),
        })
        items.append(pub)
    return {"items": items, "total": len(items)}


def retry_wrong(user, limit=10):
    """用错题本里的题直接生成一份「错题重做」试卷。user 为用户字典。"""
    user_id = user["user_id"]
    rows = store.q(
        "SELECT question_id FROM wrong_questions WHERE user_id=? AND cleared=0"
        " ORDER BY wrong_count DESC, last_wrong_at DESC LIMIT ?",
        (user_id, int(limit)),
    )
    if not rows:
        return None, "EXAM_INSUFFICIENT_QUESTIONS"
    qids = [r["question_id"] for r in rows if r["question_id"] in content.STATE["question_index"]]
    if not qids:
        return None, "EXAM_INSUFFICIENT_QUESTIONS"
    paper_id = store.new_id("p")
    questions = [content.STATE["question_index"][x] for x in qids]
    total_score = sum(content.TYPE_SCORE.get(x["type"], 0.0) for x in questions)
    ts = store.now()
    spec = {"types": {}, "levels": None, "chapters": None, "duration_minutes": 10,
            "title": "错题重做", "shuffle_seed": ts, "retry_of_wrong": True}
    store.ex(
        "INSERT INTO papers(paper_id, user_id, title, description, config, question_ids,"
        " duration_minutes, status, total_score, drafts, created_at, started_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            paper_id, user_id, "错题重做（%d 题）" % len(questions),
            "来自错题本 · 满分 %g 分 · 限时 10 分钟" % total_score,
            json.dumps(spec, ensure_ascii=False), json.dumps(qids),
            10, "进行中", total_score, "{}", ts, ts,
        ),
    )
    store.log_event("wrong_retry_created", {"paper_id": paper_id, "count": len(questions)})
    return get_paper(paper_id, user), None


def may_see_answer(question_id, user):
    """是否允许该用户查看这道题的答案。

    公开部署必须做这个判断：原实现对任何登录用户都返回答案，
    等于只要知道 question_id 就能把整套题库的答案拉下来。
    现在只允许三种情况：管理员；该题出现在自己**已交卷**的试卷里；
    该题在自己的错题本里（说明确实做过并做错了）。
    """
    if not user:
        return False
    if user.get("role") == "admin":
        return True
    uid = user["user_id"]
    row = store.q1(
        "SELECT 1 AS ok FROM paper_answers pa JOIN papers p ON p.paper_id = pa.paper_id"
        " WHERE p.user_id=? AND p.status='已交卷' AND pa.question_id=? LIMIT 1",
        (uid, question_id),
    )
    if row:
        return True
    row = store.q1(
        "SELECT 1 AS ok FROM wrong_questions WHERE user_id=? AND question_id=? LIMIT 1",
        (uid, question_id),
    )
    return bool(row)


def question_analysis(question_id, user):
    item = content.STATE["question_index"].get(question_id)
    if not item:
        return None, "SESSION_NOT_FOUND"
    if not may_see_answer(question_id, user):
        return None, "FORBIDDEN_NOT_OWNER"
    pub = content.question_public(item, with_answer=True)
    pub["related"] = [
        content.question_public(content.STATE["question_index"][q], with_answer=False)
        for q in content.STATE["points"].get(item["knowledge_points"][0], [])[:4]
        if q != question_id and q in content.STATE["question_index"]
    ]
    return pub, None


def migrate_local_papers(user, papers):
    """把匿名期间本地保存的试卷搬到账号下（对应原站的会话迁移）。"""
    moved = 0
    for p in papers or []:
        pid = p.get("paper_id")
        if not pid:
            continue
        row = _row(pid)
        if row and row["user_id"] == user["user_id"]:
            continue
        try:
            store.ex(
                "INSERT INTO papers(paper_id, user_id, title, description, config, question_ids,"
                " duration_minutes, status, total_score, score, elapsed_seconds, drafts,"
                " created_at, started_at, submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, user["user_id"], p.get("title") or "迁移试卷", p.get("description") or "",
                 json.dumps(p.get("config") or {}, ensure_ascii=False),
                 json.dumps(p.get("question_ids") or []), int(p.get("duration_minutes") or 10),
                 p.get("status") or "进行中", float(p.get("total_score") or 0), p.get("score"),
                 p.get("elapsed_seconds"), json.dumps(p.get("drafts") or {}, ensure_ascii=False),
                 float(p.get("created_at") or store.now()), float(p.get("started_at") or store.now()),
                 p.get("submitted_at")),
            )
            moved += 1
        except Exception:
            continue
    return moved
