# 微积分题库 · 考试模拟系统

一个「**题库 + 考试模拟**」的全栈系统：微积分知识库支撑带引用的智能问答，题库支撑规则组卷、自动判分、错题本与成绩分析。

架构参照 `dlu.pocketbay.app` 的思路复刻并重写：**原生 JS 单页前端 + Python 标准库后端 + SQLite 持久化 + SSE 流式回答**，大模型层可插拔，当前默认用「关键词检索 + 模板回答」跑通全流程，不联网、零费用。

**全程零第三方依赖**——只需要 Python 3.8+ 自带的模块。

---

## 快速开始

```bash
# 在项目根目录执行（Windows 下用你机器上的 python）
python server/app.py --port 8788
```

启动后打开 **http://127.0.0.1:8788/**

首次访问会弹出登录框：

| 账号 | 密码 | 说明 |
|---|---|---|
| `admin` | `admin123` | 内置管理员，首次建库时自动创建 |
| — | — | 也可以直接点「注册」创建自己的账号 |

> ⚠️ **部署到任何他人可访问的环境前，必须先改掉 admin 口令。** 当前实现里管理员是唯一内置账号，且口令写在 `server/store.py` 的 `ensure_admin()` 中。

可选参数：

```bash
python server/app.py --host 0.0.0.0 --port 9000 --db /path/to/app.db
```

代码里没有硬编码的绝对路径，整个目录可以直接拷到别的机器上运行。

---

## 日常使用

仓库里带了三个双击就能用的脚本（Windows）：

| 脚本 | 作用 |
|---|---|
| **`start.ps1`** | 启动网站。本机与同 Wi-Fi 的手机都能访问，窗口里会直接显示手机该用哪个地址 |
| **`start-public.ps1`** | 启动网站**并生成一个公网地址**（Cloudflare 免费隧道，不用注册）。任何网络都能打开，但**地址是临时的，关窗口就失效，重开换新地址** |
| **`stop.ps1`** | 停止网站和隧道 |

用法：右键文件 → **使用 PowerShell 运行**。（首次可能需要在 PowerShell 里先执行一次
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 允许运行脚本。）

脚本会自动找 Python；找不到会明确提示去哪装。端口被占用时会告诉你「服务可能已经在跑了」，
而不是甩一堆报错。

### 界面上有什么

| 页面 | 能干什么 |
|---|---|
| **智能问答** | 问微积分问题。回答由知识库检索 + 大模型组织，**每条都会标注依据的章节**。可开「深度思考」让它逐步推导 |
| **考试模拟** | 选题型配比、难度、章节、时长 → 组卷 → 答题（有倒计时，到点自动交卷）→ 看成绩报告 |
| **历史成绩** | 每次考试的总分、等级、正确率、分章节掌握度，可点开回看逐题解析 |
| **错题本** | 做错的题自动进来，附答案与解析；「错题重做」一键重组成卷；连续答对 2 次自动移出 |
| **知识库** | 按章浏览全部正文，或按关键词检索并高亮命中段落 |
| **关于** | 当前回答模式（模板 / 大模型）、今日额度剩余、使用提示 |

### 几个常见操作

```bash
# 忘了管理员密码
python tools/reset_password.py --list
python tools/reset_password.py admin '新密码'

# 看有哪些账号
python tools/reset_password.py --list

# 检查额度还剩多少（浏览器直接打开也行）
#   http://127.0.0.1:8788/api/v1/usage

# 检查服务是否正常
#   http://127.0.0.1:8788/api/v1/health
```

### 想改题库或知识库

内容全在这两个文件里，改完**重启服务**即可生效：

- `server/seed/knowledge.json` —— 章节与知识块
- `server/seed/questions.json` —— 题目

启动时会强制校验，数据有错会直接报出来并阻止启动，不会带着坏数据跑：

```
ValueError: 种子数据校验失败：
  - q-0007 答案 'E' 不在选项中 ['A', 'B', 'C', 'D']
  - q-0012 的 chapter 不在知识库章节中：第九章 级数
```

### 日常维护

- **额度**：默认每账号 20 次/天、全局 500 次/天。超了自动降级成本地模板回答，不会报错也不会继续花钱。
  想调整改 `llm.env` 或环境变量（见「环境变量总表」）。
- **数据备份**：全部数据在 `data/app.db` 一个文件里，复制走就是完整备份。
- **看日志**：服务窗口里会实时打印每条请求，出问题先看这个窗口。

---

## 公网部署

本系统默认按**可公开访问**来加固：限流、每日额度、答案防泄露、管理员随机初始密码。
但免费 PaaS 有一个绕不开的现实，先说清楚。

### ⚠️ 免费层会丢数据，这是必然的

免费 PaaS 的容器文件系统是**临时的**：

