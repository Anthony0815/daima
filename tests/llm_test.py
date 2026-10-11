#!/usr/bin/env python3
"""大模型接入测试：用本地假的 OpenAI 兼容服务验证整条通路。

不需要真实 API Key，覆盖：
- 请求格式（model / messages / system prompt / 知识库原文是否真的传进去了）
- 流式（SSE）解析与逐块输出
- 非流式回退（CALCULUS_LLM_STREAM=0）
- 各类失败（HTTP 错误、非法 JSON、空内容、中途断流）是否正确降级为模板回答
- 端到端：走完整的 qa.ask() 生成器，确认帧协议与引用不受影响
- API Key 绝不泄漏到公开接口

用法：python tests/llm_test.py
"""

import json
import os
import shutil
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

RESULTS = []
STATE = {"mode": "stream", "last_body": None, "last_headers": None}

MOCK_ANSWER = "拉格朗日中值定理要求闭区间连续、开区间可导，结论是 f(b) − f(a) = f′(ξ)(b − a)。"
FAKE_KEY = "sk-test-DO-NOT-LEAK-7f3a91"


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print("%-52s %s" % (name, "PASS" if ok else "FAIL") + (("   " + detail) if (detail and not ok) else ""))


# ---------------------------------------------------------------- 假服务

class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _sse(self, text, reasoning=None):
        lines = []
        # 思考模式的思维链走 reasoning_content，与 content 同级；正文解析必须忽略它
        if reasoning:
            for ch in reasoning:
                lines.append("data: " + json.dumps(
                    {"choices": [{"delta": {"reasoning_content": ch}}]}, ensure_ascii=False))
                lines.append("")
        for ch in text:
            lines.append("data: " + json.dumps({"choices": [{"delta": {"content": ch}}]}, ensure_ascii=False))
            lines.append("")
        lines.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}))
        lines.append("")
        lines.append("data: [DONE]")
        lines.append("")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            STATE["last_body"] = json.loads(raw.decode("utf-8"))
        except Exception:
            STATE["last_body"] = None
        STATE["last_headers"] = {k.lower(): v for k, v in self.headers.items()}

        mode = STATE["mode"]
        if mode == "http_error":
            self._send(401, "application/json",
                       json.dumps({"error": {"message": "invalid api key"}}).encode())
            return
        if mode == "bad_json":
            self._send(200, "application/json", b"<html>not json</html>")
            return
        if mode == "empty":
            self._send(200, "application/json",
                       json.dumps({"choices": [{"message": {"content": "   "}}]}).encode())
            return
        if mode == "empty_stream":
            self._send(200, "text/event-stream", b"data: [DONE]\n\n")
            return
        if mode == "mid_error":
            # 声明 chunked，只写一个 chunk 就断开，制造中途断流
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            first = ("data: " + json.dumps({"choices": [{"delta": {"content": "前半段"}}]},
                                           ensure_ascii=False) + "\n\n").encode("utf-8")
            self.wfile.write(b"%x\r\n" % len(first) + first + b"\r\n")
            self.wfile.flush()
            self.close_connection = True
            try:
                self.connection.close()
            except Exception:
                pass
            return

        if STATE["last_body"] and STATE["last_body"].get("stream"):
            body = self._sse(MOCK_ANSWER, "思考中" if mode == "reasoning" else None)
            if mode == "stream_length":
                body = body.replace(b'"finish_reason": "stop"', b'"finish_reason": "length"')
            self._send(200, "text/event-stream", body)
            return

        message = {"content": MOCK_ANSWER}
        if mode == "reasoning":
            message["reasoning_content"] = "思考中" * 20
        self._send(200, "application/json",
                   json.dumps({"choices": [{"message": message}], "model": "test-model-v1"},
                              ensure_ascii=False).encode("utf-8"))


def start_mock():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


# ---------------------------------------------------------------- 测试主体

