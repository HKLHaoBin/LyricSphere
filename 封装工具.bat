@echo off
REM Activate the virtual environment
call .venv\Scripts\activate.bat

REM Run the Python script to convert the Python program to an executable
python -m auto_py_to_exe

REM Pause to keep the window open
pause