| 触发时机 | 后果 |
|---|---|
| 每次重新部署（push 代码） | `/data/app.db` 被清空 |
| 空闲一段时间后被平台回收（Render 免费层约 15 分钟） | 下次访问是冷启动，数据库重新建 |
| 平台例行维护 / 迁移 | 同上 |

**会丢的**：用户账号、问答记录、试卷、成绩、错题本。
**不会丢的**：题库与知识库（它们在仓库的 `server/seed/*.json` 里）、以及管理员密码（从环境变量读，见下）。

所以免费层适合「**公开演示**」；如果要留得住用户数据，看下面「想保住数据怎么办」。

### 方式一：Render（最简单，一键）

1. 把项目推到 GitHub 仓库
2. Render 控制台 → New → Blueprint → 选这个仓库（它会自动读 `render.yaml`）
3. 在 Environment 里填两个变量（`render.yaml` 里标了 `sync: false`，不会进仓库）：
   - `CALCULUS_LLM_API_KEY` = 你的 DeepSeek 密钥
   - `CALCULUS_ADMIN_PASSWORD` = 管理员初始密码（**必填**，否则每次重建都会随机生成并只打印在日志里）
4. 部署完成后打开分配的 `https://xxx.onrender.com`，用 `admin` + 你设的密码登录

HTTPS 平台自动提供，不用自己配证书。

### 方式二：Railway / Fly.io（支持挂载卷，数据能留住）

两者都支持挂载持久卷，把卷挂到 `/data` 即可：

```bash
# Fly.io 示例
fly launch --no-deploy
fly volumes create calc_data --size 1
# 在 fly.toml 里把 [[mounts]] 的 source 设为 calc_data、destination 设为 /data
fly secrets set CALCULUS_LLM_API_KEY=sk-xxx CALCULUS_ADMIN_PASSWORD=你的密码
fly deploy
```

Railway 同理：加一个 Volume 挂到 `/data`，并设置 `CALCULUS_DB=/data/app.db`。

### 方式三：自己的云服务器 / 内网穿透

- **云服务器**：直接 `python server/app.py --host 127.0.0.1 --port 8788`，前面挂 Nginx 做 HTTPS 反代，并设置 `CALCULUS_TRUST_PROXY=1`。用 systemd 或 supervisor 守护。
- **内网穿透（Cloudflare Tunnel / ngrok / frp）**：本机跑服务，穿透工具负责公网入口与 HTTPS。优点是几分钟上线；缺点是电脑必须开着。

### 想保住数据怎么办

当前实现用的是 SQLite，**没有做外部数据库支持**。三条路：

1. **挂持久卷**（Railway / Fly / 付费 Render Disk）—— 最省事，见方式二
2. **自己加备份**：定时把 `/data/app.db` 复制到对象存储，冷启动时拉回来。需要自己写脚本。
3. **换 Postgres**：需要改 `server/store.py` 的 SQL 方言（`?` 占位符、`ON CONFLICT`、`AUTOINCREMENT` 都要调整）。这是真正的一步工程，**当前不建议**。

---

## 部署前检查清单

上线前请逐条确认，每一条都对应一个真实的坑：

- [ ] **`CALCULUS_LLM_API_KEY` 配好了，但没有写进代码或仓库**（`.gitignore` 与 `.dockerignore` 都已排除 `llm.env`）
- [ ] **`CALCULUS_ADMIN_PASSWORD` 设成了强密码**，不再是 `admin123`（随机初始密码只在首次建库时打印，重建后就看不到了）
- [ ] **每日额度生效**：`CALCULUS_QUOTA_USER_DAILY=20`、`CALCULUS_QUOTA_GLOBAL_DAILY=500`
- [ ] **`CALCULUS_TRUST_PROXY=1`**（在反向代理/PaaS 后面必须开，否则限流会把所有用户当成同一个 IP）
- [ ] 打开 `/api/v1/health` 确认 `llm_mode` 和 `limits` 都符合预期
- [ ] 随便问一个问题，确认**回答里看不到任何密钥**
- [ ] 确认你知道怎么重置管理员密码：`python tools/reset_password.py --list`

---

## 安全与费用护栏

公开部署时这些是**必须**的，都已经实现并有测试守着：

### 费用护栏（最重要）

问答接口每次调用都在花运营者的钱。三层保护：

| 层 | 默认值 | 作用 |
|---|---|---|
| 每账号每日额度 | 20 次 | 单个用户刷不出量 |
| 全局每日额度 | 500 次 | **硬上限**，无论多少人来刷，当日最多 500 次调用 |
| 每用户频率 | 30 次/分 | 阻止爆发式滥用 |

**额度耗尽时不会报错**，而是自动降级为本地模板回答（把知识库原文组织给你），
用户仍然拿得到有用内容。对应错误码 `LLM_QUOTA_EXCEEDED_USER` / `LLM_QUOTA_EXCEEDED_GLOBAL`。

