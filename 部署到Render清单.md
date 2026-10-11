# 部署到 Render 逐步清单（方案 B1：Gitee 当代码源）

> 目标：拿到一个固定网址 `https://calculus-exam.onrender.com`
> 代码来源：你已有的 Gitee 仓库 `https://gitee.com/Anthonywcx/daima.git`
> 预计耗时：首次 15~25 分钟（主要花在等 Docker 构建）

---

## 0. 先准备两样东西

| 要准备的 | 从哪拿 |
|---|---|
| `CALCULUS_LLM_API_KEY`（DeepSeek 密钥） | 打开同目录的 `llm.env`，复制 `CALCULUS_LLM_API_KEY=` 后面那一整串 |
| `CALCULUS_ADMIN_PASSWORD`（管理员密码） | 自己想一个，**至少 6 位且同时含字母和数字**（否则程序会改成随机值，你就登不进去了） |

⚠️ 这两个值**不要**写进任何要提交的文件里。下面的清单里用 `在这里粘贴密钥` / `在这里粘贴你的强密码` 占位。

---

## 1. 确认 Gitee 仓库是「公开」的

Render 要匿名拉取你的代码，所以仓库必须公开。

1. 打开一个**无痕/隐私窗口**（Edge 按 `Ctrl+Shift+N`）
2. 访问：https://gitee.com/Anthonywcx/daima
3. 能看到文件列表 = 公开，✅ 继续下一步
4. 如果要求登录、或提示私有 → 到 Gitee 仓库 → `管理` → `仓库设置` → 把「开源/公开」设为公开（Gitee 要求先完成实名认证）

**同时确认默认分支名**：在仓库首页看左上角分支名，应该是 `master`。记住它，第 4 步要用。

---

## 2. 注册 / 登录 Render

1. 打开 https://dashboard.render.com/register
2. 用邮箱注册（也可以点 GitHub 登录，若 GitHub 打不开就老实用邮箱）
3. 收邮件验证，登录进控制台

---

## 3. 新建 Web Service，选「Public Git Repository」

1. 右上角 `New` → 选 **Web Service**
2. 在代码来源的三个标签里，选 **Public Git Repository**（不要选 Git Provider）
3. 输入框里填：

   ```
   https://gitee.com/Anthonywcx/daima.git
   ```

4. 点 **Connect**

- ✅ 连上了 → 继续第 4 步
- ❌ 报错 / 一直转圈 / 提示无法访问仓库 → 这条路走不通（Gitee 不在 Render 官方支持的提供方里），跳到文末「如果连不上怎么办」

---

## 4. 填服务信息

| 字段 | 填什么 |
|---|---|
| **Name** | `calculus-exam` ← 决定网址前缀 |
| **Region** | `Singapore` ← 离国内最近，别选美国 |
| **Branch** | `master` ← 必须和你 Gitee 仓库的默认分支一致 |
| **Language / Runtime** | `Docker` |
| **Dockerfile Path** | `./Dockerfile` |
| **Build Command / Start Command** | 留空（Docker 模式不需要，Dockerfile 里已写好 `CMD`） |
| **Instance Type** | `Free` |
| **Health Check Path**（在 Advanced 里） | `/api/v1/health` |

> 端口不用填：Render 用 `PORT` 环境变量注入，你的 `server/app.py` 已经会自动改听 `0.0.0.0`。

---

## 5. 环境变量（13 个）

在 `Advanced` → `Environment Variables` 里，Render 支持 **Add from .env** 按钮，把下面整段粘进去最快（记得替换两个占位）：

```env
CALCULUS_LLM_BASE_URL=https://api.deepseek.com
CALCULUS_LLM_MODEL=deepseek-flash
CALCULUS_LLM_THINKING=deep_think
CALCULUS_LLM_STREAM=1
CALCULUS_LLM_API_KEY=在这里粘贴密钥
CALCULUS_ADMIN_PASSWORD=在这里粘贴你的强密码
CALCULUS_TRUST_PROXY=1
CALCULUS_QUOTA_USER_DAILY=20
CALCULUS_QUOTA_GLOBAL_DAILY=500
CALCULUS_RATE_USER_PER_MIN=6
CALCULUS_RATE_IP_PER_MIN=20
CALCULUS_MAX_CONCURRENT=32
CALCULUS_MAX_BODY=65536
CALCULUS_DB=/data/app.db
```

