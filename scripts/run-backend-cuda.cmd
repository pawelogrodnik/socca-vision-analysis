@echo off
setlocal

set "REPO_ROOT=%~dp0.."
pushd "%REPO_ROOT%"

set "YOLO_CONFIG_DIR=%REPO_ROOT%\backend\storage\.ultralytics"
set "MPLCONFIGDIR=%REPO_ROOT%\backend\storage\.matplotlib"
set "TORCH_HOME=%REPO_ROOT%\backend\storage\.torch"
set "ORLIK_STORAGE_DIR=%REPO_ROOT%\backend\storage"
if "%ORLIK_APP_MODE%"=="" set "ORLIK_APP_MODE=local-analysis"
if "%ORLIK_PUBLISH_TARGET%"=="" set "ORLIK_PUBLISH_TARGET=local-json"
if not exist "%REPO_ROOT%\backend\storage\logs" mkdir "%REPO_ROOT%\backend\storage\logs"

if not exist "%REPO_ROOT%\backend\.venv-cuda\Scripts\python.exe" (
  echo CUDA backend venv not found. Run scripts\setup-backend-cuda.ps1 first.
  exit /b 1
)

"%REPO_ROOT%\backend\.venv-cuda\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 >> "%REPO_ROOT%\backend\storage\logs\native-backend-cuda.out.log" 2>> "%REPO_ROOT%\backend\storage\logs\native-backend-cuda.err.log"

popd