界面上会实时显示剩余额度。

### 频率限制

| 范围 | 默认 | 说明 |
|---|---|---|
| 每 IP 总数 | 240 次/分 | 一次页面加载有 3~4 个请求，卡太死会误伤正常用户 |
| 每用户（花钱接口） | 30 次/分 | 只作用于问答、组卷、错题重做 |
| 注册 | 8 次/分/IP | 机器人刷号的主要入口，卡最紧 |
| 登录 | 20 次/分/IP | 要容忍用户输错密码重试，比注册宽松 |

超限返回 HTTP 429 + `SYS_RATE_LIMITED`。

### 其他加固

| 项 | 说明 |
|---|---|
| 答案防泄露 | `/exam/questions/{id}/analysis` 只有**管理员**、或该题在**你已交卷的试卷**里、或在**你的错题本**里时才返回答案；否则 403 |
| 管理员初始密码 | 从 `CALCULUS_ADMIN_PASSWORD` 读取；未设置则**随机生成并只打印一次**，彻底废除 `admin123` |
| 请求体上限 | 默认 64 KB，超过返回 413（并先把请求体读掉，保证客户端能收到响应） |
| 并发上限 | 默认 32，超过返回 503，避免线程被无限创建 |
| 真实客户端 IP | `CALCULUS_TRUST_PROXY=1` 时才信任 `X-Forwarded-For`（直连场景下该头可伪造） |
| 改密码 | 页面「账号信息 → 修改密码」，改完吊销该用户全部令牌 |
| HTTPS | 由 PaaS / 反向代理提供；程序本身只跑 HTTP |

### 环境变量总表

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | — | PaaS 注入的端口；设置了就自动监听 `0.0.0.0` |
| `CALCULUS_DB` | `data/app.db` | 数据库路径，持久卷请指向 `/data/app.db` |
| `CALCULUS_ADMIN_PASSWORD` | 随机 | 管理员初始密码，仅首次建库时使用 |
| `CALCULUS_TRUST_PROXY` | 关 | 置 `1` 信任代理头 |
| `CALCULUS_QUOTA_USER_DAILY` | `20` | 每账号每日大模型次数，`0` 为不限 |
| `CALCULUS_QUOTA_GLOBAL_DAILY` | `500` | 全局每日大模型次数，`0` 为不限 |
| `CALCULUS_RATE_USER_PER_MIN` | `30` | 每用户（花钱接口）每分钟 |
| `CALCULUS_RATE_IP_PER_MIN` | `240` | 每 IP 每分钟 |
| `CALCULUS_RATE_REGISTER_PER_MIN` | `8` | 注册每 IP 每分钟 |
| `CALCULUS_RATE_LOGIN_PER_MIN` | `20` | 登录每 IP 每分钟 |
| `CALCULUS_MAX_BODY` | `65536` | 请求体字节上限 |
| `CALCULUS_MAX_CONCURRENT` | `32` | 并发请求上限 |
| `CALCULUS_QUIET` | 关 | 置 `1` 关闭访问日志 |
| `CALCULUS_LLM_*` | — | 见下文「接入大模型 API」 |

---

## 目录结构

```
calculus-exam/
├── server/
│   ├── app.py               HTTP 服务入口：路由、SSE、并发与请求体限制、静态资源
│   ├── store.py             SQLite 存储层：用户、令牌、会话、试卷、错题、事件、额度计数
│   ├── limits.py            限流与每日配额（公开部署的费用护栏）
│   ├── content.py           知识库与题库：加载、校验、倒排索引、BM25 检索、抽题
│   ├── qa.py                问答：会话管理、检索、回答组织、SSE 帧生成
│   ├── exam.py              组卷、判分、报告、错题本、答案可见性判定
│   ├── llm.py               可插拔大模型层（模板 / OpenAI 兼容，支持流式与思考模式）
│   └── seed/
│       ├── knowledge.json   知识库：6 章 / 163 个知识块
│       └── questions.json   题库：101 道题
├── web/
│   ├── index.html           页面骨架
│   └── assets/
│       ├── styles.css       明暗双主题样式
│       ├── api.js           API 客户端 + SSE 消费
│       └── app.js           单页应用：视图、状态、考试计时、LaTeX 兜底
├── tools/
│   └── reset_password.py    命令行重置密码（忘了密码时的后门）
├── tests/
│   ├── grading_test.py      判分与错题本单元测试（48 项）
│   ├── limits_test.py       限流与配额单元测试（22 项）
│   ├── llm_test.py          大模型接入测试，用本地假服务（58 项）
│   ├── smoke_test.py        端到端接口冒烟测试（117 项）
│   ├── frontend_check.py    前端静态一致性检查（17 项）
│   ├── latex_test.js        LaTeX 兜底转换测试，需要 Node（18 项）
│   └── probe_llm.py         大模型连通性探针（排查用，非测试）
├── Dockerfile               容器镜像（刻意不拷贝 llm.env）
├── .dockerignore            构建排除项
├── render.yaml              Render 一键部署（含全部护栏环境变量）
├── Procfile                 原生运行时启动命令
├── llm.env.example          大模型配置模板（复制为 llm.env 后填写）
├── .gitignore               已排除 llm.env / data / 临时文件
└── data/app.db              SQLite 数据库（首次启动自动生成）
```