> `CALCULUS_TRUST_PROXY=1` 别漏：在 Render 的反代后面，不设它的话所有访问者会被当成同一个 IP，限流会误伤。
> 额度那 4 个值是你的**费用护栏**，别改大。

---

## 6. 创建并等待

1. 点 **Create Web Service**
2. 进入 `Logs` 页面看构建：`Cloning` → `Building` → `Deploying`
3. 首次大约 3~8 分钟（拉 `python:3.12-slim` 镜像）
4. 状态变成 **Live** 就成功了，网址在页面顶部：

   ```
   https://calculus-exam.onrender.com
   ```

---

## 7. 上线后马上验证这 5 项

- [ ] 打开 `https://calculus-exam.onrender.com/api/v1/health` → 应返回 `{"status": "ok", ...}`，并检查 `llm_mode` 是 `openai_compatible`、`questions` 是 `101`
- [ ] 打开首页，能正常显示界面
- [ ] 用 `admin` + 你在第 5 步设的密码登录成功
- [ ] 随便提一个知识库问题，回答正常，且**回答里看不到任何密钥**
- [ ] 自己注册一个普通账号试一次（模拟同学的使用路径）

---

## 8. 以后改了代码怎么更新

**Public Git Repository 方式不支持自动部署**，所以：

1. 在你自己的终端里 `git push` 到 Gitee（这个方式 Render 不会自动感知）
2. 回到 Render 服务页 → 右上角 **Manual Deploy** → **Deploy latest commit**

> 注意：每次重新部署，`/data/app.db` 会清空 —— 用户账号、试卷、成绩、错题本都没了（题库和知识库不丢，它们在 `server/seed/*.json` 里）。这是免费层没有持久磁盘的必然结果，详见 `README.md` 的「公网部署」章节。

---

## 9. 常见报错与对策

| 现象 | 原因 / 对策 |
|---|---|
| Connect 时报无法访问仓库 | Gitee 不被 Render 支持，或仓库不是公开的 → 确认公开；仍不行就转 GitHub 方案 |
| 构建成功但访问 502 / 一直转圈 | 多半是端口没绑对 → 确认没有手动设 `PORT`，程序会读 Render 注入的值 |
| 部署失败提示 health check | 检查 Health Check Path 是 `/api/v1/health`；看 Logs 里有没有 Python 报错 |
| 首页能开但提问报错 | 大概率 `CALCULUS_LLM_API_KEY` 填错或没填 → 回 Environment 改，改完会自动重新部署 |
| 15 分钟不访问后打开很慢 | 免费层会休眠，冷启动要几十秒，正常现象 |
| 国内打开经常超时 | Render 无国内节点，属于平台限制 → 改用 `start-public.ps1` 的 Cloudflare 隧道 |

---

## 如果连不上怎么办（备选）

1. **改用 GitHub 当代码源（最稳）**：在 GitHub 建一个仓库（Private 也行，Render 支持私有仓库），把本地代码 push 上去，然后用 Render 的 `New → Blueprint`，它会自动读项目里的 `render.yaml`，13 个环境变量只剩 2 个需要手填，而且以后 push 会自动部署。
2. **用 Cloudflare 隧道先顶上**：直接双击 `start-public.ps1`，2 分钟拿到 `https://xxxx.trycloudflare.com`，国内访问通常比 Render 快；缺点是电脑得开着、地址会变。

---

## 已知代价（提前接受，别到时候觉得是 bug）

- 免费层：0.1 CPU / 512 MB 内存，人多了会慢
- 15 分钟无人访问即休眠，下次访问冷启动几十秒
- 每次重新部署 `/data/app.db` 清空（账号、成绩、错题本会丢）
- 不能自动部署，改代码要手动 Redeploy
- `onrender.com` 在国内访问速度不稳定
