<#
  停止「微积分题库与考试模拟系统」

  用法：右键本文件 → 使用 PowerShell 运行

  会停掉：占用 8788 端口的网站进程，以及 cloudflared 隧道进程。
#>

$ErrorActionPreference = 'Continue'

function Write-Line($text, $color = 'Gray') {
    Write-Host $text -ForegroundColor $color
}

Write-Line ''
Write-Line '  停止服务' 'Cyan'
Write-Line '  ================================' 'Cyan'
Write-Line ''

$stopped = 0

# 1) 网站进程（用 netstat 而不是 Get-NetTCPConnection：后者在受限环境下会被拒绝）
$pids = @()
$lines = netstat -ano | Select-String ":8788" | Where-Object { $_.Line -match 'LISTENING' }
foreach ($l in $lines) {
    $parts = $l.Line.Trim() -split '\s+'
    if ($parts.Count -ge 5) { $pids += [int]$parts[4] }
}
$pids = $pids | Select-Object -Unique

if ($pids) {
    foreach ($p in $pids) {
        try {
            $proc = Get-Process -Id $p -ErrorAction Stop
            Stop-Process -Id $p -Force -ErrorAction Stop
            Write-Line ('  已停止网站进程 ' + $proc.ProcessName + ' (PID ' + $p + ')') 'Green'
            $stopped++
        } catch {
            Write-Line ('  [警告] 无法停止 PID ' + $p + '：' + $_.Exception.Message) 'Yellow'
        }
    }
} else {
    Write-Line '  8788 端口上没有正在运行的网站进程。' 'DarkGray'
}

# 2) 隧道进程
$tunnels = Get-Process cloudflared -ErrorAction SilentlyContinue
if ($tunnels) {
    foreach ($t in $tunnels) {
        try {
            Stop-Process -Id $t.Id -Force -ErrorAction Stop
            Write-Line ('  已停止隧道进程 cloudflared (PID ' + $t.Id + ')') 'Green'
            $stopped++
        } catch {
            Write-Line ('  [警告] 无法停止 cloudflared PID ' + $t.Id) 'Yellow'
        }
    }
} else {
    Write-Line '  没有正在运行的隧道进程。' 'DarkGray'
}

Write-Line ''
if ($stopped -gt 0) {
    Write-Line ('  完成，共停止 ' + $stopped + ' 个进程。') 'White'
} else {
    Write-Line '  没有需要停止的进程。' 'White'
}
Write-Line ''
Read-Host '  按回车键退出'