---

## 功能一览

### 智能问答
- 知识库检索：中文按「汉字二元组 + ASCII 词」切分，BM25 风格 TF-IDF 打分。不引第三方分词库。
- 回答会标注**引用章节**（例如《第三章 微分中值定理与导数应用》· 3.2 拉格朗日中值定理）。
- 每条回答附带**相关练习**（从题库里挑同类题目）。
- 三个开关：**深度思考**（扩大检索范围 + 加推导思路）、**工作流**（自动 / 问答 / 闲聊）。
- SSE 流式输出，帧协议：`stage` / `citation` / `delta` / `done` / `error`。
- 知识库无命中时返回 `QA_NO_RELEVANT_CONTENT`，并明确告知题库覆盖范围，不编造内容。

### 考试模拟
- 四种题型：**单选（4 分）/ 多选（5 分）/ 判断（3 分）/ 填空（4 分）**。
- 组卷规则：题型配比、难度配比（基础 / 进阶 / 挑战）、指定章节、考试时长；单卷上限 20 题。
- 多选题**漏选得一半分、错选零分**；填空题答案做归一化比对（去空格、全角转半角、统一标点与正负号）。
- 考试中：倒计时（余 60 秒变红）、草稿自动保存、到时**自动交卷**。
- 交卷后：总分 / 等级 / 正确率、分章节掌握度、薄弱与优势知识点、逐题解析、学习建议。
- **错题本**：答错自动入库，连续答对 2 次自动移出；支持「错题重做」一键成卷。
- **历史成绩**：累计正确率、最好成绩、每次试卷可回看报告。

### 知识库
- 按章浏览全部正文，支持关键词检索并高亮命中块（显示相关度分数）。

### 运行监控（管理员）
- 用户 / 会话 / 消息 / 试卷 / 平均正确率 / 错题条目等指标，以及最近事件审计日志。

### 前端细节
- 明暗双主题、响应式布局（窄屏侧栏变抽屉）。
- 数学记号：`^` 自动转上标、`_` 转下标（`e^(2x)` → e²ˣ、`f_x` → f<sub>x</sub>），解析支持粗体、列表、引用、行内代码。

---

## 接入大模型 API

默认是 `keyword_template` 模式（纯本地检索 + 模板回答）。**任何兼容 OpenAI `/chat/completions` 的服务都能直接接上**，代码不用改，只填配置。

### 第 1 步：复制配置文件

```bash
cp llm.env.example llm.env        # Windows: copy llm.env.example llm.env
```

`llm.env` 已经在 `.gitignore` 里，不会被提交。

### 第 2 步：填入三行

```ini
CALCULUS_LLM_BASE_URL=https://api.deepseek.com/v1
CALCULUS_LLM_API_KEY=sk-你的密钥
CALCULUS_LLM_MODEL=deepseek-chat
```

### 第 3 步：重启服务

```bash
python server/app.py --port 8788
```

启动日志会多出一行（**不会回显密钥**）：

```
已从 D:\...\calculus-exam\llm.env 加载 6 项配置（不显示取值）
...
回答模式：大模型（deepseek-flash）
```

看到「回答模式：大模型（…）」就成功了。也可以打开页面的「关于」，那里会显示当前模式；或直接问一个问题，回答不再带「知识库原文依据」那种模板格式，并且逐字流式出现。

不放心的话先跑一次连通性探针，它会真实调用一次并把结果、耗时、token 用量都打出来：

```bash
python tests/probe_llm.py --extras
```

```
BASE_URL ：https://api.deepseek.com
MODEL    ：deepseek-flash
API_KEY  ：sk-b3a...4cfc（长度 35）

--- 非流式请求 ---
  成功  HTTP 200  用时 1.46s
  服务端返回的 model：deepseek-flash
  token 用量：{"prompt_tokens": 52, "completion_tokens": 229, ...}
```

`--extras` 会额外探测 `max_tokens`、`thinking`、`temperature` 三个参数是否被该服务商接受。

### 常见服务商填法

| 服务商 | `BASE_URL` | `MODEL` 示例 | `THINKING` |
|---|---|---|---|
| DeepSeek | `https://api.deepseek.com`（**无 `/v1`**） | `deepseek-flash` / `deepseek-v4-pro` | `deep_think` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | `auto` |
| 阿里云百炼（通义） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | `auto` |
| 月之暗面 Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | `auto` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | `auto` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3` | `auto` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5:7b`（Key 随便填 `ollama`，但不能为空） | `auto` |

