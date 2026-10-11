#!/usr/bin/env python3
"""前端静态一致性检查（不需要浏览器）。

沙箱下无法运行无头浏览器（Chromium 的 mojo IPC 需要命名管道，会被拒绝），
因此用静态分析覆盖最容易出错的三类问题：

1. api.js 里调用的接口路径，在 app.py 的路由表里是否真实存在（客户端/服务端漂移）；
2. app.js 里 $('#id') 引用的元素 id，是否在某处模板中被定义过（选择器拼写错误）；
3. app.js 里用到的 API.xxx 方法，是否都在 api.js 中导出（方法名拼写错误）。
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web", "assets")
SERVER = os.path.join(ROOT, "server")

sys.path.insert(0, SERVER)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print("%-52s %s" % (name, "PASS" if ok else "FAIL") + (("   " + detail) if (detail and not ok) else ""))


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def concrete(path):
    """把 `/api/v1/x/${id}` 里的占位符替换成示例值，便于与服务端正则做具体匹配。"""
    return re.sub(r"\$\{[^}]*\}", "abc123", path)


def main():
    app_js = read(os.path.join(WEB, "app.js"))
    api_js = read(os.path.join(WEB, "api.js"))
    index_html = read(os.path.join(ROOT, "web", "index.html"))
    app_py = read(os.path.join(SERVER, "app.py"))

    # ---------------------------------------------- 1. 路由漂移
    route_src = re.findall(r'\("(GET|POST|PUT|DELETE)",\s*r"([^"]+)"', app_py)
    routes = [(m, re.compile("^" + p + "$")) for m, p in route_src]
    check("routes.parsed", len(routes) >= 28, "解析到 %d 条路由" % len(routes))

    calls = re.findall(r"\b(get|post|put|del)\(\s*[`'\"](/api/v1/[^`'\"]*)[`'\"]", api_js)
    method_map = {"get": "GET", "post": "POST", "put": "PUT", "del": "DELETE"}
    check("client.calls_parsed", len(calls) >= 25, "解析到 %d 个客户端调用" % len(calls))

    unmatched = []
    for fn, raw_path in calls:
        path = concrete(raw_path.split("?")[0])
        method = method_map[fn]
        if not any(m == method and r.match(path) for m, r in routes):
            unmatched.append("%s %s" % (method, raw_path))
    check("client.paths_all_served", not unmatched, "无对应路由：" + "; ".join(unmatched))

    # 反向：服务端暴露但客户端未调用的接口（仅提示，用于发现遗漏的前端入口）
    client_set = {(method_map[fn], concrete(raw.split("?")[0])) for fn, raw in calls}
    uncovered = []
    for m, p in route_src:
        sample = re.sub(r"\(\?P<\w+>\[[^\]]+\]\+?\)", "abc123", p)
        if (m, sample) not in client_set:
            uncovered.append("%s %s" % (m, p))
    print("      提示：服务端 %d 条路由，客户端直接覆盖 %d 条" % (len(routes), len(routes) - len(uncovered)))
    if uncovered:
        print("      未覆盖：" + "; ".join(uncovered))

    # ---------------------------------------------- 2. DOM id
    defined = set(re.findall(r'id="([A-Za-z][\w\-]*)"', app_js))
    defined |= set(re.findall(r"id='([A-Za-z][\w\-]*)'", app_js))
    defined |= set(re.findall(r'id="([A-Za-z][\w\-]*)"', index_html))

    used_ids = set(re.findall(r"\$\(\s*'#([A-Za-z][\w\-]*)'", app_js))
    used_ids |= set(re.findall(r'\$\(\s*"#([A-Za-z][\w\-]*)"', app_js))
    used_ids |= set(re.findall(r"getElementById\(\s*'([^']+)'", app_js))

    missing = sorted(i for i in used_ids if i not in defined)
    check("dom.all_queried_ids_defined", not missing,
          "未定义的 id：" + ", ".join(missing) + "（已定义 %d 个）" % len(defined))

    # ---------------------------------------------- 3. API 方法
    exported = set(re.findall(r"^\s{4}([A-Za-z]\w*)\s*[:(]", api_js, re.M))
    exported |= set(re.findall(r"^\s{4}([A-Za-z]\w*),", api_js, re.M))
    # 顶层 return 里以「名字: ...」或「名字,」形式导出的都算
    return_block = api_js.split("return {", 1)[-1]
    exported = set(re.findall(r"([A-Za-z]\w*)\s*[:(,]", return_block))

    used_api = set(re.findall(r"\bAPI\.([A-Za-z]\w*)", app_js))
    unknown = sorted(m for m in used_api if m not in exported)
    check("api.all_methods_exported", not unknown,
          "未导出的方法：" + ", ".join(unknown))

    # ---------------------------------------------- 4. 其他基本一致性
    for asset in ("styles.css", "api.js", "app.js"):
        check("assets.%s_linked" % asset.replace(".", "_"),
              ("/assets/" + asset) in index_html)

    check("app.has_mount_point", 'id="app"' in index_html and "$('#app')" in app_js)
    check("app.has_toast_layer", 'id="toast-layer"' in index_html and "$('#toast-layer')" in app_js)

    # 五类 SSE 帧都应被前端处理
    for ev in ("stage", "citation", "delta", "done", "error"):
        check("sse.handles_%s" % ev, ("'%s'" % ev) in app_js)

    # 浅色/深色主题都应定义
    css = read(os.path.join(WEB, "styles.css"))
    check("css.dark_theme_defined", 'data-theme="dark"' in css)
    check("css.uses_css_vars", css.count("var(--") > 40)

    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n" + "=" * 60)
    print("TOTAL %d / %d passed" % (passed, len(RESULTS)))
    print("=" * 60)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
