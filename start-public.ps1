<#
  生成一个公网地址，让任何网络（手机流量、别人的电脑）都能访问

  用法：右键本文件 → 使用 PowerShell 运行

  原理：先在本地启动网站，再用 Cloudflare 的免费隧道把它映射到一个
        临时的 https 公网地址（不需要注册任何账号）。

  注意：
    · 这个地址是临时的，关掉窗口就失效，重新运行会换一个新地址
    · 地址是公开的，任何人拿到都能用；大模型调用有每日额度上限兜底
    · 想长期固定的地址，请部署到 Render 等平台（见 README）
#>

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Write-Line($text, $color = 'Gray') {
    Write-Host $text -ForegroundColor $color
}

function Find-Python {
    $cands = @()
    foreach ($name in 'python', 'python3') {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $cands += $cmd.Source }
    }
    $cands += 'C:\Users\ASUS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
    $cands += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    $cands += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
    foreach ($p in $cands) {
        if (-not $p -or -not (Test-Path $p)) { continue }
        try {
            $v = & $p -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v -and ([version]$v -ge [version]'3.8')) { return $p }
        } catch { }
    }
    return $null
}

function Find-Cloudflared {
    $cands = @(
        (Join-Path $root 'cloudflared.exe'),
        (Join-Path (Split-Path -Parent $root) 'bin\cloudflared.exe'),
        (Join-Path $env:LOCALAPPDATA 'cloudflared\cloudflared.exe')
    )
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

Write-Line ''
Write-Line '  生成公网地址' 'Cyan'
Write-Line '  ================================' 'Cyan'
Write-Line ''

$python = Find-Python
if (-not $python) {
    Write-Line '  [错误] 找不到 Python（需要 3.8+），请先运行 .\start.ps1 看提示。' 'Red'
    Read-Host '  按回车键退出'
    exit 1
}

$cf = Find-Cloudflared
if (-not $cf) {
    Write-Line '  未找到 cloudflared，正在从 Cloudflare 官方地址下载（约 53 MB）…' 'Yellow'
    $target = Join-Path $root 'cloudflared.exe'
    $url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
    try {
        $old = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing -TimeoutSec 600
        $ProgressPreference = $old
        $cf = $target
        Write-Line '  下载完成。' 'Green'
    } catch {
        Write-Line ('  [错误] 下载失败：' + $_.Exception.Message) 'Red'
        Write-Line '  可手动下载 cloudflared-windows-amd64.exe 放到本目录并改名为 cloudflared.exe' 'Yellow'
        Write-Line '  下载页：https://github.com/cloudflare/cloudflared/releases' 'Yellow'
        Read-Host '  按回车键退出'
        exit 1
    }
}
Write-Line ('  cloudflared: ' + $cf) 'DarkGray'

# 隧道指向本地 8788：先确保本地服务在跑
function Test-PortListening($port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect('127.0.0.1', $port)
        $client.Close()
        return $true
    } catch { return $false }
}

$serverProc = $null
if (-not (Test-PortListening 8788)) {
    Write-Line '  正在启动本地服务…' 'Green'
    $serverProc = Start-Process -FilePath $python `
        -ArgumentList @('server/app.py', '--port', '8788', '--host', '127.0.0.1') `
        -WorkingDirectory $root -PassThru -WindowStyle Minimized
    Start-Sleep -Seconds 2
} else {
    Write-Line '  本地服务已在运行，直接复用它。' 'DarkGray'
}

Write-Line ''
Write-Line '  正在建立隧道…（首次可能需要十几秒）' 'Green'
Write-Line ''
Write-Line '  下面出现 https://xxxx.trycloudflare.com 就是你的公网地址。' 'White'
Write-Line '  这个窗口不能关，关了地址就失效。' 'Yellow'
Write-Line ''

try {
    & $cf tunnel --url http://127.0.0.1:8788 --no-autoupdate
} finally {
    if ($serverProc -and -not $serverProc.HasExited) {
        Write-Line '  正在停止本地服务…' 'DarkGray'
        Stop-Process -Id $serverProc.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Line ''
    Write-Line '  公网地址已失效。' 'Yellow'
    Read-Host '  按回车键退出'
}
