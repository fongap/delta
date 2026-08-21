@echo off
setlocal
cd /d "D:\900 AIWork\910 GitHub\delta\packaging"
call "C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
set "PATH=C:\Users\Fong\AppData\Roaming\Python\Python312\Scripts;%PATH%"
del portable_build_run.log 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -File build_portable.ps1 %* > portable_build_run.log 2>&1
echo EXIT=%ERRORLEVEL%
