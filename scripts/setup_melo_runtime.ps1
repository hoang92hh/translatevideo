param(
    [ValidateSet("cpu", "cuda")]
    [string]$Device = "cuda"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $repoRoot ".runtimes\melo"
$pythonExe = Join-Path $runtimeRoot "Scripts\python.exe"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher (py.exe) was not found. Install Python 3.9 x64 first."
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    & py -3.9 -m venv $runtimeRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot create runtime. OpenVoice requires Python 3.9."
    }
}

& $pythonExe -m pip install --upgrade pip "setuptools<81" wheel
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip tooling." }
if ($Device -eq "cuda") {
    # CUDA 11.8 supports Pascal/GTX 10xx GPUs such as GTX 1060.
    & $pythonExe -m pip install torch==2.1.2 torchaudio==2.1.2 --index-url https://download.pytorch.org/whl/cu118
} else {
    & $pythonExe -m pip install torch==2.1.2 torchaudio==2.1.2 --index-url https://download.pytorch.org/whl/cpu
}
if ($LASTEXITCODE -ne 0) { throw "Failed to install PyTorch runtime." }
& $pythonExe -m pip install "av>=11,<19" "faster-whisper>=1.2.1,<2"
if ($LASTEXITCODE -ne 0) { throw "Failed to install Faster Whisper compatibility packages." }
& $pythonExe -m pip install librosa==0.9.1 pydub==0.25.1 wavmark==0.0.3 numpy==1.22.0 eng_to_ipa==0.0.2 inflect==7.0.0 unidecode==1.3.7 whisper-timestamped==1.14.2 pypinyin==0.50.0 cn2an==0.5.22 jieba==0.42.1 gradio==3.48.0 langid==1.1.6
if ($LASTEXITCODE -ne 0) { throw "Failed to install OpenVoice dependencies." }
& $pythonExe -m pip install --no-deps "git+https://github.com/myshell-ai/OpenVoice.git"
if ($LASTEXITCODE -ne 0) { throw "Failed to install OpenVoice." }
& $pythonExe -m pip install "git+https://github.com/myshell-ai/MeloTTS.git"
if ($LASTEXITCODE -ne 0) { throw "Failed to install MeloTTS." }
& $pythonExe -m unidic download
if ($LASTEXITCODE -ne 0) { throw "Failed to download UniDic." }

$checkpointFolder = Join-Path $runtimeRoot "checkpoints_v2"
if (-not (Test-Path -LiteralPath $checkpointFolder)) {
    & $pythonExe -c "from huggingface_hub import snapshot_download; snapshot_download('myshell-ai/OpenVoiceV2', local_dir=r'$checkpointFolder')"
    if ($LASTEXITCODE -ne 0) { throw "Failed to download OpenVoice V2 checkpoints." }
}

Write-Host "MeloTTS/OpenVoice runtime installed at $runtimeRoot"
Write-Host "The application will automatically detect $pythonExe"
