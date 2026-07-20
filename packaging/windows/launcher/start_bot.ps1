$ErrorActionPreference = "Stop"

Add-Type -AssemblyName PresentationFramework

$messages = Get-Content -LiteralPath (Join-Path $PSScriptRoot "messages.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$programName = [regex]::Unescape("\u7a0b\u5e8f")
$programRoot = Join-Path $packageRoot $programName
$exePath = Join-Path $programRoot "FeishuSalesBot.exe"
$logsRoot = Join-Path $programRoot "logs"
$pidPath = Join-Path $logsRoot "feishu_bot_launcher.pid"
$stdoutPath = Join-Path $logsRoot "startup_stdout.log"
$stderrPath = Join-Path $logsRoot "startup_stderr.log"

function Show-Message([string]$message, [string]$title, [string]$icon) {
    [System.Windows.MessageBox]::Show(
        $message,
        $title,
        [System.Windows.MessageBoxButton]::OK,
        ([System.Windows.MessageBoxImage]$icon)
    ) | Out-Null
}

if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    Show-Message $messages.incomplete_package $messages.start_failed_title "Error"
    exit 1
}

New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null

if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
    $savedPid = 0
    [void][int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$savedPid)
    if ($savedPid -gt 0) {
        $existing = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
        if ($null -ne $existing) {
            $existingPath = $null
            try { $existingPath = $existing.Path } catch { }
            if ($existingPath -and ([System.IO.Path]::GetFullPath($existingPath) -eq [System.IO.Path]::GetFullPath($exePath))) {
                Show-Message $messages.already_running $messages.title "Information"
                exit 0
            }
        }
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

try {
    $process = Start-Process `
        -FilePath $exePath `
        -WorkingDirectory $programRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
    Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII
    Start-Sleep -Seconds 4
    $process.Refresh()

    if ($process.HasExited) {
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        $details = ""
        foreach ($logPath in @($stderrPath, $stdoutPath)) {
            if (Test-Path -LiteralPath $logPath -PathType Leaf) {
                $tail = Get-Content -LiteralPath $logPath -Tail 12 -ErrorAction SilentlyContinue
                if ($tail) { $details += "`n" + ($tail -join "`n") }
            }
        }
        Show-Message ($messages.start_failed + $details) $messages.start_failed_title "Error"
        exit 1
    }

    Show-Message $messages.start_success $messages.start_success_title "Information"
} catch {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Show-Message ($messages.start_failed + "`n" + $_.Exception.Message) $messages.start_failed_title "Error"
    exit 1
}
