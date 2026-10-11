# 部署到 PocketBay 逐步清单

> 目标：拿到固定网址 `https://<项目名>.pocketbay.app`
> 代码来源：**本地目录直接上传，不需要 GitHub，也不需要 Gitee 推送**
> 预计耗时：10~20 分钟
> 平台状态：公开内测，**内测期全部免费**（官方原话：价格与配额可能在正式发布前调整）

---

## 一、先看这三条（决定成败）

### 1. ⚠️ 官网写的 `pip install pocketbay` 目前装不了

我实测过 PyPI：`https://pypi.org/pypi/pocketbay/json` 和 `https://pypi.org/simple/pocketbay/` **都返回 404**，说明这个 CLI 包还没公开上架。
所以**别走 CLI 那条路**，走官方真正在用的 **Agent 部署协议**（让 AI 编程工具替你执行，见第三节）。

### 2. ✅ 好消息：它有持久卷，你的数据比放在 Render 免费层更稳

官方 Agent 协议原文写着：

> **项目持久卷**：动态应用挂载 `/data` 并注入 `POCKETBAY_DATA_DIR=/data`，**跨更新保留**。

而你的 [Dockerfile 第 19 行](calculus-exam/Dockerfile#L19) 早就写了 `CALCULUS_DB=/data/app.db` —— **正好落在 `/data` 这个持久卷上**。
对比 [Render 免费层](calculus-exam/部署到Render清单.md)（无磁盘、每次重新部署必丢账号和成绩），这是 PocketBay 的实质优势。
**但这是官方声明，仍要自己实测一次（见第六节）。**

### 3. ⚠️ 配对页的"数据库做法"必须选 D，选错就白折腾

官方协议给的四个选项（只在配对页出现，必须你来点）：

| 选项 | 含义 | 你该选吗 |
|---|---|---|
| A | 结构 + 数据迁到平台托管 PostgreSQL | ❌ 别选 |
| B | 只迁结构到平台托管库 | ❌ 别选 |
| C | 不迁库，改用外部数据库连接 | ❌ 别选 |
| **D** | **SQLite / 文件数据库改用持久卷 `/data`** | ✅ **选这个** |

原因：你的系统是 SQLite 实现，[README 明确写了"没有做外部数据库支持"](calculus-exam/README.md#L158)。选 A/B 会得到一个连不上的库。

---

## 二、部署前准备（2 分钟）

### 2.1 把管理员密码写进 `llm.env`

现在 `llm.env` 里**没有** `CALCULUS_ADMIN_PASSWORD`，那样程序会随机生成密码、只在日志里打印一次，你之后很可能登不进去。
用记事本打开 [llm.env](calculus-exam/llm.env)，加一行（密码至少 6 位、必须同时含字母和数字）：

```
CALCULUS_ADMIN_PASSWORD=你设的强密码
```

> 顺序是安全的：[app.py:721](calculus-exam/server/app.py#L721) 先加载 `llm.env` 到环境变量，[app.py:725](calculus-exam/server/app.py#L725) 才初始化数据库，所以 `ensure_admin()` 能读到这个密码。

### 2.2 想清楚密钥给平台这件事

PocketBay 的配置主路径是：**配对页批准 → 配置加密入库 → 运行时按原路径只读挂载**（官方协议称 `upload_mode=package`）。
也就是说，如果你在配对页批准迁移 `llm.env`，你的 **DeepSeek 密钥会加密存到 PocketBay 的服务器上**。

- 能接受 → 最省事，什么都不用额外填
- 不能接受 → 部署完到 DeepSeek 控制台**轮换一次密钥**，或者干脆改用 [Render 方案](calculus-exam/部署到Render清单.md)（密钥只存在 Render 环境变量里）

无论如何，你的费用护栏都还在：20 次/人/天、**全局 500 次/天的硬上限**。

### 2.3 关于隐私，先知道两件事

协议里明确提到应用可以进入平台的 **「发现」频道**（别人能直接打开你的应用），而且平台可能派「体验官」以标记 UA 访问做走访。
如果你不想让这个考试系统被公开试用：**部署后进控制台把项目访问权限设成不公开**。里面有学生账号数据，这一步别省。

---

## 三、正式部署（照着做）

### 步骤 1：把这句话发给一个"有外网的" AI 编程工具

官方标准提示词（原文）：

```
Please deploy the current project to PocketBay. Deployment guide: https://pocketbay.com/deploy
```

或者中文：

```
请把当前项目部署到 PocketBay，部署手册：https://pocketbay.com/deploy
```

**发给谁**：Cursor、Claude Code、Codex、GitHub Copilot、Windsurf、Trae 等（官方列出支持这些）。
**不要发给我（当前 DSH 会话）** —— 我实测这台机器的命令行**完全没有外网**：`cn.pocketbay.com`、`pypi.org`、`gitee.com` 三个 HTTPS 请求全部失败，所以我无法替你上传、也无法轮询构建状态。

**在哪个目录**：打开 `D:\Users\ASUS\Documents\deepseek-harness\default-workspace\calculus-exam`（这就是仓库根目录，协议要求"上传对象永远是仓库根目录"，**不要**选子目录、也**不要**告诉它"只要 server/ 或 web/"）。

### 步骤 2：AI 会给你一个配对链接

AI 会先在平台创建"配对会话"，然后**单独发一条消息**给你一个链接，形如：

```
https://pocketbay.com/pair/ABCD-EFGH
```

它的行为规范（官方协议要求的）：这条消息**只有链接本身**，不夹带解释；你在网页点完就行，**不用回对话里告诉它"我授权了"**。

### 步骤 3：在配对页做三个确认

在浏览器里登录 PocketBay（没有账号就现场注册），然后：

1. **项目名**：建议就用 `calculus-exam`（它决定网址前缀）
2. **数据库做法**：选 **D —— SQLite 改用持久卷 `/data`**（见第一节第 3 条，这步最关键）
3. **配置文件**：如果列到了 `llm.env`，**批准它**（这样密钥和管理员密码才能进容器）；不要批准 `data/` 里的数据库文件

配对码有效期约 **10 分钟**，超时要让 AI 重新建会话。

### 步骤 4：AI 打包上传、平台构建

AI 会自动打包（默认排除 `.git`、`__pycache__`、本地数据库文件与 dump、未批准的 `.env`/密钥），上传后平台自己识别项目类型并构建。

**注意：不要告诉 AI 用什么框架、不要传 `framework_hint`** —— 官方协议明确禁止，平台自己会识别（你根目录有 `Dockerfile`，它走单容器 Web 应用那条路）。

### 步骤 5：拿到网址

成功后地址是：

```
https://calculus-exam.pocketbay.app
```

（具体前缀以实际返回的项目 slug 为准。首次部署如果失败，官方允许最多重试 3 次。）

---

## 四、上线后立刻验证 5 项

- [ ] 打开 `https://<你的地址>/api/v1/health` → 应返回 `{"status": "ok", ...}`，`questions` 是 `101`
- [ ] 打开首页，界面正常
- [ ] 用 `admin` + 你在 2.1 里设的密码登录成功
- [ ] 提一个知识库问题，回答正常，且**回答和日志里都看不到密钥**
- [ ] 注册一个普通账号（模拟同学的使用路径）

---

## 五、出错了怎么办（官方错误分类，别自己瞎猜）

官方说失败时**只信 `error_category` 和 `repair_hint`** 两个字段，把 AI 看到的原始信息留着。

| 报错类别 | 含义 | 怎么办 |
|---|---|---|
| `app_port_mismatch` / `port_binding` / `app_not_listening` | 进程没读 `PORT` 或绑了 `127.0.0.1` | 你的代码已满足（[app.py:711-712](calculus-exam/server/app.py#L711) 读 `PORT` 并绑 `0.0.0.0`），若真报这个再看日志 |
| `host_resource_pressure` | 平台运行节点容量不足 | **别改代码、别重试**，等平台恢复 |
| `host_port_conflict` | 平台回环端口冲突 | 平台会自动换端口，**别改代码** |
| `dependency_error` / `app_start_error` / `app_crashed` | 应用自身问题 | 看 `build_logs_tail` / `runtime_logs_tail` 再改 |
| 413 | 上传包太大 | 去掉依赖/缓存，别删业务资源 |
| `wait_for_database` | 数据库声明没完成 | 回配对页把数据库做法选完 |
| `plan_limit_exceeded` | 免费套餐项目数满了 | **别新建项目**，改选已有项目重新授权 |

其他硬规则：同一构建错误**不许反复新建会话**碰运气；总尝试次数上限 3 次；`active_release_unchanged=true` 时说明**旧版本还在服务**，不算停机。

---

## 六、最重要的一次实测：数据到底留不留得住

官方说持久卷"跨更新保留"，但这是内测平台，**必须自己验**：

1. 在线上应用**注册一个测试账号**（比如 `test001`）
2. 回 PocketBay 控制台，点一次重新部署 / 或让 AI 再部署一次（版本更新）
3. 等它上线后，**登录看 `test001` 还在不在**

- ✅ 还在 → 数据保住了，这个平台对你的项目**比 Render 免费层强**，可以留
- ❌ 没了 → 说明实际行为和宣传不符，那就：要么接受"每次更新清数据"（和 Render 一样），要么改用 [Railway / Fly.io 挂持久卷](calculus-exam/README.md#L138) 的方案

**这个测试顺便验证了另一个问题**：`/data` 挂载是否真的生效。如果测试账号丢了，同时也说明挂载没生效。

---

## 七、已知代价（提前接受，别当成 bug）

- **内测平台**：2026 年 8 月才发布，价格与配额"正式发布前可能调整"
- **主体在香港**，长期稳定性未经时间验证
- **不是永久免费承诺**：如果正式发布后开始收费，你要么付费、要么迁走（好在代码和 Dockerfile 都是标准的，迁 Render / Railway 不用改代码）
- **可能会休眠**：官方能力页面提到 sleep/wake 生命周期，冷启动可能要等几十秒
- **应用可能进入「发现」频道**，任何人都能打开试用（记得去控制台设访问权限）
- 免费额度以 **Credits / 构建时间 / 上传体积**计量，内测期不阻塞，正式发布后可能受限

---

## 八、和 Render 怎么选（结论）

| | PocketBay | Render 免费层 |
|---|---|---|
| Git 仓库 | **不需要** | 需要（Gitee 或 GitHub） |
| 数据（SQLite） | 挂载 `/data` 持久卷，**声称跨更新保留** | **必丢**（无磁盘） |
| 费用 | 内测免费，配额会变 | 永久免费层，规则稳定 |
| 成熟度 | 很新（2026-08） | 成熟 |
| 国内访问 | 香港主体，可能更快 | 新加坡节点，偏慢 |
| 网址 | `xxx.pocketbay.app` | `xxx.onrender.com` |

**建议**：先按本清单试 PocketBay（不用碰 Git、内测免费、数据可能保得住），做完第六节的实测再决定去留；如果实测数据保不住，再回到 [Render 清单](calculus-exam/部署到Render清单.md)。

---

## 附：我准备好的干净部署包（备用）

同目录外的 [../calculus-exam-pocketbay.zip](../calculus-exam-pocketbay.zip)（107.7 KB，20 个文件）是我打包的干净副本：

- **含**：`Dockerfile`、`.dockerignore`、`server/`（含题库）、`web/`、`tools/`、`render.yaml`
- **不含**：`llm.env`（密钥）、`data/`（数据库）、`tests/`、`__pycache__`

**什么时候用它**：只有当**配对页没有配置迁移**、又能另外给应用注入环境变量时才有意义。
**默认别用它**：因为你的密钥和管理员密码就在 `llm.env` 里，而 PocketBay 的配置主路径是"批准后随包上传、加密入库"——直接让 AI 打包**原始目录**才对。这个 ZIP 更适合当"给别人试玩/备份"的干净副本。
