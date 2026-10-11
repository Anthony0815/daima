"""知识库与题库：加载种子数据、建立倒排索引、提供检索与知识点聚合。

中文检索不引第三方分词库（保持零依赖），采用「ASCII 词 + 汉字二元组」切分，
配合 BM25 风格的 TF-IDF 打分。对微积分这种含大量公式与专名的语料效果足够。
"""

import json
import math
import os
import re

CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
ASCII_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9]*|\d+(?:\.\d+)?")

QUESTION_TYPES = ["单选题", "多选题", "判断题", "填空题"]

# 各题型单题分值（满分 = 抽题数量加权求和）
TYPE_SCORE = {"单选题": 4.0, "多选题": 5.0, "判断题": 3.0, "填空题": 4.0}

STATE = {
    "documents": [],        # 章节文档
    "doc_index": {},        # doc_id -> 文档
    "block_index": {},      # "doc_id#i" -> {doc_id, i, text, level, title}
    "postings": {},         # term -> {block_key: tf}
    "questions": [],        # 题目
    "question_index": {},   # question_id -> 题目
    "points": {},           # 知识点 -> [question_id]
}


# ---------------------------------------------------------------- 分词

def tokenize(text):
    """返回 [(term, weight)]。二元组为主，单字与英文数字词作补充。"""
    text = (text or "").lower()
    out = []
    for run in CJK_RUN.findall(text):
        for i in range(len(run)):
            if i + 1 < len(run):
                out.append((run[i:i + 2], 1.0))
            if len(run) == 1:
                out.append((run[i], 0.35))
    for w in ASCII_WORD.findall(text):
        out.append((w, 1.1))
    return out


def _index_blocks():
    postings = {}
    for key, blk in STATE["block_index"].items():
        tf = {}
        # 标题权重加倍：命中标题通常意味着主题相关
        for term, w in tokenize(blk["text"]):
            tf[term] = tf.get(term, 0.0) + w
        for term, w in tokenize(blk.get("title") or ""):
            tf[term] = tf.get(term, 0.0) + w * 2.0
        for term, count in tf.items():
            postings.setdefault(term, {})[key] = count
    STATE["postings"] = postings


# ---------------------------------------------------------------- 加载与校验

def _seed_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed")


def load(seed_dir=None):
    seed_dir = seed_dir or _seed_dir()
    with open(os.path.join(seed_dir, "knowledge.json"), "r", encoding="utf-8") as f:
        kb = json.load(f)
    with open(os.path.join(seed_dir, "questions.json"), "r", encoding="utf-8") as f:
        qb = json.load(f)

    STATE["documents"] = kb["documents"]
    STATE["doc_index"] = {d["doc_id"]: d for d in kb["documents"]}
    STATE["questions"] = qb["questions"]
    STATE["question_index"] = {x["question_id"]: x for x in qb["questions"]}

    block_index = {}
    for doc in STATE["documents"]:
        title = doc.get("title") or ""
        heading = title
        for i, blk in enumerate(doc.get("blocks", [])):
            if blk.get("level", 0) >= 1:
                heading = blk["text"]
            key = "%s#%d" % (doc["doc_id"], i)
            block_index[key] = {
                "key": key,
                "doc_id": doc["doc_id"],
                "doc_title": title,
                "index": i,
                "level": blk.get("level", 0),
                "text": blk["text"],
                "title": heading,
            }
    STATE["block_index"] = block_index
    _index_blocks()

    pts = {}
    for item in STATE["questions"]:
        for p in item.get("knowledge_points", []):
            pts.setdefault(p, []).append(item["question_id"])
    STATE["points"] = pts

    problems = validate()
    if problems:
        raise ValueError("种子数据校验失败：\n  - " + "\n  - ".join(problems))
    return summary()


