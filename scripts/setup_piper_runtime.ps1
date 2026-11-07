param(
    [string]$PythonVersion = "3.13"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $repoRoot ".runtimes\piper"
$pythonExe = Join-Path $runtimeRoot "Scripts\python.exe"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher (py.exe) was not found. Install Python $PythonVersion x64 first."
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    & py "-$PythonVersion" -m venv $runtimeRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot create Piper runtime with Python $PythonVersion."
    }
}

& $pythonExe -m pip install --upgrade pip wheel
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip tooling." }

& $pythonExe -m pip install "piper-tts==1.8.0"
if ($LASTEXITCODE -ne 0) { throw "Failed to install Piper TTS." }

Write-Host "Piper TTS runtime installed at $runtimeRoot"
Write-Host "The application will automatically detect $pythonExe"
Write-Host "Download voice .onnx and matching .onnx.json files separately, then select them in Step 4."
