<#
  一键把「微积分题库与考试模拟系统」推送到 GitHub 仓库
  仓库：https://github.com/Anthony0815/daima.git

  用法：
    · 右键本文件 → 「使用 PowerShell 运行」   （真正推送）
    · 只检查不推送：在 PowerShell 里执行  .\推送到GitHub.ps1 -Check

  说明：
    · 本地分支 master 会推到 GitHub 的默认分支 main（该仓库默认分支就是 main）
    · 推送前会检查 llm.env（密钥文件）有没有被 git 跟踪，防止密钥泄露
#>
param([switch]$Check)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$REPO          = 'https://github.com/Anthony0815/daima.git'
$REMOTE_NAME   = 'github'
$LOCAL_BRANCH  = 'master'   # 本地（和 Gitee）用的分支
$REMOTE_BRANCH = 'main'     # GitHub 仓库的默认分支

function Line($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }

Line ''
Line '  推送到 GitHub' 'Cyan'
Line '  ================================' 'Cyan'
Line ''

# ---- 1) 找 git（新装的 Git 在旧窗口里可能认不到，所以直接找安装路径）----
$git = $null
$cmd = Get-Command git -ErrorAction SilentlyContinue
if ($cmd) { $git = $cmd.Source }
if (-not $git) {
    foreach ($p in 'C:\Program Files\Git\cmd\git.exe', 'C:\Program Files\Git\bin\git.exe') {
        if (Test-Path $p) { $git = $p; break }
    }
}
if (-not $git) {
    Line '  [错误] 找不到 git。请先安装 Git，或用新开的 PowerShell 窗口再运行本脚本。' 'Red'
    Read-Host '  按回车退出'
    exit 1
}
Line ('  git: ' + $git) 'DarkGray'

# ---- 2) 安全检查：密钥文件绝不能被推送 ----
$tracked = & $git ls-files
if ($tracked -contains 'llm.env') {
    Line ''
    Line '  [危险] llm.env 已经被 git 跟踪，推送会把你的密钥传到公开仓库！' 'Red'
    Line '         请先执行：  git rm --cached llm.env' 'Yellow'
    Line '         然后重新运行本脚本。' 'Yellow'
    Read-Host '  按回车退出'
    exit 1
}
Line '  [检查通过] llm.env 未被跟踪，密钥不会上传。' 'Green'

# ---- 3) 配置远程地址（重复运行也安全）----
$remotes = & $git remote
if ($remotes -contains $REMOTE_NAME) {
    & $git remote set-url $REMOTE_NAME $REPO
} else {
    & $git remote add $REMOTE_NAME $REPO
}
Line ('  远程 ' + $REMOTE_NAME + ' -> ' + $REPO) 'DarkGray'

$branch = & $git rev-parse --abbrev-ref HEAD
$count  = & $git rev-list --count HEAD
Line ('  本地分支 ' + $branch + '（' + $count + ' 个提交待推送）') 'DarkGray'
Line ''

if ($Check) {
    Line '  [仅检查模式] 没有推送任何东西。' 'Yellow'
    Read-Host '  按回车退出'
    exit 0
}

# ---- 4) 推送 ----
Line '  正在推送…第一次会弹出浏览器让你登录 GitHub 授权。' 'Green'
Line ''
$ErrorActionPreference = 'Continue'
& $git push -u $REMOTE_NAME ($LOCAL_BRANCH + ':' + $REMOTE_BRANCH) 2>&1 | ForEach-Object { Write-Host ('  ' + $_) }
$code = $LASTEXITCODE
Line ''

if ($code -eq 0) {
    Line '  [成功] 已推送！' 'Green'
    Line '  仓库地址：https://github.com/Anthony0815/daima' 'White'
    Line '  请打开确认：文件列表里应该没有 llm.env。' 'White'
    Line ''
    Line '  下一步（Render 部署）：' 'Cyan'
    Line '   New -> Blueprint -> 选 daima 仓库 -> 填 CALCULUS_LLM_API_KEY 和 CALCULUS_ADMIN_PASSWORD -> Apply' 'Gray'
} else {
    Line '  [失败] 推送没成功，对号入座：' 'Red'
    Line '   - Repository not found     : 仓库名或账号不对，确认 https://github.com/Anthony0815/daima 能打开' 'Yellow'
    Line '   - Authentication failed    : 浏览器授权没完成，重跑一次并完成登录' 'Yellow'
    Line '   - Failed to connect / 超时 : 你的网络到 GitHub 不通（国内常见），需要代理：' 'Yellow'
    Line '       设置代理： git config --global http.proxy http://127.0.0.1:7890   （端口换成你自己的）' 'Yellow'
    Line '       取消代理： git config --global --unset http.proxy' 'Yellow'
    Line '   - rejected / non-fast-forward : 远程有本地没有的提交，改用 git pull github main --allow-unrelated-histories' 'Yellow'
}
Line ''
Read-Host '  按回车退出'
