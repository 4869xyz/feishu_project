$ErrorActionPreference = "Stop"

Add-Type -AssemblyName PresentationFramework

$messages = Get-Content -LiteralPath (Join-Path $PSScriptRoot "messages.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$programName = [regex]::Unescape("\u7a0b\u5e8f")
$logsRoot = Join-Path $packageRoot "$programName\logs"
$runtimeLog = Join-Path $logsRoot "feishu_bot_listener.log"
$stderrLog = Join-Path $logsRoot "startup_stderr.log"
$stdoutLog = Join-Path $logsRoot "startup_stdout.log"

$logPath = @($runtimeLog, $stderrLog, $stdoutLog) |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1

if (-not $logPath) {
    [System.Windows.MessageBox]::Show(
        $messages.no_logs,
        $messages.title,
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Information
    ) | Out-Null
    exit 0
}

Start-Process -FilePath "notepad.exe" -ArgumentList @($logPath)