def validate():
    """启动即校验，出题前就把数据错误暴露出来。"""
    problems = []
    doc_names = {d["title"] for d in STATE["documents"]}
    seen = set()
    for item in STATE["questions"]:
        qid = item.get("question_id", "<无ID>")
        if qid in seen:
            problems.append("题目 ID 重复：%s" % qid)
        seen.add(qid)
        qtype = item.get("type")
        if qtype not in QUESTION_TYPES:
            problems.append("%s 题型非法：%r" % (qid, qtype))
        if item.get("level") not in (1, 2, 3):
            problems.append("%s 难度必须是 1/2/3，实际 %r" % (qid, item.get("level")))
        if not item.get("stem"):
            problems.append("%s 缺少题干" % qid)
        if not item.get("analysis"):
            problems.append("%s 缺少解析" % qid)
        if item.get("chapter") and item["chapter"] not in doc_names:
            problems.append("%s 的 chapter 不在知识库章节中：%s" % (qid, item["chapter"]))
        opts = item.get("options") or []
        keys = [o["key"] for o in opts]
        ans = item.get("answer")
        if qtype in ("单选题", "多选题", "判断题"):
            if len(opts) < 2:
                problems.append("%s 选项少于 2 个" % qid)
            if len(set(keys)) != len(keys):
                problems.append("%s 选项 key 重复" % qid)
            if qtype == "判断题" and keys != ["A", "B"]:
                problems.append("%s 判断题选项必须是 A/B" % qid)
            if not ans or any(c not in keys for c in ans):
                problems.append("%s 答案 %r 不在选项中 %s" % (qid, ans, keys))
            if qtype == "单选题" and len(ans) != 1:
                problems.append("%s 单选题答案必须唯一，实际 %r" % (qid, ans))
            if qtype == "多选题" and len(ans) < 2:
                problems.append("%s 多选题答案至少两个，实际 %r" % (qid, ans))
            if qtype == "多选题" and list(ans) != sorted(ans):
                problems.append("%s 多选题答案需按字母升序，实际 %r" % (qid, ans))
        elif qtype == "填空题":
            if opts:
                problems.append("%s 填空题不应有选项" % qid)
            if not item.get("accepted"):
                problems.append("%s 填空题缺少 accepted 参考答案" % qid)
        if not item.get("knowledge_points"):
            problems.append("%s 缺少知识点标签" % qid)
    return problems


def summary():
    by_type = {}
    by_level = {}
    for item in STATE["questions"]:
        by_type[item["type"]] = by_type.get(item["type"], 0) + 1
        by_level[item["level"]] = by_level.get(item["level"], 0) + 1
    return {
        "documents": len(STATE["documents"]),
        "blocks": len(STATE["block_index"]),
        "questions": len(STATE["questions"]),
        "points": len(STATE["points"]),
        "terms": len(STATE["postings"]),
        "by_type": by_type,
        "by_level": {str(k): v for k, v in sorted(by_level.items())},
    }


# ---------------------------------------------------------------- 对外读取

def documents_public():
    """章节列表（含目录），对应原站的 handbook/files。"""
    out = []
    for doc in STATE["documents"]:
        sections = []
        for i, blk in enumerate(doc.get("blocks", [])):
            if blk.get("level", 0) >= 1:
                sections.append({"level": blk["level"], "title": blk["text"], "anchor": "sec-%d" % i})
        out.append({
            "doc_id": doc["doc_id"],
            "seq": doc.get("seq"),
            "name": doc.get("title"),
            "source_format": doc.get("source_format", "markdown"),
            "visibility": doc.get("visibility", "公开可读"),
            "section_count": len(sections),
            "sections": sections,
            "question_count": sum(
                1 for x in STATE["questions"] if x.get("chapter") == doc.get("title")
            ),
        })
    return out


def document_public(doc_id):
    doc = STATE["doc_index"].get(doc_id)
    if not doc:
        return None
    return {
        "doc_id": doc["doc_id"],
        "seq": doc.get("seq"),
        "name": doc.get("title"),
        "source_format": doc.get("source_format", "markdown"),
        "visibility": doc.get("visibility", "公开可读"),
        "content_blocks": [
            {"level": b.get("level", 0), "text": b["text"], "order": i}
            for i, b in enumerate(doc.get("blocks", []))
        ],
    }


def points_public():
    out = []
    for name, qids in sorted(STATE["points"].items(), key=lambda kv: (-len(kv[1]), kv[0])):
        chapters = sorted({STATE["question_index"][q]["chapter"] for q in qids})
        out.append({
            "point": name,
            "question_count": len(qids),
            "chapters": chapters,
        })
    return out


