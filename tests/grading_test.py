#!/usr/bin/env python3
"""判分引擎单元测试：不依赖 HTTP，直接验证题型判分与答案归一化。

用法：python tests/grading_test.py
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import content  # noqa: E402
import exam  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print("%-52s %s" % (name, "PASS" if ok else "FAIL") + (("   " + detail) if (detail and not ok) else ""))


def main():
    content.load()

    single = {"type": "单选题", "answer": "C", "accepted": []}
    check("single.correct", exam.grade_question(single, "C") == (4.0, True, False))
    check("single.wrong", exam.grade_question(single, "A") == (0.0, False, False))
    check("single.blank", exam.grade_question(single, "") == (0.0, False, False))
    check("single.lowercase_input", exam.grade_question(single, "c") == (4.0, True, False))

    multi = {"type": "多选题", "answer": "ABD", "accepted": []}
    check("multi.exact_full", exam.grade_question(multi, "ABD") == (5.0, True, False))
    check("multi.order_insensitive", exam.grade_question(multi, "DBA") == (5.0, True, False))
    check("multi.subset_half", exam.grade_question(multi, "AB") == (2.5, False, True))
    check("multi.single_subset_half", exam.grade_question(multi, "B") == (2.5, False, True))
    check("multi.extra_choice_zero", exam.grade_question(multi, "ABC") == (0.0, False, False))
    check("multi.wrong_zero", exam.grade_question(multi, "C") == (0.0, False, False))
    check("multi.blank_zero", exam.grade_question(multi, "") == (0.0, False, False))

    judge = {"type": "判断题", "answer": "B", "accepted": []}
    check("judge.correct", exam.grade_question(judge, "B") == (3.0, True, False))
    check("judge.wrong", exam.grade_question(judge, "A") == (0.0, False, False))
    check("judge.blank_zero", exam.grade_question(judge, "") == (0.0, False, False))

    blank = {"type": "填空题", "answer": None, "accepted": ["5/3", "e^2", "6x"]}
    check("blank.exact", exam.grade_question(blank, "5/3") == (4.0, True, False))
    check("blank.spaces_ignored", exam.grade_question(blank, " 5 / 3 ") == (4.0, True, False))
    check("blank.case_insensitive", exam.grade_question(blank, "E^2") == (4.0, True, False))
    check("blank.fullwidth_digits", exam.grade_question(blank, "５/３") == (4.0, True, False))
    # 全角负号与中文标点应被归一化
    neg = {"type": "填空题", "answer": None, "accepted": ["-1/6"]}
    check("blank.fullwidth_minus", exam.grade_question(neg, "－1/6") == (4.0, True, False))
    check("blank.wrong", exam.grade_question(blank, "3/5") == (0.0, False, False))
    check("blank.empty_zero", exam.grade_question(blank, "   ") == (0.0, False, False))
    check("blank.alt_answer_accepted", exam.grade_question(blank, "6x") == (4.0, True, False))

    # 归一化函数本身
    check("norm_multi.dedup_sort", exam._norm_multi("bbaa") == "AB")
    check("norm_multi.from_list", exam._norm_multi(["b", "a"]) == "AB")
    check("norm_multi.drops_non_letters", exam._norm_multi("a1b ") == "AB")
    check("norm_text.strips_all_spaces", exam._norm_text(" 5 / 3 ") == "5/3")
    check("norm_text.fullwidth_to_halfwidth", exam._norm_text("＋") == "+")

    # 组卷规则校验
    ok_cfg, code, _ = exam.validate_config({"types": {"单选题": 5, "多选题": 3}, "duration_minutes": 15})
    check("config.valid", code is None and ok_cfg["types"]["单选题"] == 5 and ok_cfg["duration_minutes"] == 15,
          str(code))

    _, code, _ = exam.validate_config({"types": {}})
    check("config.empty_rejected", code == "EXAM_RULE_INVALID", str(code))

    _, code, _ = exam.validate_config({"types": {"单选题": 25}})
    check("config.over_max_rejected", code == "EXAM_RULE_INVALID", str(code))

    _, code, _ = exam.validate_config({"types": {"单选题": -1}})
    check("config.negative_rejected", code == "EXAM_RULE_INVALID", str(code))

    _, code, _ = exam.validate_config({"types": {"单选题": 5}, "duration_minutes": 0})
    check("config.duration_zero_rejected", code == "EXAM_RULE_INVALID", str(code))

    _, code, _ = exam.validate_config({"types": {"单选题": 5}, "duration_minutes": 999})
    check("config.duration_too_long_rejected", code == "EXAM_RULE_INVALID", str(code))

    _, code, _ = exam.validate_config({"types": {"单选题": "abc"}})
    check("config.non_numeric_rejected", code == "EXAM_RULE_INVALID", str(code))

    norm, code, _ = exam.validate_config({"types": {"单选题": 3}, "title": ""})
    check("config.default_title", code is None and norm["title"] == "微积分模拟测验", str(norm))

    # 抽题：难度与章节过滤真的生效
    qs, err = content.draw_questions({"types": {"单选题": 4}, "levels": {"1": 4, "2": 0, "3": 0},
                                      "chapters": None, "shuffle_seed": 1})
    check("draw.level_filter", err is None and len(qs) == 4 and all(q["level"] == 1 for q in qs), str(err))

    qs2, err2 = content.draw_questions({"types": {"单选题": 3}, "levels": None,
                                        "chapters": ["第四章 不定积分"], "shuffle_seed": 2})
    check("draw.chapter_filter",
          err2 is None and len(qs2) == 3 and all(q["chapter"] == "第四章 不定积分" for q in qs2), str(err2))

    qs3, err3 = content.draw_questions({"types": {"单选题": 2, "多选题": 2}, "levels": None,
                                        "chapters": None, "shuffle_seed": 3})
    check("draw.no_duplicates", err3 is None and len({q["question_id"] for q in qs3}) == 4, str(err3))

    _, err4 = content.draw_questions({"types": {"填空题": 20}, "levels": None, "chapters": None,
                                      "shuffle_seed": 4})
    check("draw.insufficient_reported", err4 is not None and err4[0] == "EXAM_INSUFFICIENT_QUESTIONS", str(err4))

    # 题库自身的答案合法性与解析完备性
    bad = []
    for item in content.STATE["questions"]:
        if item["type"] in ("单选题", "多选题", "判断题"):
            keys = [o["key"] for o in item["options"]]
            if item["type"] == "单选题" and len(item["answer"]) != 1:
                bad.append(item["question_id"])
            if item["type"] == "多选题" and len(item["answer"]) < 2:
                bad.append(item["question_id"])
            if any(c not in keys for c in item["answer"]):
                bad.append(item["question_id"])
    check("bank.answers_valid", not bad, "问题题目：" + ",".join(bad))

    dup_stems = len(content.STATE["questions"]) - len({q["stem"] for q in content.STATE["questions"]})
    check("bank.no_duplicate_stems", dup_stems == 0, "重复题干数：%d" % dup_stems)

    per_chapter = {}
    for q in content.STATE["questions"]:
        per_chapter[q["chapter"]] = per_chapter.get(q["chapter"], 0) + 1
    check("bank.chapter_coverage", len(per_chapter) == 6 and min(per_chapter.values()) >= 14,
          str(per_chapter))

    # ---------------------------------------------------- 错题本生命周期
    # 放在单元测试里做，因为在 HTTP 层组卷是随机的、无法确定地断言次数变化
    tmpdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp-grading-test")
    shutil.rmtree(tmpdir, ignore_errors=True)
    os.makedirs(tmpdir, exist_ok=True)
    import store
    store.init(os.path.join(tmpdir, "g.db"), seed_admin=False)
    uid = store.create_user("wrongbook", "pass1234")["user_id"]

    exam._update_wrong_book(uid, "q-0001", False)
    check("wrongbook.created_on_first_wrong", exam.wrong_count(uid) == 1, str(exam.wrong_count(uid)))

    exam._update_wrong_book(uid, "q-0001", False)
    row = store.q1("SELECT * FROM wrong_questions WHERE user_id=? AND question_id=?", (uid, "q-0001"))
    check("wrongbook.counts_repeated_wrongs",
          row["wrong_count"] == 2 and row["correct_streak"] == 0, str(dict(row)))

    exam._update_wrong_book(uid, "q-0001", True)
    row = store.q1("SELECT * FROM wrong_questions WHERE user_id=? AND question_id=?", (uid, "q-0001"))
    check("wrongbook.kept_after_one_correct",
          row["correct_streak"] == 1 and row["cleared"] == 0 and exam.wrong_count(uid) == 1,
          str(dict(row)))

    exam._update_wrong_book(uid, "q-0001", False)
    row = store.q1("SELECT * FROM wrong_questions WHERE user_id=? AND question_id=?", (uid, "q-0001"))
    check("wrongbook.wrong_resets_streak", row["correct_streak"] == 0, str(dict(row)))

    exam._update_wrong_book(uid, "q-0001", True)
    exam._update_wrong_book(uid, "q-0001", True)
    row = store.q1("SELECT * FROM wrong_questions WHERE user_id=? AND question_id=?", (uid, "q-0001"))
    check("wrongbook.cleared_after_two_consecutive_correct",
          row["correct_streak"] == 2 and row["cleared"] == 1 and exam.wrong_count(uid) == 0,
          str(dict(row)))

    # 答对一道从未错过的题不应往错题本里写东西
    exam._update_wrong_book(uid, "q-0002", True)
    check("wrongbook.correct_on_never_wrong_is_noop", exam.wrong_count(uid) == 0,
          str(exam.wrong_count(uid)))

    store.close_all()
    shutil.rmtree(tmpdir, ignore_errors=True)

    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n" + "=" * 60)
    print("TOTAL %d / %d passed" % (passed, len(RESULTS)))
    print("=" * 60)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
