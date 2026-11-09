param(
    [ValidateSet("app", "setup")]
    [string]$Target = "app",
    [string]$Python = ".\environment\app\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
if (-not (Test-Path -LiteralPath $Python)) { throw "Không tìm thấy Python build: $Python" }

& $Python -m pip show pyinstaller | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Cài PyInstaller trước: $Python -m pip install pyinstaller" }

$buildRoot = Join-Path $repoRoot "build_application"
$distRoot = Join-Path $buildRoot "release"
New-Item -ItemType Directory -Force -Path $distRoot | Out-Null

if ($Target -eq "app") {
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name translatevideo `
        --icon (Join-Path $repoRoot "video_translator\assets\translanguage_icon.ico") `
        --add-data "$(Join-Path $repoRoot 'video_translator');video_translator" `
        --distpath $distRoot --workpath (Join-Path $buildRoot "work_app") `
        --specpath $buildRoot (Join-Path $repoRoot "scripts\translatevideo_launcher.py")
    if ($LASTEXITCODE -ne 0) { throw "Build translatevideo.exe thất bại" }
} else {
    & $Python -m PyInstaller --noconfirm --clean --onefile --console --name setup `
        --add-data "$(Join-Path $repoRoot 'pyproject.toml');." `
        --distpath $distRoot --workpath (Join-Path $buildRoot "work_setup") `
        --specpath $buildRoot (Join-Path $repoRoot "scripts\translatevideo_setup.py")
    if ($LASTEXITCODE -ne 0) { throw "Build setup.exe thất bại" }
}

foreach ($name in @("extension", "huong_dan_su_dung", "reference")) {
    if (-not (Test-Path -LiteralPath (Join-Path $distRoot $name))) {
        Copy-Item -LiteralPath (Join-Path $repoRoot $name) -Destination (Join-Path $distRoot $name) -Recurse
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $distRoot "reference\prompt_template") | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot "reference\prompt_template\prompt_rut_gon.md") `
    -Destination (Join-Path $distRoot "reference\prompt_template\prompt_rut_gon.md") -Force
$videoPrompts = Join-Path $distRoot "reference\prompt_video"
New-Item -ItemType Directory -Force -Path $videoPrompts | Out-Null
Copy-Item -Path (Join-Path $repoRoot "reference\prompt_video\*") -Destination $videoPrompts -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "api_keys.env.example") -Destination (Join-Path $distRoot "api_keys.env.example") -Force
if (-not (Test-Path -LiteralPath (Join-Path $distRoot "tool"))) {
    Copy-Item -LiteralPath (Join-Path $repoRoot "tools") -Destination (Join-Path $distRoot "tool") -Recurse
}
New-Item -ItemType Directory -Force -Path (Join-Path $distRoot "models") | Out-Null
Write-Host "Đã cập nhật $Target tại $distRoot"
