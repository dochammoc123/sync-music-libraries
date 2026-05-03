@echo off
REM Quick check: log directory + detail/summary files (same paths as main.py)
cd /d "%~dp0"
C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe test_log_paths.py
echo.
pause
