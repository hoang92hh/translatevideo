param(
    [ValidateSet("Auto", "CPU", "CUDA12")]
    [string]$Profile = "Auto",
    [string]$ApplicationRoot = "",
    [switch]$IncludePiper,
    [switch]$IncludeMelo,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ApplicationRoot) {
    $ApplicationRoot = Split-Path -Parent $scriptRoot
}
$ApplicationRoot = [System.IO.Path]::GetFullPath($ApplicationRoot)
$appRoot = Join-Path $ApplicationRoot "app"
if (-not (Test-Path -LiteralPath (Join-Path $appRoot "pyproject.toml"))) {
    $appRoot = $ApplicationRoot
}
$environmentRoot = Join-Path $ApplicationRoot "environment"
$downloadsRoot = Join-Path $ApplicationRoot "downloads"
$modelsRoot = Join-Path $ApplicationRoot "models"
$mainRuntime = Join-Path $environmentRoot "main"
$pythonExe = Join-Path $mainRuntime "Scripts\python.exe"

foreach ($folder in @($environmentRoot, $downloadsRoot, $modelsRoot, (Join-Path $ApplicationRoot "projects"), (Join-Path $ApplicationRoot "reference"), (Join-Path $ApplicationRoot "extensions"), (Join-Path $ApplicationRoot "tools"))) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
}
$env:TRANSLANGUAGE_HOME = $ApplicationRoot
$env:PIP_CACHE_DIR = Join-Path $downloadsRoot "pip"
$env:HF_HOME = Join-Path $downloadsRoot "huggingface"

if ($Profile -eq "Auto") {
    $hasNvidia = $false
    $gpuName = ""
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $gpuName = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1).Trim()
        $hasNvidia = $LASTEXITCODE -eq 0
    }
    if ($gpuName -match "RTX\s*50") {
        Write-Warning "GPU $gpuName needs a newer CUDA profile; setup will use CPU."
        $Profile = "CPU"
    } else {
        $Profile = if ($hasNvidia) { "CUDA12" } else { "CPU" }
    }
}
Write-Host "TransLanguage profile: $Profile"

$basePython = ""
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $basePython = (& py -3.13 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
}
if (-not $basePython) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python Launcher and Winget were not found. Install Python 3.13 x64 and retry."
    }
    Write-Host "Installing Python 3.13 x64..."
    & winget install --exact --id Python.Python.3.13 --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Cannot install Python 3.13." }
    $basePython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
    if (-not (Test-Path -LiteralPath $basePython)) {
        throw "Python was installed but python.exe was not found. Restart Setup-TransLanguage.exe."
    }
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    & $basePython -m venv $mainRuntime
    if ($LASTEXITCODE -ne 0) { throw "Cannot create the main runtime." }
}

& $pythonExe -m pip install --upgrade pip "setuptools<82" wheel
if ($LASTEXITCODE -ne 0) { throw "Cannot update Python packaging tools." }

if ($Profile -eq "CUDA12") {
    $torchVersion = (& $pythonExe -c "import torch; print(torch.__version__)" 2>$null | Select-Object -First 1)
    if ($torchVersion -ne "2.11.0+cu126") {
        & $pythonExe -m pip install --force-reinstall --no-deps torch==2.11.0+cu126 torchvision==0.26.0+cu126 torchaudio==2.11.0+cu126 --index-url https://download.pytorch.org/whl/cu126
        if ($LASTEXITCODE -ne 0) { throw "Cannot install PyTorch CUDA 12.6." }
    }
    & $pythonExe -m pip install "$appRoot[cuda]"
    if ($LASTEXITCODE -ne 0) { throw "Cannot install TransLanguage CUDA dependencies." }
    $ortGpuVersion = (& $pythonExe -c "from importlib.metadata import version; print(version('onnxruntime-gpu'))" 2>$null | Select-Object -First 1)
    if ($ortGpuVersion -ne "1.20.2") {
        & $pythonExe -m pip install --force-reinstall --no-deps onnxruntime-gpu==1.20.2
        if ($LASTEXITCODE -ne 0) { throw "Cannot install ONNX Runtime GPU." }
    }
} else {
    $torchVersion = (& $pythonExe -c "import torch; print(torch.__version__)" 2>$null | Select-Object -First 1)
    if (-not $torchVersion -or -not $torchVersion.StartsWith("2.11.0")) {
        & $pythonExe -m pip install --force-reinstall torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
        if ($LASTEXITCODE -ne 0) { throw "Cannot install PyTorch CPU." }
    }
    & $pythonExe -m pip install "$appRoot[cpu]"
    if ($LASTEXITCODE -ne 0) { throw "Cannot install TransLanguage CPU dependencies." }
}

& $pythonExe -m pip check
if ($LASTEXITCODE -ne 0) { throw "The main runtime contains incompatible dependencies." }

if ($IncludePiper) {
    & (Join-Path $scriptRoot "setup_piper_runtime.ps1") -ApplicationRoot $ApplicationRoot
}
if ($IncludeMelo) {
    $device = if ($Profile -eq "CUDA12") { "cuda" } else { "cpu" }
    & (Join-Path $scriptRoot "setup_melo_runtime.ps1") -Device $device -ApplicationRoot $ApplicationRoot
}

$installation = @{
    installed_at = (Get-Date).ToString("o")
    profile = $Profile
    python = $pythonExe
    piper = [bool]$IncludePiper
    melo = [bool]$IncludeMelo
} | ConvertTo-Json
$installation | Set-Content -LiteralPath (Join-Path $environmentRoot "installation.json") -Encoding UTF8

Write-Host ""
Write-Host "TransLanguage installation completed."
Write-Host "Runtime: $mainRuntime"
Write-Host "Models:  $modelsRoot"
if (-not $NonInteractive) {
    Read-Host "Press Enter to close"
}
