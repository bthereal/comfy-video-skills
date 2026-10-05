@echo off
rem Starts the local ComfyUI used by these skills (localhost only, port 8188).
rem Double-click to run; keep this window open while generating. Log: %COMFYUI_DIR%\user\comfyui_8188.err.log
rem Requires the COMFYUI_DIR environment variable (your ComfyUI install folder).
title ComfyUI (127.0.0.1:8188)
if "%COMFYUI_DIR%"=="" (
  echo ERROR: COMFYUI_DIR is not set.
  echo Set it to your ComfyUI install folder, e.g.:  setx COMFYUI_DIR "C:\path\to\ComfyUI"
  echo then close this window and run this file again.
  pause
  exit /b 1
)
if not exist "%COMFYUI_DIR%\main.py" (
  echo ERROR: COMFYUI_DIR=%COMFYUI_DIR% does not contain ComfyUI's main.py
  pause
  exit /b 1
)
cd /d "%COMFYUI_DIR%"
python main.py --port 8188 2>> user\comfyui_8188.err.log