def main():
    srv, port = start_mock()
    base = "http://127.0.0.1:%d/v1" % port

    os.environ["CALCULUS_LLM_BASE_URL"] = base
    os.environ["CALCULUS_LLM_API_KEY"] = FAKE_KEY
    os.environ["CALCULUS_LLM_MODEL"] = "test-model-v1"
    os.environ.pop("CALCULUS_LLM_STREAM", None)

    import content
    import limits
    import llm
    import qa
    import store

    content.load()

    context = [{"doc_id": "ch3", "doc_title": "第三章 微分中值定理与导数应用",
                "section": "3.2 拉格朗日中值定理", "text": "若 f(x) 在 [a, b] 上连续、在 (a, b) 内可导……",
                "score": 5.0, "anchor": "sec-5"}]

    # ---------------------------------------------- 配置识别
    p = llm.get_provider()
    check("provider.selected_when_configured", p.name == "openai_compatible", p.name)
    check("provider.label_has_model", p.label == "大模型（test-model-v1）", p.label)
    check("provider.streaming_default_on", p.streaming is True)
    check("provider.safe_base_hides_path", p.safe_base() == "http://127.0.0.1:%d" % port, p.safe_base())

    desc = llm.describe()
    check("describe.no_api_key", FAKE_KEY not in json.dumps(desc, ensure_ascii=False))
    check("describe.reports_base_and_model",
          desc.get("base") == "http://127.0.0.1:%d" % port and desc.get("model") == "test-model-v1",
          json.dumps(desc, ensure_ascii=False))

    # ---------------------------------------------- 请求格式
    STATE["mode"] = "ok"
    p = llm.OpenAICompatibleProvider()
    p.streaming = False
    text = p.compose("拉格朗日中值定理的条件是什么？", context, [], False, "kb")
    check("nonstream.returns_content", text == MOCK_ANSWER, text[:80])

    body = STATE["last_body"]
    check("request.model_field", body.get("model") == "test-model-v1", json.dumps(body)[:120])
    check("request.stream_false", body.get("stream") is False)
    check("request.has_system_prompt",
          body["messages"][0]["role"] == "system" and "微积分" in body["messages"][0]["content"])
    user_msg = body["messages"][1]["content"]
    check("request.carries_knowledge_evidence", "拉格朗日中值定理" in user_msg and "若 f(x) 在" in user_msg,
          user_msg[:120])
    check("request.carries_question", "条件是什么" in user_msg)
    check("request.temperature", body.get("temperature") == 0.2, json.dumps(body)[:160])
    check("request.omits_max_tokens_by_default", "max_tokens" not in body,
          "默认不发送 max_tokens（思考模式会先吃掉预算导致正文为空）")
    check("request.auth_header", STATE["last_headers"].get("authorization") == "Bearer " + FAKE_KEY,
          str(STATE["last_headers"].get("authorization"))[:40])

    # max_tokens 显式配置时才发送
    os.environ["CALCULUS_LLM_MAX_TOKENS"] = "4096"
    STATE["mode"] = "ok"
    llm.OpenAICompatibleProvider().compose("问题", context, [], False, "kb")
    check("request.sends_max_tokens_when_configured",
          STATE["last_body"].get("max_tokens") == 4096, json.dumps(STATE["last_body"])[:160])
    os.environ.pop("CALCULUS_LLM_MAX_TOKENS", None)

    # ---------------------------------------------- 思考模式开关
    def thinking_of(deep_think, mode=None):
        if mode is not None:
            os.environ["CALCULUS_LLM_THINKING"] = mode
        else:
            os.environ.pop("CALCULUS_LLM_THINKING", None)
        p = llm.OpenAICompatibleProvider()
        return p._thinking_value(deep_think)

    check("thinking.auto_sends_nothing", thinking_of(False, "auto") is None)
    check("thinking.off_always_disabled", thinking_of(True, "off") == "disabled")
    check("thinking.on_always_enabled", thinking_of(False, "on") == "enabled")
    check("thinking.deep_think_off_by_default", thinking_of(False, "deep_think") == "disabled")
    check("thinking.deep_think_enabled_when_toggled", thinking_of(True, "deep_think") == "enabled")
    check("thinking.invalid_value_falls_back_to_auto", thinking_of(False, "banana") is None)

    # 只有启用思考时才带 reasoning_effort
    os.environ["CALCULUS_LLM_THINKING"] = "on"
    os.environ["CALCULUS_LLM_REASONING_EFFORT"] = "low"
    STATE["mode"] = "ok"
    llm.OpenAICompatibleProvider().compose("问题", context, [], False, "kb")
    check("thinking.reasoning_effort_sent_when_enabled",
          STATE["last_body"].get("reasoning_effort") == "low", json.dumps(STATE["last_body"])[:160])
    os.environ["CALCULUS_LLM_THINKING"] = "off"
    llm.OpenAICompatibleProvider().compose("问题", context, [], False, "kb")
    check("thinking.no_reasoning_effort_when_disabled",
          "reasoning_effort" not in STATE["last_body"] and
          STATE["last_body"].get("thinking") == {"type": "disabled"},
          json.dumps(STATE["last_body"])[:160])
    os.environ.pop("CALCULUS_LLM_THINKING", None)
    os.environ.pop("CALCULUS_LLM_REASONING_EFFORT", None)

    # deep_think 应当改变指令
    STATE["mode"] = "ok"
    p = llm.OpenAICompatibleProvider()
    p.compose("问题", context, [], True, "kb")
    check("request.deep_think_changes_instruction",
          "推导" in STATE["last_body"]["messages"][1]["content"])

    # 思考内容（reasoning_content）不得混进回答正文
    STATE["mode"] = "reasoning"
    p = llm.OpenAICompatibleProvider()
    p.streaming = False
    text = p.compose("问题", context, [], False, "kb")
    check("reasoning.content_excluded_nonstream", text == MOCK_ANSWER and "思考中" not in text, text[:80])
    text = "".join(llm.OpenAICompatibleProvider().compose_stream("问题", context, [], False, "kb"))
    check("reasoning.content_excluded_stream", text == MOCK_ANSWER and "思考中" not in text, text[:80])

    # ---------------------------------------------- 流式
    STATE["mode"] = "stream"
    p = llm.OpenAICompatibleProvider()
    chunks = list(p.compose_stream("拉格朗日中值定理的条件是什么？", context, [], False, "kb"))
    check("stream.yields_multiple_chunks", len(chunks) > 3, "chunks=%d" % len(chunks))
    check("stream.concatenation_matches", "".join(chunks) == MOCK_ANSWER, "".join(chunks)[:80])
    check("stream.request_stream_true", STATE["last_body"].get("stream") is True)
    check("stream.accept_header_is_sse",
          "text/event-stream" in (STATE["last_headers"].get("accept") or ""))

    # 长度截断提示
    STATE["mode"] = "stream_length"
    p = llm.OpenAICompatibleProvider()
    truncated = "".join(p.compose_stream("问题", context, [], False, "kb"))
    check("stream.length_finish_adds_notice", "截断" in truncated and truncated.startswith(MOCK_ANSWER),
          truncated[-60:])

    # ---------------------------------------------- 失败路径
    STATE["mode"] = "http_error"
    try:
        llm.OpenAICompatibleProvider().compose("q", context, [], False, "kb")
        check("error.http_401_raises", False, "没有抛异常")
    except llm.LLMUnavailable as e:
        check("error.http_401_raises", "HTTP 401" in str(e), str(e))

    STATE["mode"] = "http_error"
    try:
        list(llm.OpenAICompatibleProvider().compose_stream("q", context, [], False, "kb"))
        check("error.stream_on_http_error_raises", False, "没有抛异常")
    except llm.LLMUnavailable:
        check("error.stream_on_http_error_raises", True)

    # 只发 [DONE]、没有任何内容 → 必须报错而不是给出空回答
    STATE["mode"] = "empty_stream"
    try:
        list(llm.OpenAICompatibleProvider().compose_stream("q", context, [], False, "kb"))
        check("error.empty_stream_raises", False, "没有抛异常")
    except llm.LLMUnavailable as e:
        check("error.empty_stream_raises", "未返回任何内容" in str(e), str(e))

    STATE["mode"] = "bad_json"
    try:
        llm.OpenAICompatibleProvider().compose("q", context, [], False, "kb")
        check("error.bad_json_raises", False, "没有抛异常")
    except llm.LLMUnavailable as e:
        check("error.bad_json_raises", "JSON" in str(e), str(e))

    STATE["mode"] = "empty"
    try:
        llm.OpenAICompatibleProvider().compose("q", context, [], False, "kb")
        check("error.empty_content_raises", False, "没有抛异常")
    except llm.LLMUnavailable as e:
        check("error.empty_content_raises", "空内容" in str(e), str(e))

    # 中途断流：http.client 会把截断的 chunked 流当作正常结束而不报错，
    # 因此实现里必须自己发现「没见过结束标记」，显式提示内容可能不完整。
    STATE["mode"] = "mid_error"
    partial = "".join(llm.OpenAICompatibleProvider().compose_stream("q", context, [], False, "kb"))
    check("error.mid_stream_keeps_partial_and_warns",
          partial.startswith("前半段") and "中断" in partial, repr(partial))

    # ---------------------------------------------- 端到端（真实 qa.ask 生成器）
    # 测试库放在项目目录内：沙箱不允许写系统临时目录
    tmpdir = os.path.join(HERE, ".tmp-llm-test")
    shutil.rmtree(tmpdir, ignore_errors=True)
    os.makedirs(tmpdir, exist_ok=True)
    store.init(os.path.join(tmpdir, "test.db"))
    user = store.create_user("llmtester", "pass1234", nickname="测试")
    sess = qa.create_session(user["user_id"])

    STATE["mode"] = "stream"
    frames = list(qa.ask(user, sess["session_id"], "拉格朗日中值定理的条件是什么？", False, "auto"))
    events = [f[0] for f in frames]
    answer = "".join(f[1]["text"] for f in frames if f[0] == "delta")
    done = [f[1] for f in frames if f[0] == "done"]
    check("e2e.stream.delta_frames", "delta" in events, str(events))
    check("e2e.stream.answer_is_model_output", answer == MOCK_ANSWER, answer[:80])
    check("e2e.stream.done_mode_is_openai",
          bool(done) and done[0]["mode"] == "openai_compatible", json.dumps(done)[:120])
    check("e2e.stream.citations_still_present",
          any(f[0] == "citation" and f[1]["items"] for f in frames))
    check("e2e.stream.has_llm_stage",
          any(f[0] == "stage" and "大模型" in f[1]["name"] for f in frames),
          str([f[1] for f in frames if f[0] == "stage"]))

    msgs, _ = qa.get_messages(sess["session_id"], user)
    check("e2e.stream.persisted_with_mode",
          msgs["items"][-1]["meta"].get("mode") == "openai_compatible",
          json.dumps(msgs["items"][-1].get("meta"), ensure_ascii=False)[:160])

    # 大模型挂掉 → 应降级为模板回答，并给出 LLM_UNAVAILABLE_FALLBACK
    STATE["mode"] = "http_error"
    frames = list(qa.ask(user, sess["session_id"], "拉格朗日中值定理的条件是什么？", False, "auto"))
    answer = "".join(f[1]["text"] for f in frames if f[0] == "delta")
    errs = [f[1] for f in frames if f[0] == "error"]
    check("e2e.fallback.error_frame",
          any(e.get("code") == "LLM_UNAVAILABLE_FALLBACK" for e in errs), json.dumps(errs, ensure_ascii=False)[:160])
    check("e2e.fallback.uses_template_content",
          "知识库原文依据" in answer and "拉格朗日中值定理" in answer, answer[:120])
    check("e2e.fallback.prefixed_with_notice", answer.startswith("> 智能回答暂时不可用"), answer[:60])

    # qa.py 的「中途中断保留部分内容」分支：直接注入一个吐一半就报错的 provider，
    # 不依赖传输层行为（http.client 对截断的 chunked 流不会抛异常）
    class FlakyProvider:
        name = "openai_compatible"
        label = "大模型（模拟）"
        streaming = True

        def compose_stream(self, *a, **k):
            yield "前半段内容"
            raise llm.LLMUnavailable("模拟连接中断")

    real_get_provider = llm.get_provider
    llm.get_provider = lambda: FlakyProvider()
    try:
        frames = list(qa.ask(user, sess["session_id"], "拉格朗日中值定理的条件是什么？", False, "auto"))
    finally:
        llm.get_provider = real_get_provider
    answer = "".join(f[1]["text"] for f in frames if f[0] == "delta")
    errs = [f[1] for f in frames if f[0] == "error"]
    check("e2e.midstream.preserves_partial", answer.startswith("前半段内容"), answer[:60])
    check("e2e.midstream.appends_interruption_note", "中断" in answer, answer[-70:])
    check("e2e.midstream.error_frame",
          any(e.get("code") == "LLM_UNAVAILABLE_FALLBACK" for e in errs),
          json.dumps(errs, ensure_ascii=False)[:160])

    # ---------------------------------------------- 额度用尽：必须真的不调用大模型
    # 这是公开部署最重要的一条：额度耗尽后如果还在打大模型，等于护栏失效、照样烧钱。
    os.environ["CALCULUS_QUOTA_USER_DAILY"] = "1"
    os.environ["CALCULUS_QUOTA_GLOBAL_DAILY"] = "0"
    limits.reset_quotas()
    STATE["mode"] = "stream"
    STATE["last_body"] = None

    frames = list(qa.ask(user, sess["session_id"], "拉格朗日中值定理的条件是什么？", False, "auto"))
    first_answer = "".join(f[1]["text"] for f in frames if f[0] == "delta")
    check("quota.within_limit_uses_llm",
          first_answer == MOCK_ANSWER and STATE["last_body"] is not None, first_answer[:60])

    STATE["last_body"] = None      # 清空探针，等下看它有没有被再次填充
    frames = list(qa.ask(user, sess["session_id"], "拉格朗日中值定理的条件是什么？", False, "auto"))
    second_answer = "".join(f[1]["text"] for f in frames if f[0] == "delta")
    errs = [f[1] for f in frames if f[0] == "error"]
    check("quota.over_limit_never_calls_llm", STATE["last_body"] is None,
          "额度已用尽却仍然请求了大模型接口")
    check("quota.over_limit_falls_back_to_template",
          "知识库原文依据" in second_answer, second_answer[:100])
    check("quota.over_limit_error_frame",
          any(e.get("code") == "LLM_QUOTA_EXCEEDED_USER" for e in errs),
          json.dumps(errs, ensure_ascii=False)[:160])
    check("quota.over_limit_done_reports_quota",
          any(f[0] == "done" and (f[1].get("quota") or {}).get("user_remaining") == 0 for f in frames),
          json.dumps([f[1] for f in frames if f[0] == "done"], ensure_ascii=False)[:160])

    for key in ("CALCULUS_QUOTA_USER_DAILY", "CALCULUS_QUOTA_GLOBAL_DAILY"):
        os.environ.pop(key, None)

    # 未配置 → 回到模板模式，且不再出现「调用大模型」阶段
    os.environ.pop("CALCULUS_LLM_BASE_URL", None)
    os.environ.pop("CALCULUS_LLM_API_KEY", None)
    check("unconfigured.falls_back_to_template", llm.get_provider().name == "keyword_template")
    frames = list(qa.ask(user, sess["session_id"], "拉格朗日中值定理的条件是什么？", False, "auto"))
    check("unconfigured.no_llm_stage",
          not any(f[0] == "stage" and "大模型" in f[1]["name"] for f in frames))
    check("unconfigured.describe_has_hint", "hint" in llm.describe())

    srv.shutdown()
    store.close_all()          # Windows 下不关连接会锁住 test.db，导致下面删不掉
    shutil.rmtree(tmpdir, ignore_errors=True)
    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n" + "=" * 60)
    print("TOTAL %d / %d passed" % (passed, len(RESULTS)))
    print("=" * 60)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