> `BASE_URL` 要写到 `/v1` 那一级为止（DeepSeek 例外，它不带 `/v1`），**不要**自己带上 `/chat/completions`，程序会自动拼。
> `THINKING` 发送的是 DeepSeek 专有的 `{"thinking":{"type":...}}` 字段，其他服务商不认，所以默认为 `auto`（不发送）。

### 思考模式与「深度思考」开关

**DeepSeek 的思考模式默认开启**，但「查学生手册」这类问答用它纯属浪费。实测同一个微积分问题：

| | 耗时 | 输出 token | 说明 |
|---|---|---|---|
| 思考关闭 | 2.2s | 较少 | 直接给答案与依据 |
| 思考开启 | 11.5s | 3~4 倍 | 逐步推导，更详细 |

`CALCULUS_LLM_THINKING` 的四个取值：

| 取值 | 行为 |
|---|---|
| `deep_think` | **推荐**。平时关闭；界面上「深度思考」开关打开时才启用推理 |
| `off` | 始终关闭，最快最省 |
| `on` | 始终开启 |
| `auto` | 不发送该参数，完全用服务商默认（对 DeepSeek 等于 `on`） |

选 `deep_think` 后，页面上的「深度思考」按钮就真的在切换推理模式了，不是摆设。

### ⚠️ 关于 `max_tokens`：默认故意不发送

思考模式下，**思考内容会先消耗 `max_tokens` 预算**。实测把 `max_tokens` 设为 64 时，64 个 token 全被思考吃掉，**正文一个字都没有**，页面上就是一片空白。

所以程序默认**不发送** `max_tokens`，交给服务端的默认上限。确有限制需要时再设 `CALCULUS_LLM_MAX_TOKENS`，并且给足够大的值（建议 ≥4096）。

### 数学排版：不使用 LaTeX

本项目刻意**不引入 KaTeX / MathJax**，保持零依赖。系统提示词会要求模型使用与知识库一致的纯文本记号（`lim(x→0)`、`∫(0→1)`、`√`、`^` 表幂、`_` 表下标）。

万一家模型不听话仍然输出 LaTeX，前端 `app.js` 里的 `latexLite()` 会兜底转换：

```
\(\displaystyle \lim_{x\to 0}\frac{\sin x-x}{x^3}=-\frac16\)
   ↓
lim(x→0)(sin x-x)/(x^3)=-(1)/(6)
```

覆盖 `\frac`（含 `\frac16` 这种无花括号简写）、`\sqrt`、`\lim_{}`、`\int_{}^{}`、希腊字母、关系符等。这套转换有独立的回归测试：`node tests/latex_test.js`（18 项）。

### 可选项

| 变量 | 默认 | 说明 |
|---|---|---|
| `CALCULUS_LLM_STREAM` | `1` | 流式输出。设 `0` 则等模型整段返回后再显示 |
| `CALCULUS_LLM_TIMEOUT` | `60` | 单次请求超时（秒） |
| `CALCULUS_LLM_TEMPERATURE` | `0.2` | 越低越稳定（思考模式下该参数无效） |
| `CALCULUS_LLM_THINKING` | `auto` | 见上表 |
| `CALCULUS_LLM_REASONING_EFFORT` | 不发送 | `low` / `high` / `max`，仅在启用思考时发送 |
| `CALCULUS_LLM_MAX_TOKENS` | 不发送 | 见上方警告 |

### 配置优先级与安全

- 真实**环境变量优先**于 `llm.env`：文件里的同名项不会覆盖已存在的系统环境变量。CI / 容器里可以直接用环境变量，不必放文件。
- 密钥只从环境变量或 `llm.env` 读取，**不写进代码、不写进数据库、不出现在日志里**。
- `/api/v1/about` 是公开接口，它只暴露 `scheme://host` 级别的地址和模型名，**不含密钥、不含完整路径**（有专门的测试用例守着这一点）。
- 换配置文件路径：`python server/app.py --env-file /path/to/my.env`

### 工作流与检索的关系

接上大模型后，检索仍然照常工作，`workflow` 决定喂给模型什么：

| 工作流 | 行为 |
|---|---|
| 自动 | 先检索；命中知识库就带着原文问模型，没命中就走闲聊 |
| 问答 | 强制检索；检索不到直接返回 `QA_NO_RELEVANT_CONTENT`，不编造 |
| 闲聊 | 不检索，直接把问题交给模型 |

`deep_think` 打开后会要求模型给出详细推导步骤。**引用来源始终来自检索结果**，与模型无关，所以不会出现「模型编了一个章节」。

### 出错时会怎样

任何调用失败都**不会把异常抛给用户**，而是降级回模板回答：

