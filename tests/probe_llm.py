#!/usr/bin/env python3
"""大模型连通性探针：读 llm.env，真实调用一次，把关键信息打出来。

用来回答「我的密钥/模型名/地址到底配对了没有」。会实际产生极少量 token 费用。

用法：
    python tests/probe_llm.py              # 同时测非流式与流式
    python tests/probe_llm.py --no-stream  # 只测非流式
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

DEFAULT_ENV = os.path.join(ROOT, "llm.env")


def load_env(path):
    if not os.path.isfile(path):
        return False
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().lstrip("\ufeff")
            v = v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v
    return True


def mask(key):
    if not key:
        return "(空)"
    if len(key) <= 12:
        return key[:3] + "***"
    return "%s...%s（长度 %d）" % (key[:6], key[-4:], len(key))


def call(base_url, key, model, stream, extra=None, timeout=60):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是简洁的助教，只回答一句话。"},
            {"role": "user", "content": "用一句话说明拉格朗日中值定理的结论。"},
        ],
        "stream": stream,
    }
    if extra:
        payload.update(extra)
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "Accept": "text/event-stream" if stream else "application/json",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.read(400).decode("utf-8", "replace"),
                "elapsed": round(time.time() - t0, 2)}
    except Exception as e:
        return {"ok": False, "status": None, "error": "%s: %s" % (type(e).__name__, e),
                "elapsed": round(time.time() - t0, 2)}

    if not stream:
        raw = resp.read().decode("utf-8")
        elapsed = round(time.time() - t0, 2)
        try:
            data = json.loads(raw)
        except Exception:
            return {"ok": False, "status": resp.status, "error": "响应不是 JSON：" + raw[:200],
                    "elapsed": elapsed}
        try:
            msg = data["choices"][0]["message"]
        except Exception:
            return {"ok": False, "status": resp.status,
                    "error": "响应结构异常：" + json.dumps(data, ensure_ascii=False)[:300],
                    "elapsed": elapsed}
        usage = data.get("usage") or {}
        return {
            "ok": True, "status": resp.status, "elapsed": elapsed,
            "content": (msg.get("content") or "").strip(),
            "has_reasoning": bool(msg.get("reasoning_content")),
            "reasoning_chars": len(msg.get("reasoning_content") or ""),
            "usage": usage,
            "model_echo": data.get("model"),
        }

    # 流式：记录第一个 content 增量出现的时刻，以及是否有 reasoning_content
    first_content_at = None
    chars = 0
    reasoning_chars = 0
    saw_done = False
    text = []
    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                saw_done = True
                break
            try:
                obj = json.loads(body)
            except Exception:
                continue
            for ch in (obj.get("choices") or []):
                d = ch.get("delta") or {}
                if d.get("reasoning_content"):
                    reasoning_chars += len(d["reasoning_content"])
                if d.get("content"):
                    if first_content_at is None:
                        first_content_at = round(time.time() - t0, 2)
                    chars += len(d["content"])
                    text.append(d["content"])
    except Exception as e:
        return {"ok": False, "status": resp.status,
                "error": "读取流时出错：%s: %s" % (type(e).__name__, e),
                "elapsed": round(time.time() - t0, 2)}
    return {
        "ok": True, "status": resp.status, "elapsed": round(time.time() - t0, 2),
        "first_content_at": first_content_at, "content_chars": chars,
        "reasoning_chars": reasoning_chars, "saw_done": saw_done,
        "content": "".join(text).strip(),
    }


def show(title, r):
    print("\n--- %s ---" % title)
    if not r["ok"]:
        print("  失败  HTTP %s  用时 %ss" % (r.get("status"), r.get("elapsed")))
        print("  原因  %s" % str(r.get("error"))[:400])
        return False
    print("  成功  HTTP %s  用时 %ss" % (r.get("status"), r.get("elapsed")))
    if "content" in r:
        print("  内容  %s" % r["content"][:120])
    if "model_echo" in r:
        print("  服务端返回的 model：%s" % r.get("model_echo"))
    if "has_reasoning" in r:
        print("  思考内容(reasoning_content)：%s（%d 字）"
              % ("有" if r["has_reasoning"] else "无", r.get("reasoning_chars", 0)))
    if r.get("usage"):
        print("  token 用量：%s" % json.dumps(r["usage"], ensure_ascii=False))
    if "first_content_at" in r:
        print("  首个正文增量出现在 %ss（此前若在思考，只会看到空白）" % r.get("first_content_at"))
    if "saw_done" in r:
        print("  收到 [DONE] 结束标记：%s" % ("是" if r["saw_done"] else "否（流被截断）"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=DEFAULT_ENV)
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--extras", action="store_true",
                    help="额外探测 max_tokens 与 thinking 参数是否被接受")
    args = ap.parse_args()

    print("配置文件：%s（%s）" % (args.env_file, "存在" if os.path.isfile(args.env_file) else "不存在"))
    load_env(args.env_file)

    base = os.environ.get("CALCULUS_LLM_BASE_URL", "")
    key = os.environ.get("CALCULUS_LLM_API_KEY", "")
    model = os.environ.get("CALCULUS_LLM_MODEL", "deepseek-chat")
    print("BASE_URL ：%s" % (base or "(未设置)"))
    print("MODEL    ：%s" % model)
    print("API_KEY  ：%s" % mask(key))
    if not base or not key:
        print("\n❌ BASE_URL 或 API_KEY 未设置，先在 llm.env 里填好。")
        return 1

    final_url = base.rstrip("/") + "/chat/completions"
    print("实际请求 ：POST %s" % final_url)

    ok_non = show("非流式请求", call(base, key, model, False))
    if not ok_non:
        return 1

    if not args.no_stream:
        show("流式请求", call(base, key, model, True))

    if args.extras:
        show("带 max_tokens=64", call(base, key, model, False, {"max_tokens": 64}))
        show("带 thinking=disabled",
             call(base, key, model, False, {"thinking": {"type": "disabled"}}))
        show("带 temperature=0.2",
             call(base, key, model, False, {"temperature": 0.2}))

    print("\n结论：密钥与地址可用。若上面某一步失败，把失败原因发我。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
