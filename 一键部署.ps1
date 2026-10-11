<#
  一键部署：把项目推送到 GitHub，并把 Render 要填的值准备好
  ------------------------------------------------------------------
  这个脚本会做：
    1. 检查 llm.env 有没有被 git 跟踪（防止密钥泄露）
    2. 如果 llm.env 里没有管理员密码，自动生成一个强密码并写进去
    3. 把代码推送到 https://github.com/Anthony0815/daima.git
    4. 把 API 密钥复制到剪贴板（不显示在屏幕上）
    5. 打开 Render 控制台，并打印剩下的三次点击该点哪

  用法：
    · 右键本文件 → 「使用 PowerShell 运行」    （真正执行）
    · 只检查不动手： .\一键部署.ps1 -Check
#>
param([switch]$Check)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$REPO   = 'https://github.com/Anthony0815/daima.git'
$REMOTE = 'origin'
$LB     = 'master'   # 本地分支
$RB     = 'master'   # 远程分支（代码在 master 上；main 分支只有你上传的压缩包，别用）
$KEYFILE = Join-Path $root 'llm.env'

function Line($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }

Line ''
Line '  一键部署：推送 GitHub + 准备 Render 要填的值' 'Cyan'
Line '  ============================================================' 'Cyan'
Line ''

# ---------- 1) 找 git ----------
$git = $null
$cmd = Get-Command git -ErrorAction SilentlyContinue
if ($cmd) { $git = $cmd.Source }
if (-not $git) {
    foreach ($p in 'C:\Program Files\Git\cmd\git.exe', 'C:\Program Files\Git\bin\git.exe') {
        if (Test-Path $p) { $git = $p; break }
    }
}
if (-not $git) {
    Line '  [错误] 找不到 git。请先安装 Git，或用新开的 PowerShell 窗口重试。' 'Red'
    Read-Host '  按回车退出'; exit 1
}
Line ('  git: ' + $git) 'DarkGray'

# ---------- 2) 安全检查：密钥文件不能进 Git ----------
$tracked = & $git ls-files
if ($tracked -contains 'llm.env') {
    Line ''
    Line '  [危险] llm.env 已经被 git 跟踪，推送会把密钥传到公开仓库！' 'Red'
    Line '         请先执行：  git rm --cached llm.env' 'Yellow'
    Read-Host '  按回车退出'; exit 1
}
Line '  [检查通过] llm.env 未被跟踪，密钥不会进 GitHub' 'Green'

# ---------- 3) 读取 llm.env ----------
if (-not (Test-Path $KEYFILE)) {
    Line ('  [错误] 找不到 ' + $KEYFILE) 'Red'
    Read-Host '  按回车退出'; exit 1
}
$apiKey = $null; $adminPw = $null
foreach ($ln in (Get-Content $KEYFILE -Encoding UTF8)) {
    if ($ln -match '^\s*CALCULUS_LLM_API_KEY\s*=\s*(.+)$') {
        $apiKey = $Matches[1].Trim().Trim('"').Trim("'")
    }
    if ($ln -match '^\s*CALCULUS_ADMIN_PASSWORD\s*=\s*(.+)$') {
        $adminPw = $Matches[1].Trim().Trim('"').Trim("'")
    }
}
if (-not $apiKey) {
    Line '  [错误] llm.env 里没有 CALCULUS_LLM_API_KEY，无法接通大模型。' 'Red'
    Read-Host '  按回车退出'; exit 1
}
Line ('  [检查通过] 读到 DeepSeek 密钥（长度 ' + $apiKey.Length + '，不回显）') 'Green'