def questions_by_level_counts():
    counts = {1: 0, 2: 0, 3: 0}
    for item in STATE["questions"]:
        counts[item["level"]] = counts.get(item["level"], 0) + 1
    return [{"level": lv, "available_points": counts.get(lv, 0)} for lv in (1, 2, 3)]


def question_public(item, with_answer=False):
    out = {
        "question_id": item["question_id"],
        "type": item["type"],
        "level": item["level"],
        "chapter": item.get("chapter"),
        "knowledge_points": item.get("knowledge_points", []),
        "stem": item["stem"],
        "options": item.get("options", []),
        "score": TYPE_SCORE.get(item["type"], 0.0),
    }
    if with_answer:
        out["answer"] = item.get("answer")
        out["accepted"] = item.get("accepted", [])
        out["analysis"] = item.get("analysis")
    return out


# ---------------------------------------------------------------- 检索

def search(query, top_k=5, min_score=0.6):
    """BM25 风格检索，返回知识块。"""
    terms = tokenize(query)
    if not terms:
        return []
    N = max(1, len(STATE["block_index"]))
    scores = {}
    for term, weight in terms:
        posting = STATE["postings"].get(term)
        if not posting:
            continue
        df = len(posting)
        idf = math.log(1.0 + (N - df + 0.5) / (df + 0.5))
        for key, tf in posting.items():
            scores[key] = scores.get(key, 0.0) + weight * idf * (tf / (tf + 1.2))
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    out = []
    for key, score in ranked[:top_k * 2]:
        if score < min_score:
            break
        blk = STATE["block_index"][key]
        if blk["level"] >= 1 and len(blk["text"]) < 8:
            continue  # 纯标题行单独作为证据价值低
        out.append({
            "doc_id": blk["doc_id"],
            "doc_title": blk["doc_title"],
            "section": blk["title"],
            "text": blk["text"],
            "level": blk["level"],
            "score": round(score, 4),
            "anchor": "sec-%d" % blk["index"],
        })
        if len(out) >= top_k:
            break
    return out


def related_questions(query, top_k=3):
    """在题干与知识点里找相关题目，作为问答的补充证据。"""
    terms = tokenize(query)
    if not terms:
        return []
    N = max(1, len(STATE["questions"]))
    scores = {}
    for item in STATE["questions"]:
        hay = item["stem"] + " " + " ".join(item.get("knowledge_points", []))
        tf = {}
        for term, w in tokenize(hay):
            tf[term] = tf.get(term, 0.0) + w
        s = 0.0
        for term, weight in terms:
            if term not in tf:
                continue
            df = 0
            for other in STATE["questions"]:
                if term in (other["stem"] + " ".join(other.get("knowledge_points", []))).lower():
                    df += 1
            idf = math.log(1.0 + (N - df + 0.5) / (df + 0.5))
            s += weight * idf * (tf[term] / (tf[term] + 1.2))
        if s > 0:
            scores[item["question_id"]] = s
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]
    return [question_public(STATE["question_index"][qid]) for qid, _ in ranked]


# ---------------------------------------------------------------- 组卷抽题

def draw_questions(spec):
    """按规则抽题。

    spec: {
      "types": {"单选题": 5, "多选题": 2, ...},
      "levels": {1: 3, 2: 4, 3: 0} 或 None 表示不限,
      "chapters": ["第一章 ..."] 或 None 表示不限,
      "shuffle_seed": int
    }
    返回 (questions, error_code)。同一次组卷内题目不重复。
    """
    import random

    rng = random.Random(spec.get("shuffle_seed"))
    pool = list(STATE["questions"])
    chapters = spec.get("chapters")
    if chapters:
        pool = [x for x in pool if x.get("chapter") in chapters]
    levels = spec.get("levels")
    if levels:
        wanted = {int(k) for k, v in levels.items() if v}
        if wanted:
            pool = [x for x in pool if x["level"] in wanted]

    chosen = []
    for qtype, count in (spec.get("types") or {}).items():
        if count <= 0:
            continue
        candidates = [x for x in pool if x["type"] == qtype and x not in chosen]
        if len(candidates) < count:
            return None, ("EXAM_INSUFFICIENT_QUESTIONS", qtype, count, len(candidates))
        chosen.extend(rng.sample(candidates, count))
    rng.shuffle(chosen)
    return chosen, None
