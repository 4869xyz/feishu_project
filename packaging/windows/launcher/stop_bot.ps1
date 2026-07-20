$ErrorActionPreference = "Stop"

Add-Type -AssemblyName PresentationFramework

$messages = Get-Content -LiteralPath (Join-Path $PSScriptRoot "messages.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$programName = [regex]::Unescape("\u7a0b\u5e8f")
$programRoot = Join-Path $packageRoot $programName
$exePath = Join-Path $programRoot "FeishuSalesBot.exe"
$pidPath = Join-Path $programRoot "logs\feishu_bot_launcher.pid"

function Show-Message([string]$message, [string]$title, [string]$icon) {
    [System.Windows.MessageBox]::Show(
        $message,
        $title,
        [System.Windows.MessageBoxButton]::OK,
        ([System.Windows.MessageBoxImage]$icon)
    ) | Out-Null
}

if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
    Show-Message $messages.not_running $messages.title "Information"
    exit 0
}

$savedPid = 0
[void][int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$savedPid)
$process = if ($savedPid -gt 0) { Get-Process -Id $savedPid -ErrorAction SilentlyContinue } else { $null }

if ($null -eq $process) {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Show-Message $messages.not_running $messages.title "Information"
    exit 0
}

$processPath = $null
try { $processPath = $process.Path } catch { }
if (-not $processPath -or ([System.IO.Path]::GetFullPath($processPath) -ne [System.IO.Path]::GetFullPath($exePath))) {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Show-Message $messages.pid_mismatch $messages.stop_failed_title "Warning"
    exit 1
}

try {
    Stop-Process -Id $savedPid -Force
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Show-Message $messages.stop_success $messages.stop_success_title "Information"
} catch {
    Show-Message ($messages.stop_failed_title + "`n" + $_.Exception.Message) $messages.stop_failed_title "Error"
    exit 1
}