# ---------- 4) 管理员密码 ----------
if ($adminPw) {
    Line '  [检查通过] llm.env 里已有 CALCULUS_ADMIN_PASSWORD' 'Green'
} elseif ($Check) {
    Line '  [待处理] llm.env 里没有 CALCULUS_ADMIN_PASSWORD，正式运行时会自动生成一个' 'Yellow'
} else {
    $chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    $rand = -join (1..10 | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    $adminPw = 'Calc-' + $rand + '7'
    $text = [System.IO.File]::ReadAllText($KEYFILE, [System.Text.Encoding]::UTF8)
    if (-not $text.EndsWith("`n")) { $text += "`r`n" }
    $text += 'CALCULUS_ADMIN_PASSWORD=' + $adminPw + "`r`n"
    [System.IO.File]::WriteAllText($KEYFILE, $text, (New-Object System.Text.UTF8Encoding($false)))
    Line '  [已写入] llm.env 里原本没有管理员密码，已自动生成并写入。' 'Yellow'
}

if ($adminPw) {
    Line ('  [管理员密码] ' + $adminPw) 'White'
    Line '         ↑ 这是你线上用 admin 登录的密码，请先记下来' 'Yellow'
}

# ---------- 5) 配置远程 ----------
$remotes = & $git remote
if ($remotes -contains $REMOTE) {
    & $git remote set-url $REMOTE $REPO
} else {
    & $git remote add $REMOTE $REPO
}
Line ('  远程 ' + $REMOTE + ' -> ' + $REPO) 'DarkGray'
$branch = & $git rev-parse --abbrev-ref HEAD
$count  = & $git rev-list --count HEAD
Line ('  本地分支 ' + $branch + '（' + $count + ' 个提交待推送）') 'DarkGray'
Line ''

if ($Check) {
    Line '  [仅检查模式] 什么都没推送，也没改任何文件。' 'Yellow'
    Read-Host '  按回车退出'; exit 0
}

# ---------- 6) 推送 ----------
Line '  正在推送…第一次会弹出浏览器让你登录 GitHub 授权，请完成登录。' 'Green'
Line ''
$ErrorActionPreference = 'Continue'
& $git push -u $REMOTE ($LB + ':' + $RB) 2>&1 | ForEach-Object { Write-Host ('  ' + $_) }
$code = $LASTEXITCODE
Line ''

if ($code -ne 0) {
    Line '  [失败] 推送没成功，对号入座：' 'Red'
    Line '   - Authentication failed    : 浏览器授权窗口没走完，重跑一次并完成登录' 'Yellow'
    Line '   - Repository not found     : 仓库名或账号不对，确认 https://github.com/Anthony0815/daima 能打开' 'Yellow'
    Line '   - Failed to connect / 超时 : 你的网络到 GitHub 不通，需要代理：' 'Yellow'
    Line '       设置： git config --global http.proxy http://127.0.0.1:7890   （端口换成你自己的）' 'Yellow'
    Line '       取消： git config --global --unset http.proxy' 'Yellow'
    Line '   - rejected / non-fast-forward : 远程有本地没有的提交，把报错发我' 'Yellow'
    Line ''
    Read-Host '  按回车退出'; exit 1
}

# ---------- 7) 成功：准备 Render 要填的值 ----------
$clip = $true
try { Set-Clipboard -Value $apiKey } catch { $clip = $false }

Line '  [成功] 代码已推送到 GitHub！' 'Green'
Line ''
Line '  两个要填进 Render 的值：' 'Cyan'
if ($clip) {
    Line '     CALCULUS_LLM_API_KEY      -> 已复制到剪贴板，到 Render 的输入框里直接 Ctrl+V' 'White'
} else {
    Line '     CALCULUS_LLM_API_KEY      -> 打开 llm.env，复制 CALCULUS_LLM_API_KEY= 后面那一串' 'White'
}
Line ('     CALCULUS_ADMIN_PASSWORD   -> ' + $adminPw) 'White'
Line '       （这个密码请记好，是你线上用 admin 登录的密码）' 'Yellow'
Line ''
Line '  接下来在浏览器里点三下（我已经帮你打开 Render 了）：' 'Cyan'
Line '    1) 右上角 New -> Blueprint' 'White'
Line '    2) 在列表里找到 daima 仓库，点 Connect' 'White'
Line '       （第一次会要求授权 Render 访问你的 GitHub，点同意即可）' 'White'
Line '    3) 它会自动读 render.yaml，并弹出上面那两个要填的值：' 'White'
Line '         第一个框 Ctrl+V，第二个框照抄上面的密码，然后点 Deploy Blueprint' 'White'
Line ''
Line '  部署约 3~8 分钟。完成后打开这个地址验证 DeepSeek 助教是否接通：' 'Cyan'
Line '    https://calculus-exam.onrender.com/api/v1/health' 'White'
Line '    看到 "llm_mode": "openai_compatible" 就是接通了；' 'White'
Line '    若是 "keyword_template"，说明密钥没填对，回 Render 的 Environment 改。' 'White'
Line ''
Line '  提醒：Render 免费层每次重新部署会清空 /data/app.db（账号、成绩会丢，题库不会）。' 'DarkGray'
Line ''

Start-Process 'https://dashboard.render.com/'
Read-Host '  按回车退出'
