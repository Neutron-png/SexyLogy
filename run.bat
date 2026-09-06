@echo off
rem LOGY launcher - works even though the venv is missing pyvenv.cfg:
rem runs the system Python 3.14 but points it at the venv's site-packages.
setlocal
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%.venv\Lib\site-packages;%ROOT%"
py "%ROOT%main.py" %*
endlocal
