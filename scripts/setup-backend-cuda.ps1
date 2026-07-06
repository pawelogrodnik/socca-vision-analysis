param(
    [string]$Python = "",
    [ValidateSet("cu118", "cu126", "cu128")]
    [string]$CudaIndex = "cu126",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $RepoRoot "backend"
$VenvDir = Join-Path $BackendDir ".venv-cuda"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $BackendDir "requirements.txt"

function Resolve-Python3Command {
    param([string]$RequestedPython)

    $Candidates = @()
    if ($RequestedPython) {
        $Candidates += ,@($RequestedPython)
    }
    $Candidates += ,@("py", "-3")
    $Candidates += ,@("python3")
    $Candidates += ,@("python")

    foreach ($Candidate in $Candidates) {
        $Exe = $Candidate[0]
        $Args = @()
        if ($Candidate.Count -gt 1) {
            $Args = $Candidate[1..($Candidate.Count - 1)]
        }
        try {
            $Version = & $Exe @Args -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>$null
            if ($LASTEXITCODE -eq 0 -and $Version -match "^3\.") {
                return ,$Candidate
            }
        } catch {
            continue
        }
    }
    throw "Python 3 was not found. Install Python 3.10+ or pass -Python C:\Path\To\python.exe."
}

$PythonCommand = Resolve-Python3Command -RequestedPython $Python
$PythonArgs = @()
if ($PythonCommand.Count -gt 1) {
    $PythonArgs = $PythonCommand[1..($PythonCommand.Count - 1)]
}

if ((Test-Path $VenvDir) -and $Force) {
    Remove-Item -LiteralPath $VenvDir -Recurse -Force
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating CUDA backend virtualenv at $VenvDir"
    & $PythonCommand[0] @PythonArgs -m venv $VenvDir
}

Write-Host "Upgrading pip"
& $VenvPython -m pip install --upgrade pip

Write-Host "Installing PyTorch CUDA wheels from https://download.pytorch.org/whl/$CudaIndex"
& $VenvPython -m pip install torch torchvision torchaudio --index-url "https://download.pytorch.org/whl/$CudaIndex"

Write-Host "Installing backend requirements"
& $VenvPython -m pip install -r $Requirements

Write-Host ""
Write-Host "CUDA backend environment is ready."
Write-Host "Verify with:"
Write-Host "  $VenvPython backend/scripts/verify_cuda.py"
Write-Host ""
Write-Host "If torch.cuda.is_available() is false, update the NVIDIA driver or rerun this script with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/setup-backend-cuda.ps1 -CudaIndex cu118 -Force"
