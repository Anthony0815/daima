<#
  启动「微积分题库与考试模拟系统」

  用法：右键本文件 → 使用 PowerShell 运行
       或在 PowerShell 里执行  .\start.ps1

  启动后：
    本机访问    http://127.0.0.1:8788/
    手机访问    http://<本机局域网IP>:8788/   （手机要连同一个 Wi-Fi）
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
    # 本机 DSH 运行环境自带的 Python（无 PATH 时的兜底）
    $cands += 'C:\Users\ASUS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
    $cands += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    $cands += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
    $cands += 'C:\Python312\python.exe'
    $cands += 'C:\Python311\python.exe'

    foreach ($p in $cands) {
        if (-not $p -or -not (Test-Path $p)) { continue }
        try {
            $v = & $p -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v -and ([version]$v -ge [version]'3.8')) { return $p }
        } catch { }
    }
    return $null
}

function Test-PortListening($port) {
    # 不用 Get-NetTCPConnection：它在受限环境（如本机沙箱）里会被拒绝，
    # 而且 .NET 的直连测试在任何 Windows 上都能用。
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect('127.0.0.1', $port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

Write-Line ''
Write-Line '  微积分题库 · 考试模拟系统' 'Cyan'
Write-Line '  ================================' 'Cyan'
Write-Line ''

$python = Find-Python
if (-not $python) {
    Write-Line '  [错误] 找不到 Python（需要 3.8 或更高版本）' 'Red'
    Write-Line ''
    Write-Line '  请先安装 Python：https://www.python.org/downloads/'
    Write-Line '  安装时务必勾选 "Add Python to PATH"'
    Write-Line ''
    Read-Host '  按回车键退出'
    exit 1
}
Write-Line ("  Python: " + $python) 'DarkGray'

# 端口被占用时给出明确提示，而不是抛一堆看不懂的错
if (Test-PortListening 8788) {
    Write-Line ''
    Write-Line '  [提示] 8788 端口已被占用——服务可能已经在运行了。' 'Yellow'
    Write-Line '         直接打开 http://127.0.0.1:8788/ 看看。' 'Yellow'
    Write-Line '         想重启请先运行 .\stop.ps1' 'Yellow'
    Write-Line ''
    Read-Host '  按回车键退出'
    exit 0
}

# 找出局域网 IP，方便手机访问
$lanIp = $null
try {
    $sock = New-Object System.Net.Sockets.UdpClient
    $sock.Connect('223.5.5.5', 80)
    $lanIp = $sock.Client.LocalEndPoint.Address.ToString()
    $sock.Close()
} catch { }

Write-Line ''
Write-Line '  启动中…' 'Green'
Write-Line ''
Write-Line '  ┌─────────────────────────────────────────────┐' 'DarkGray'
Write-Line '  │  本机访问： http://127.0.0.1:8788/          │' 'White'
if ($lanIp) {
    $line = '  │  手机访问： http://' + $lanIp + ':8788/'
    Write-Line ($line.PadRight(47) + '│') 'White'
}
Write-Line '  └─────────────────────────────────────────────┘' 'DarkGray'
Write-Line ''
Write-Line '  管理员账号： admin' 'DarkGray'
Write-Line '  （密码见你设置时记下的那个；忘了就用 tools\reset_password.py 重置）' 'DarkGray'
Write-Line ''
Write-Line '  关闭这个窗口，或在窗口里按 Ctrl+C，即可停止服务。' 'DarkGray'
Write-Line ''

& $python server/app.py --port 8788 --host 0.0.0.0

Write-Line ''
Write-Line '  服务已停止。' 'Yellow'
Read-Host '  按回车键退出'
