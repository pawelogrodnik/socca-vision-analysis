param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendPython = Join-Path $RepoRoot "backend\.venv-cuda\Scripts\python.exe"

if (-not (Test-Path $BackendPython)) {
    throw "CUDA backend venv not found: $BackendPython. Run scripts/setup-backend-cuda.ps1 first."
}

$env:YOLO_CONFIG_DIR = Join-Path $RepoRoot "backend\storage\.ultralytics"
$env:MPLCONFIGDIR = Join-Path $RepoRoot "backend\storage\.matplotlib"
$env:TORCH_HOME = Join-Path $RepoRoot "backend\storage\.torch"
$env:ORLIK_STORAGE_DIR = Join-Path $RepoRoot "backend\storage"
$env:ORLIK_APP_MODE = if ($env:ORLIK_APP_MODE) { $env:ORLIK_APP_MODE } else { "local-analysis" }
$env:ORLIK_PUBLISH_TARGET = if ($env:ORLIK_PUBLISH_TARGET) { $env:ORLIK_PUBLISH_TARGET } else { "local-json" }

New-Item -ItemType Directory -Force $env:YOLO_CONFIG_DIR, $env:MPLCONFIGDIR, $env:TORCH_HOME | Out-Null

& $BackendPython -m uvicorn app.main:app --app-dir backend --host $HostAddress --port $Port
