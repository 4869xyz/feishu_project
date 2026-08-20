[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envPath = Join-Path $projectRoot ".env.meeting-minutes"
$specPath = Join-Path $PSScriptRoot "MeetingMinutesBot.spec"
$distRoot = Join-Path $projectRoot "dist"
$workRoot = Join-Path $projectRoot "build\pyinstaller-meeting"
$releaseRoot = Join-Path $projectRoot "release"
$packageName = [regex]::Unescape("\u5468\u4f8b\u4f1a\u7eaa\u8981\u673a\u5668\u4eba")
$packageRoot = Join-Path $releaseRoot $packageName
$programRoot = Join-Path $packageRoot ([regex]::Unescape("\u7a0b\u5e8f"))
$zipPath = Join-Path $releaseRoot "$packageName-Windows-x64.zip"
$docsRoot = Join-Path $projectRoot "docs\meeting_minutes"

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project virtual environment was not found: $pythonPath"
}
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "The current .env.meeting-minutes was not found."
}

function Get-RelativeSetting([string]$name) {
    $line = Get-Content -LiteralPath $envPath -Encoding UTF8 |
        Where-Object { $_ -match "^\s*$name\s*=" } |
        Select-Object -First 1
    if (-not $line) {
        throw "$name is missing from .env.meeting-minutes."
    }
    $value = (($line -split '=', 2)[1]).Trim().Trim('"').Trim("'")
    if ([System.IO.Path]::IsPathRooted($value)) {
        throw "$name must be relative for a portable package."
    }
    $fullPath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $value))
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        throw "The configured file was not found: $fullPath"
    }
    return [PSCustomObject]@{ Relative = $value; Full = $fullPath }
}

$peopleConfig = Get-RelativeSetting "MEETING_BOT_PEOPLE_CONFIG_PATH"
$template = Get-RelativeSetting "MEETING_BOT_TEMPLATE_PATH"

if ($SkipTests) {
    Write-Host "[1/5] Skipping tests (-SkipTests)."
} else {
    Write-Host "[1/5] Running tests..."
    & $pythonPath -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed; packaging stopped."
    }
}

Write-Host "[2/5] Preparing the pinned packaging tool..."
$buildRequirements = Join-Path $projectRoot "requirements-build.txt"

function Invoke-Native([string]$file, [string[]]$commandArguments) {
    # 原生命令写 stderr 时不应触发 $ErrorActionPreference = "Stop"，这里只看退出码。
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $file @commandArguments 2>&1 | Out-Null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

if ((Invoke-Native $pythonPath @("-c", "import PyInstaller")) -ne 0) {
    # 本项目的 .venv 可能由 uv 创建且不含 pip，两种安装方式都要支持。
    $installed = (Invoke-Native $pythonPath @(
        "-m", "pip", "install", "--disable-pip-version-check", "-r", $buildRequirements
    )) -eq 0
    if (-not $installed) {
        $uv = Get-Command uv -ErrorAction SilentlyContinue
        if (-not $uv) {
            throw "PyInstaller installation failed: neither pip nor uv is available."
        }
        Write-Host "      pip unavailable; installing with uv..."
        & $uv.Source pip install --python $pythonPath -r $buildRequirements
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller installation failed."
        }
    }
}

foreach ($generatedPath in @($workRoot, (Join-Path $distRoot "MeetingMinutesBot"), $packageRoot, $zipPath)) {
    if (Test-Path -LiteralPath $generatedPath) {
        Remove-Item -LiteralPath $generatedPath -Recurse -Force
    }
}

Write-Host "[3/5] Building the portable Windows application..."
& $pythonPath -m PyInstaller --noconfirm --clean --distpath $distRoot --workpath $workRoot $specPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

Write-Host "[4/5] Assembling the portable directory..."
New-Item -ItemType Directory -Path $programRoot -Force | Out-Null
Copy-Item -Path (Join-Path $distRoot "MeetingMinutesBot\*") -Destination $programRoot -Recurse -Force
Copy-Item -LiteralPath $envPath -Destination (Join-Path $programRoot ".env.meeting-minutes") -Force

foreach ($asset in @($peopleConfig, $template)) {
    $target = Join-Path $programRoot $asset.Relative
    New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $asset.Full -Destination $target -Force
}

# 发布包一律使用全新空数据，不携带任何历史提交、附件或生成的纪要。
foreach ($runtimeDir in @("data\meeting_minutes", "logs\meeting_minutes")) {
    New-Item -ItemType Directory -Path (Join-Path $programRoot $runtimeDir) -Force | Out-Null
}

$launcherTarget = Join-Path $packageRoot "launcher"
New-Item -ItemType Directory -Path $launcherTarget -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "launcher\*") -Destination $launcherTarget -Recurse -Force
Get-ChildItem -LiteralPath $PSScriptRoot -Filter "*.cmd" -File |
    Copy-Item -Destination $packageRoot -Force
Get-ChildItem -LiteralPath $PSScriptRoot -Filter "*.txt" -File |
    Copy-Item -Destination $packageRoot -Force

# 中文字面量在 Windows PowerShell 下会因脚本编码被破坏，统一用转义写法。
$guideNames = @(
    [regex]::Unescape("\u7528\u6237\u4f7f\u7528\u8bf4\u660e.md"),
    [regex]::Unescape("\u7ba1\u7406\u5458\u4f7f\u7528\u8bf4\u660e.md")
)
foreach ($guide in $guideNames) {
    $guidePath = Join-Path $docsRoot $guide
    if (-not (Test-Path -LiteralPath $guidePath -PathType Leaf)) {
        throw "The packaged guide was not found: $guidePath"
    }
    Copy-Item -LiteralPath $guidePath -Destination $packageRoot -Force
}

Write-Host "[5/5] Creating the ZIP release..."
Compress-Archive -LiteralPath $packageRoot -DestinationPath $zipPath -CompressionLevel Optimal

$fileCount = (Get-ChildItem -LiteralPath $packageRoot -Recurse -File).Count
$zipItem = Get-Item -LiteralPath $zipPath
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash

Write-Host ""
Write-Host "Portable release created:"
Write-Host "  Directory: $packageRoot"
Write-Host "  ZIP:       $zipPath"
Write-Host "  Files:     $fileCount"
Write-Host "  Bytes:     $($zipItem.Length)"
Write-Host "  SHA-256:   $zipHash"
Write-Warning "The release contains the current .env.meeting-minutes, App Secret and real open_id values. Keep it private."
Write-Warning "Only one machine may run this Feishu app at a time; stop the development instance before handing it over."
