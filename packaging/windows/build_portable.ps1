[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envPath = Join-Path $projectRoot ".env"
$specPath = Join-Path $PSScriptRoot "FeishuSalesBot.spec"
$distRoot = Join-Path $projectRoot "dist"
$workRoot = Join-Path $projectRoot "build\pyinstaller"
$releaseRoot = Join-Path $projectRoot "release"
$packageName = [regex]::Unescape("\u98de\u4e66\u9500\u552e\u6c47\u603b\u673a\u5668\u4eba")
$packageRoot = Join-Path $releaseRoot $packageName
$programRoot = Join-Path $packageRoot ([regex]::Unescape("\u7a0b\u5e8f"))
$zipPath = Join-Path $releaseRoot "$packageName-Windows-x64.zip"

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project virtual environment was not found: $pythonPath"
}
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "The current project .env was not found."
}

$templateLine = Get-Content -LiteralPath $envPath -Encoding UTF8 |
    Where-Object { $_ -match '^\s*FEISHU_SALES_TEMPLATE_PATH\s*=' } |
    Select-Object -First 1
if (-not $templateLine) {
    throw "FEISHU_SALES_TEMPLATE_PATH is missing from .env."
}
$templateRelativePath = (($templateLine -split '=', 2)[1]).Trim().Trim('"').Trim("'")
if ([System.IO.Path]::IsPathRooted($templateRelativePath)) {
    throw "FEISHU_SALES_TEMPLATE_PATH must be relative for a portable package."
}
$templatePath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $templateRelativePath))
if (-not (Test-Path -LiteralPath $templatePath -PathType Leaf)) {
    throw "The configured workbook template was not found: $templatePath"
}

Write-Host "[1/5] Running tests..."
& $pythonPath -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed; packaging stopped."
}

Write-Host "[2/5] Preparing the pinned packaging tool..."
& $pythonPath -m pip install --disable-pip-version-check -r (Join-Path $projectRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller installation failed."
}

foreach ($generatedPath in @($workRoot, (Join-Path $distRoot "FeishuSalesBot"), $packageRoot, $zipPath)) {
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
Copy-Item -Path (Join-Path $distRoot "FeishuSalesBot\*") -Destination $programRoot -Recurse -Force
Copy-Item -LiteralPath $envPath -Destination (Join-Path $programRoot ".env") -Force

$templateTarget = Join-Path $programRoot $templateRelativePath
New-Item -ItemType Directory -Path (Split-Path $templateTarget -Parent) -Force | Out-Null
Copy-Item -LiteralPath $templatePath -Destination $templateTarget -Force

foreach ($runtimeDir in @("data\inbox", "data\archive", "data\aggregation", "logs")) {
    New-Item -ItemType Directory -Path (Join-Path $programRoot $runtimeDir) -Force | Out-Null
}

$launcherTarget = Join-Path $packageRoot "launcher"
New-Item -ItemType Directory -Path $launcherTarget -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "launcher\*") -Destination $launcherTarget -Recurse -Force
Get-ChildItem -LiteralPath $PSScriptRoot -Filter "*.cmd" -File |
    Copy-Item -Destination $packageRoot -Force
Get-ChildItem -LiteralPath $PSScriptRoot -Filter "*.txt" -File |
    Copy-Item -Destination $packageRoot -Force

Write-Host "[5/5] Creating the ZIP release..."
Compress-Archive -LiteralPath $packageRoot -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host ""
Write-Host "Portable release created:"
Write-Host "  Directory: $packageRoot"
Write-Host "  ZIP:       $zipPath"
Write-Warning "The release contains the current .env and Feishu App Secret. Keep it private."