| 情况 | 行为 |
|---|---|
| 网络不通 / HTTP 4xx / 5xx | 返回 `LLM_UNAVAILABLE_FALLBACK` 帧，正文降级为知识库原文组织结果，并在开头注明原因 |
| 响应不是合法 JSON / 结构异常 / 内容为空 | 同上 |
| 流式输出中途断线 | **保留已经生成的部分**，末尾追加「连接在收到结束标记前中断」提示，不丢弃重来 |
| 回答被 `max_tokens` 截断 | 末尾追加提示，建议提高上限（不会假装回答完整） |

模板模式（未配置）则完全不联网，离线可用。

### 常见问题

| 现象 | 原因与处理 |
|---|---|
| 启动日志仍是「回答模式：关键词检索 + 模板回答」 | 配置没读到。依次检查：文件名是不是 `llm.env`（不是 `llm.env.example`）；`BASE_URL` 与 `API_KEY` 是否都非空；等号是不是写成了全角 `＝` |
| 文件里明明有配置，但第一项不生效 | Windows 记事本、PowerShell 的 `Set-Content -Encoding utf8` 会写入 **BOM**，把第一行的键名污染成 `\ufeffCALCULUS_LLM_BASE_URL`。程序已用 `utf-8-sig` 兼容这种情况；若仍异常，请把文件另存为「UTF-8（无 BOM）」 |
| 问什么都说「智能回答暂时不可用」 | 大模型调用失败，正文已降级为知识库原文，**具体原因会写在回答开头**。常见：密钥错误、余额不足、`BASE_URL` 少了 `/v1`、模型名拼错 |
| 回答总是被截断 | 提高 `CALCULUS_LLM_MAX_TOKENS` |
| 首字要等好几秒才出现 | 设 `CALCULUS_LLM_STREAM=0` 时本来就要等整段返回；保持默认 `1` 才是逐字流式 |

---

## 换成你自己的资料

系统不认识「微积分」这个主题本身，内容全部来自两个 JSON 文件：

**`server/seed/knowledge.json`** —— 章节与知识块：

```json
{
  "documents": [
    {
      "doc_id": "ch1",
      "seq": 1,
      "title": "第一章 函数与极限",
      "source_format": "markdown",
      "visibility": "公开可读",
      "blocks": [
        { "level": 1, "text": "1.1 函数的概念" },
        { "level": 0, "text": "正文段落……" }
      ]
    }
  ]
}
```
`level >= 1` 视为标题（会作为引用里的「小节」），`level = 0` 视为正文。

**`server/seed/questions.json`** —— 题库：

```json
{
  "questions": [
    {
      "question_id": "q-0001",
      "type": "单选题",
      "level": 1,
      "chapter": "第一章 函数与极限",
      "knowledge_points": ["第一重要极限"],
      "stem": "lim(x→0) (sin 3x)/x = （ ）",
      "options": [
        { "key": "A", "text": "0" },
        { "key": "B", "text": "1" },
        { "key": "C", "text": "3" },
        { "key": "D", "text": "不存在" }
      ],
      "answer": "C",
      "analysis": "化为第一重要极限的标准结构……"
    }
  ]
}
```

题型约定：

| 题型 | 必填字段 | 约束 |
|---|---|---|
| 单选题 | `options`、`answer` | `answer` 为单个选项 key |
| 多选题 | `options`、`answer` | `answer` 为升序字母串，如 `"ABD"`，至少两个 |
| 判断题 | `options`、`answer` | 选项必须是 `A=正确`、`B=错误`，`answer` 为 `A`/`B` |
| 填空题 | `accepted` | 无 `options`；`accepted` 是参考答案数组，任一命中即得分 |

`chapter` 必须与知识库里的章节 `title` 完全一致。

改完 JSON **重启服务**即可生效。启动时会强制执行 `content.validate()`，数据有问题会直接报错并列出原因，不会带着坏数据跑起来：

```
ValueError: 种子数据校验失败：
  - q-0007 答案 'E' 不在选项中 ['A', 'B', 'C', 'D']
  - q-0012 的 chapter 不在知识库章节中：第九章 级数
```

---

## API 一览

所有接口在 `/api/v1` 下。需要登录的接口通过 `Authorization: Bearer <token>` 认证。

