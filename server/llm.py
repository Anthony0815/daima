"""可插拔大模型层。

两种实现：

1. `TemplateProvider`（默认）
   纯本地。把 BM25 检索到的知识库原文组织成结构化回答，不联网、零费用。

2. `OpenAICompatibleProvider`
   任何兼容 `/chat/completions` 的服务：DeepSeek、OpenAI、通义千问、Moonshot、
   智谱、硅基流动、本地 Ollama / vLLM / LM Studio 等。配置环境变量后自动启用。

环境变量：

    CALCULUS_LLM_BASE_URL    必填，例如 https://api.deepseek.com/v1
    CALCULUS_LLM_API_KEY     必填，只从环境变量读取，不写进代码与数据库
    CALCULUS_LLM_MODEL       默认 deepseek-chat
    CALCULUS_LLM_STREAM      默认 1；设为 0 则改用一次性返回
    CALCULUS_LLM_TIMEOUT     默认 60 秒
    CALCULUS_LLM_TEMPERATURE 默认 0.2
    CALCULUS_LLM_MAX_TOKENS  默认 1024

任何调用失败都会抛出 `LLMUnavailable`，由 qa.py 降级回模板回答，不会把异常抛给用户。
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

SYSTEM_PROMPT = (
    "你是《微积分》课程的助教。只依据提供的知识库原文回答问题，"
    "不要编造定理或结论；涉及计算时给出关键步骤；"
    "无法从原文回答时明确说明「知识库中未涉及」。\n"
    "排版要求（很重要）：使用纯文本数学记号，与知识库原文保持一致——\n"
    "  · 极限写 lim(x→0)，定积分写 ∫(0→1)，根号写 √，幂用 ^（如 e^(2x)），下标用 _（如 f_x）；\n"
    "  · 分数写成 (a)/(b) 或 a/b，不要用 \\frac；\n"
    "  · 不要使用任何 LaTeX 记号（不要出现 \\( \\)、\\[ \\]、\\frac、\\lim、\\int、\\displaystyle 等），"
    "界面对 LaTeX 不做渲染，会原样显示成乱码。"
)


class LLMUnavailable(Exception):
    """大模型不可用或调用失败，调用方应降级到模板回答。"""


def _env(name, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name, default=True):
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() not in ("0", "false", "no", "off")


# ------------------------------------------------------------------ 模板实现

class TemplateProvider:
    """关键词检索 + 模板回答。"""

    name = "keyword_template"
    label = "关键词检索 + 模板回答"
    streaming = False

    def available(self):
        return True

    def compose(self, question, contexts, related, deep_think=False, workflow="kb"):
        if workflow == "chat":
            return self._chat(question)
        if not contexts:
            return (
                "知识库中没有检索到与「%s」直接相关的内容。\n\n"
                "建议换一种问法，或从「知识库」按章节目录定位；"
                "也可以缩小范围到具体概念名（例如「拉格朗日中值定理的条件」）。" % question
            )

        top = contexts[0]
        lines = []
        lines.append("**要点定位**：《%s》· %s" % (top["doc_title"], top["section"]))
        lines.append("")
        lines.append("**知识库原文依据**")
        for i, ctx in enumerate(contexts, 1):
            text = ctx["text"]
            if len(text) > 260:
                text = text[:260] + "…"
            lines.append("%d. 《%s》· %s" % (i, ctx["doc_title"], ctx["section"]))
            lines.append("   > %s" % text)
        if deep_think:
            lines.append("")
            lines.append("**推导思路**")
            for step, ctx in enumerate(contexts[:3], 1):
                lines.append("%d. 先确认「%s」这一条的前提与结论，再对照题目条件逐项核对。"
                             % (step, ctx["section"]))
        if related:
            lines.append("")
            lines.append("**相关练习**（来自题库，可在「考试模拟」中遇到）")
            for r in related:
                stem = r["stem"]
                if len(stem) > 80:
                    stem = stem[:80] + "…"
                lines.append("- [%s·难度%s] %s" % (r["type"], r["level"], stem))
        lines.append("")
        lines.append("_本回答由关键词检索自动组织，未接入大模型；配置 CALCULUS_LLM_* 后自动升级。_")
        return "\n".join(lines)

    def _chat(self, question):
        return (
            "当前处于「闲聊」工作流，不会检索知识库。\n\n"
            "你问的是：%s\n\n"
            "如果要查微积分概念、定理条件或解题方法，请把工作流切到「问答」或「自动」，"
            "我会去知识库里找对应章节作为依据。" % question
        )


# ------------------------------------------------------------------ 大模型实现

class OpenAICompatibleProvider:
    """OpenAI 兼容的 /chat/completions，用标准库实现，无第三方依赖。"""

    name = "openai_compatible"

    def __init__(self):
        self.base_url = (_env("CALCULUS_LLM_BASE_URL") or "").rstrip("/")
        self.api_key = _env("CALCULUS_LLM_API_KEY") or ""
        self.model = _env("CALCULUS_LLM_MODEL", "deepseek-chat")
        self.streaming = _env_bool("CALCULUS_LLM_STREAM", True)
        self.timeout = float(_env("CALCULUS_LLM_TIMEOUT", "60"))
        self.temperature = float(_env("CALCULUS_LLM_TEMPERATURE", "0.2"))
        # 0 表示不发送 max_tokens（理由见 _build_payload 注释）
        self.max_tokens = int(_env("CALCULUS_LLM_MAX_TOKENS", "0"))
        self.thinking_mode = (_env("CALCULUS_LLM_THINKING", "auto") or "auto").lower()
        if self.thinking_mode not in ("auto", "off", "on", "deep_think"):
            self.thinking_mode = "auto"
        self.reasoning_effort = _env("CALCULUS_LLM_REASONING_EFFORT")

    @property
    def label(self):
        return "大模型（%s）" % self.model

    @property
    def endpoint(self):
        return self.base_url + "/chat/completions"

    def available(self):
        return bool(self.base_url and self.api_key)

    def safe_base(self):
        """只暴露 scheme://host，避免把可能带凭据的路径写进公开接口。"""
        try:
            parts = urllib.parse.urlsplit(self.base_url)
            return "%s://%s" % (parts.scheme, parts.netloc)
        except Exception:
            return ""

    # -------------------------------------------------- 请求构造

    def _build_payload(self, question, contexts, related, deep_think, workflow, stream):
        evidence = "\n\n".join(
            "《%s》· %s\n%s" % (c["doc_title"], c["section"], c["text"]) for c in contexts
        ) or "（检索无结果）"
        instruction = (
            "请给出较详细的推导与解题步骤，并在结尾注明依据的章节。"
            if deep_think else
            "请简洁作答，并注明依据的章节。"
        )
        if workflow == "chat":
            instruction = "这是闲聊，不需要检索知识库，直接自然回答即可。"
        user_prompt = "知识库原文：\n%s\n\n问题：%s\n\n%s" % (evidence, question, instruction)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "stream": bool(stream),
        }
        # max_tokens 默认不发送。实测（DeepSeek deepseek-flash）：思考模式下思考内容
        # 会先消耗预算，max_tokens=64 时思考吃掉全部 64 个 token，正文一个字都没有。
        # 与其猜一个安全值，不如交给服务端的默认上限。
        if self.max_tokens > 0:
            payload["max_tokens"] = self.max_tokens
        thinking = self._thinking_value(deep_think)
        if thinking:
            payload["thinking"] = {"type": thinking}
            if thinking == "enabled" and self.reasoning_effort:
                payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _thinking_value(self, deep_think):
        """把 CALCULUS_LLM_THINKING 解析成要发送的 thinking.type，None 表示不发送（用服务商默认）。

        DeepSeek 的思考模式默认开启，查手册类问答纯属浪费（实测输出 token 多花 3~4 倍、
        首字延迟从 0.9s 涨到 1.3s）。deep_think 模式正好把界面上已有的「深度思考」开关
        映射到它：平时关闭，用户主动点开时才启用推理。
        """
        mode = self.thinking_mode
        if mode == "off":
            return "disabled"
        if mode == "on":
            return "enabled"
        if mode == "deep_think":
            return "enabled" if deep_think else "disabled"
        return None

    def _open(self, payload):
        if not self.available():
            raise LLMUnavailable("未配置 CALCULUS_LLM_BASE_URL / CALCULUS_LLM_API_KEY")
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
                "Accept": "text/event-stream" if payload.get("stream") else "application/json",
            },
            method="POST",
        )
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(300).decode("utf-8", "replace")
            except Exception:
                pass
            raise LLMUnavailable("大模型接口返回 HTTP %s %s" % (e.code, detail[:160]))
        except urllib.error.URLError as e:
            raise LLMUnavailable("无法连接大模型接口：%s" % e.reason)
        except Exception as e:
            raise LLMUnavailable("大模型调用失败：%s" % type(e).__name__)

    # -------------------------------------------------- 两种调用方式

    def compose(self, question, contexts, related, deep_think=False, workflow="kb"):
        """一次性返回完整回答。"""
        payload = self._build_payload(question, contexts, related, deep_think, workflow, False)
        resp = self._open(payload)
        try:
            data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            raise LLMUnavailable("大模型响应不是合法 JSON")
        try:
            text = data["choices"][0]["message"]["content"]
        except Exception:
            raise LLMUnavailable("大模型响应结构异常：缺少 choices[0].message.content")
        if not text or not text.strip():
            raise LLMUnavailable("大模型返回了空内容")
        return text.strip()

    def compose_stream(self, question, contexts, related, deep_think=False, workflow="kb"):
        """逐块 yield 文本增量（真正的流式）。

        连接阶段的错误会抛出 LLMUnavailable（此时尚未输出任何内容，可安全降级）；
        中途断流同样抛 LLMUnavailable，但调用方已经收到部分内容，应保留而非丢弃。
        """
        payload = self._build_payload(question, contexts, related, deep_think, workflow, True)
        resp = self._open(payload)
        produced = False
        saw_end = False
        try:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    saw_end = True
                    break
                try:
                    obj = json.loads(body)
                except Exception:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    produced = True
                    yield piece
                finish = choices[0].get("finish_reason")
                if finish and finish != "null":
                    saw_end = True
                    # 被长度截断时如实告知，避免用户以为回答完整
                    if finish == "length":
                        yield "\n\n> （回答达到长度上限被截断，可提高 CALCULUS_LLM_MAX_TOKENS 后重试。）"
                    break
        except LLMUnavailable:
            raise
        except Exception as e:
            raise LLMUnavailable("读取大模型流式响应失败：%s" % type(e).__name__)
        if not produced:
            raise LLMUnavailable("大模型未返回任何内容")
        if not saw_end:
            # http.client 会把被截断的 chunked 流当成正常结束，这里显式提示，避免静默给出残缺回答
            yield "\n\n> （连接在收到结束标记前中断，以上内容可能不完整。）"


# ------------------------------------------------------------------ 选择与描述

def get_provider():
    """配置了 BASE_URL + API_KEY 就用大模型，否则用模板。"""
    llm = OpenAICompatibleProvider()
    if llm.available():
        return llm
    return TemplateProvider()


def describe():
    """给 /api/v1/about 用的公开描述。绝不包含 API Key。"""
    llm = OpenAICompatibleProvider()
    if llm.available():
        return {
            "mode": llm.name,
            "label": llm.label,
            "model": llm.model,
            "base": llm.safe_base(),
            "streaming": llm.streaming,
            "thinking": llm.thinking_mode,
        }
    return {
        "mode": "keyword_template",
        "label": TemplateProvider.label,
        "model": None,
        "streaming": False,
        "hint": "设置 CALCULUS_LLM_BASE_URL / CALCULUS_LLM_API_KEY / CALCULUS_LLM_MODEL 即可切换为大模型",
    }
