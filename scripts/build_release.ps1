param(
    [string]$OutputRoot = "",
    [switch]$IncludeLocalModels,
    [switch]$IncludePortableFFmpeg
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $repoRoot "release"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$target = Join-Path $OutputRoot "TransLanguage"
$expectedParent = [System.IO.Path]::GetFullPath($OutputRoot).TrimEnd('\') + '\'
$resolvedTarget = [System.IO.Path]::GetFullPath($target)
if (-not $resolvedTarget.StartsWith($expectedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Invalid build directory: $resolvedTarget"
}
if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target -Recurse -Force
}

$appTarget = Join-Path $target "app"
$buildRoot = Join-Path $OutputRoot ".build"
foreach ($folder in @($target, $appTarget, (Join-Path $target "environment"), (Join-Path $target "downloads"), (Join-Path $target "models"), (Join-Path $target "projects"), (Join-Path $target "tools"), (Join-Path $target "scripts"))) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
}

Copy-Item -LiteralPath (Join-Path $repoRoot "video_translator") -Destination $appTarget -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot "main.py") -Destination $appTarget
Copy-Item -LiteralPath (Join-Path $repoRoot "pyproject.toml") -Destination $appTarget
Copy-Item -LiteralPath (Join-Path $repoRoot "reference") -Destination (Join-Path $target "reference") -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot "extension") -Destination (Join-Path $target "extensions") -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot "huong_dan_su_dung") -Destination (Join-Path $target "huong_dan_su_dung") -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot "packaging\runtime-profiles.json") -Destination (Join-Path $target "runtime-profiles.json")
Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\setup_application.ps1") -Destination (Join-Path $target "scripts")
Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\setup_melo_runtime.ps1") -Destination (Join-Path $target "scripts")
Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\setup_piper_runtime.ps1") -Destination (Join-Path $target "scripts")

if ($IncludeLocalModels -and (Test-Path -LiteralPath (Join-Path $repoRoot "models"))) {
    Copy-Item -Path (Join-Path $repoRoot "models\*") -Destination (Join-Path $target "models") -Recurse -Force
}
if ($IncludePortableFFmpeg) {
    $ffmpeg = Get-Command ffmpeg -ErrorAction Stop
    $ffprobe = Get-Command ffprobe -ErrorAction Stop
    $ffmpegTarget = Join-Path $target "tools\ffmpeg\bin"
    New-Item -ItemType Directory -Force -Path $ffmpegTarget | Out-Null
    Copy-Item -LiteralPath $ffmpeg.Source -Destination $ffmpegTarget
    Copy-Item -LiteralPath $ffprobe.Source -Destination $ffmpegTarget
}

$buildPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $buildPython)) {
    throw "Build environment was not found: $buildPython"
}
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
& $buildPython -m PyInstaller --noconfirm --clean --onefile --windowed --name TransLanguage --distpath $target --workpath (Join-Path $buildRoot "launcher-work") --specpath $buildRoot (Join-Path $repoRoot "packaging\launcher.py")
if ($LASTEXITCODE -ne 0) { throw "Cannot build TransLanguage.exe" }
& $buildPython -m PyInstaller --noconfirm --clean --onefile --name Setup-TransLanguage --distpath $target --workpath (Join-Path $buildRoot "setup-work") --specpath $buildRoot (Join-Path $repoRoot "packaging\setup_launcher.py")
if ($LASTEXITCODE -ne 0) { throw "Cannot build Setup-TransLanguage.exe" }

Write-Host "Release created at: $target"