| 方法 | 路径 | 登录 | 说明 |
|---|---|---|---|
| GET | `/about` | — | 版本、内容统计、当前回答模式 |
| POST | `/auth/register` | — | 注册并返回令牌 |
| POST | `/auth/login` | — | 登录 |
| POST | `/auth/logout` | ✔ | 注销（吊销当前令牌） |
| GET/PUT | `/auth/profile` | ✔ | 读取 / 修改昵称与联系方式 |
| GET | `/kb/documents` | — | 章节列表与目录 |
| GET | `/kb/documents/{id}` | — | 单章正文 |
| GET | `/kb/search?q=` | — | 关键词检索 |
| GET | `/kb/points` | — | 知识点及题量分布 |
| GET | `/kb/stats` | — | 内容统计 |
| POST | `/qa/sessions` | ✔ | 新建会话 |
| GET | `/qa/sessions` | ✔ | 会话列表 |
| GET | `/qa/sessions/{id}/messages` | ✔ | 历史消息 |
| POST | `/qa/sessions/{id}/ask` | ✔ | **SSE 流式提问** |
| DELETE | `/qa/sessions/{id}` | ✔ | 删除会话 |
| POST | `/qa/local-migrate` | ✔ | 导入匿名期间的本地会话 |
| GET | `/exam/metadata` | — | 题型、分值、题量上限、难度分布、章节题量 |
| GET | `/exam/papers` | ✔ | 历史成绩（含汇总统计） |
| POST | `/exam/papers` | ✔ | 按规则组卷 |
| POST | `/exam/papers/migrate` | ✔ | 导入本地试卷 |
| GET | `/exam/papers/{id}` | ✔ | 取卷（**不返回答案**） |
| PUT | `/exam/papers/{id}/drafts` | ✔ | 保存草稿 |
| POST | `/exam/papers/{id}/submit` | ✔ | 交卷判分 |
| GET | `/exam/papers/{id}/report` | ✔ | 成绩报告与逐题解析 |
| GET | `/exam/wrong-questions` | ✔ | 错题本 |
| POST | `/exam/wrong-questions/retry` | ✔ | 错题重做成卷 |
| GET | `/exam/questions/{id}/analysis` | ✔ | 单题解析与同类题 |
| GET | `/system/metrics` | 管理员 | 运行指标 |
| GET | `/system/audit-logs` | 管理员 | 事件日志 |
| GET | `/admin/stats` | 管理员 | 管理面板数据 |

错误响应统一为 `{"code": "...", "message": "...", "details": {}}`，`code` 是稳定标识（如 `AUTH_REQUIRED`、`EXAM_INSUFFICIENT_QUESTIONS`），中文文案两边都有映射。

---

## 测试

三个脚本，都可以重复运行。

```bash
# 1) 判分引擎与错题本单元测试（不需要起服务）
python tests/grading_test.py          # 48 / 48 通过

# 2) 限流与每日配额单元测试（不需要起服务）
python tests/limits_test.py           # 22 / 22 通过

# 3) 大模型接入测试（不需要真实 Key，用本地假的 OpenAI 兼容服务）
python tests/llm_test.py              # 58 / 58 通过

# 4) 端到端接口冒烟测试（需要先启动服务）
python tests/smoke_test.py --base http://127.0.0.1:8788    # 117 / 117 通过
#    中文详细报告写入 tests/report.txt
#    提示：本地跑请把限流放宽（见下方说明），否则高频用例会被 429 挡住

# 5) 前端静态一致性检查（不需要起服务、不需要浏览器）
python tests/frontend_check.py        # 17 / 17 通过

# 6) LaTeX 兜底转换测试（需要 Node.js）
node tests/latex_test.js              # 18 / 18 通过
```

> 跑冒烟测试时它会在几十秒内发出 120 多个请求。默认的每 IP 240 次/分**刚好够用**，
> 但如果你反复连跑，可能会撞上 429。本地测试建议临时放宽：
> `CALCULUS_RATE_IP_PER_MIN=5000 CALCULUS_RATE_REGISTER_PER_MIN=1000 python server/app.py`

另有 `python tests/probe_llm.py` 不是测试而是**连通性探针**，用来排查密钥/模型名/参数问题。

覆盖范围：

- **单元（48 项）**：四种题型的判分（含多选漏选半分、顺序无关、填空全角/空格/大小写归一化）、组卷规则校验的各个拒绝分支、抽题的难度与章节过滤、题库自身的答案合法性与去重、**错题本的完整生命周期**（首次答错入库、重复答错累加、答对一次保留、再答错清零、连续答对两次移出、答对未错过的题不写入）。
- **限流与配额（22 项）**：滑动窗口放行到上限、超限拒绝、窗口滑动后恢复、不同 key 互不影响、每账号配额、全局配额、`0` 表示不限、历史计数清理、非法环境变量回退默认值。
- **大模型（58 项）**：请求格式（model / messages / 知识库原文 / Authorization 头）、`max_tokens` 与 `thinking` 参数的发送条件、思考模式四种取值、思维链 `reasoning_content` 不得混进正文、流式 SSE 解析、非流式回退、五类失败路径的降级行为、**额度用尽时必须真的不调用大模型**（用探针验证接口未被请求）、API Key 不泄漏进公开接口。
- **端到端（117 项）**：每个接口真实打一遍，包括 SSE 五类帧、越权访问、注销与改密后令牌失效、组卷规则的五种非法输入、**答案防泄露**（交卷前 403、交卷后 200、他人 403、管理员可查）、**通过错题本合法路径验证判分闭环能拿满分**、请求体超限 413、健康检查与额度接口、404/405。
- **静态（17 项）**：客户端调用的每个 API 路径都能在服务端路由表里找到、`$('#id')` 引用的元素都有定义、API 方法都已导出、SSE 帧都有处理分支。
- **LaTeX（18 项）**：`\frac`（含 `\frac16` 无花括号简写）、`\sqrt`、`\lim_{}`、`\int_{}^{}`、希腊字母、关系符的纯文本转换。

合计 **280 项**。

### 本轮开发中被测试/实测抓出的真实缺陷（均已修复）

1. `duration_minutes: 0` 被 `or` 短路成默认值，绕过了时长校验。
2. `create_paper` / `retry_wrong` 把 `user_id` 字符串当成 user 字典传给 `get_paper`，组卷直接 500。
3. **最严重**：保存草稿时对所有题型都套用了多选题的归一化（只保留字母并大写），把填空题答案 `(1/3)sin3x+C` 毁成 `CNSX`，导致填空题永远判错。
4. 加了流式支持后 `qa.py` 仍按旧的 `get_provider()` 返回值解包，一旦配置了大模型就会崩。
5. **`http.client` 会把被截断的 chunked 流当成正常结束**：大模型流式输出中途断线时不会抛异常，回答会被静默截断，用户以为答完了。
6. **Windows 的 BOM 会让配置静默失效**：记事本与 PowerShell 的 `Set-Content -Encoding utf8` 都会写 BOM，把第一行键名污染成 `\ufeffCALCULUS_LLM_BASE_URL`，日志显示「加载 N 项配置」但模式根本没切换。已改用 `utf-8-sig` 读取。
7. 接上真实模型后发现模型输出 LaTeX（`\(\frac{...}\)`），而前端不渲染 LaTeX，页面会显示源码。补了系统提示词约束 + 前端 `latexLite()` 兜底，并写了 18 项回归测试。顺带测出 `\frac16` 这种**无花括号简写**和 `→` 前后的多余空格没被处理。
8. **思考模式会把 `max_tokens` 预算吃光**：实测 `max_tokens=64` 时 64 个 token 全被思考消耗，**正文一个字都没有**。改为默认不发送 `max_tokens`。
9. 官方模型名早已从 `deepseek-chat` 变更，若照旧文档配置会直接 400。已按当前官方文档更正为 `deepseek-flash` / `deepseek-v4-pro`，且 DeepSeek 的 `BASE_URL` **不带 `/v1`**。
10. **`/exam/questions/{id}/analysis` 对任何登录用户都返回答案** —— 只要知道 question_id 就能把整套题库答案拉下来。公开部署前必须堵上，现在改为只允许管理员 / 自己已交卷的题 / 错题本里的题。
11. **默认管理员口令 `admin123`** —— 公开部署等于没有密码。改为从 `CALCULUS_ADMIN_PASSWORD` 读取，未设置则随机生成并只打印一次。
12. 冒烟测试原先**正是靠那个泄露接口拿答案**来验证判分闭环的。修复后改成走合法路径（错题本 → 错题重做 → 全对），既堵了漏洞又保住了测试强度。顺带为「连续答对两次移出错题本」补了确定性的单元测试——在 HTTP 层组卷是随机的，无法稳定断言次数变化。

---

## 已知限制

诚实列出，方便你判断是否够用：

1. **无头浏览器在本机沙箱下跑不起来**（Chromium 的 mojo IPC 需要命名管道，被安全策略拒绝）。因此前端只做了语法检查与静态一致性检查，**没有做真实的浏览器渲染验证**。后端是 93 项端到端真实请求验证过的。
2. **`/exam/questions/{id}/analysis` 对任何已登录用户都会返回答案**，这是为了让「逐题解析」「错题本」可用。但如果真用这套系统组织正式考试，这是一个作弊面——需要把该接口限制为「仅自己已交卷的题目」可查。
3. **填空题靠字符串归一化比对**：`accepted` 数组里的变体覆盖不到时会被判错（例如把 `(1/3)sin3x+C` 写成 `sin(3x)/3 + C` 是覆盖到的，但写成 `1/3·sin3x + 常数` 就不是）。正式使用建议多列几个变体。
4. **单进程 SQLite**：适合个人 / 小班使用。并发量大时需要换 PostgreSQL 并把判分与检索拆出去。
5. **没有文件上传入口**：知识库与题库来自 `server/seed/*.json`，改内容需要改文件并重启。原站那套「上传 docx 自动解析入库」的流程没有实现。
6. **令牌是随机串存库比对**，没有做刷新令牌、异地登录踢下线等机制；有效期 7 天。
7. 事件日志表会一直增长，长期运行需要自行加清理策略。

---

## 许可

MIT。
