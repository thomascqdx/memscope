@echo off
setlocal
cd /d "%~dp0"

rem ---- 1. locate python ----
set "PY=py -3"
%PY% --version >nul 2>&1
if errorlevel 1 (
    set "PY=python"
    python --version >nul 2>&1
    if errorlevel 1 (
        echo [build] ERROR: python not found ^(tried: py -3, python^)
        exit /b 1
    )
)
echo [build] using: %PY%

rem ---- 2. isolated build venv ----
if not exist ".venv-build\Scripts\python.exe" (
    echo [build] creating venv .venv-build ...
    %PY% -m venv .venv-build
    if errorlevel 1 (
        echo [build] ERROR: venv creation failed
        exit /b 1
    )
) else (
    echo [build] reusing .venv-build
)
set "VENV_PY=.venv-build\Scripts\python.exe"

rem ---- 3. install pyinstaller + project ----
echo [build] installing dependencies ...
%VENV_PY% -m pip install --quiet pyinstaller==6.17.0 .
if errorlevel 1 (
    echo [build] pip failed, retrying with proxy http://127.0.0.1:7890 ...
    %VENV_PY% -m pip install --quiet --proxy http://127.0.0.1:7890 pyinstaller==6.17.0 .
    if errorlevel 1 (
        echo [build] ERROR: pip install failed
        exit /b 1
    )
)

rem ---- 4. clean previous outputs ----
if exist "build\pyinstaller" rmdir /s /q "build\pyinstaller"
if exist "dist\memscope.exe" del /q "dist\memscope.exe"

rem ---- 5. pyinstaller ----
echo [build] running PyInstaller ...
%VENV_PY% -m PyInstaller --onefile --console --name memscope --clean --noconfirm --collect-data memscope --distpath dist --workpath build\pyinstaller --specpath build\pyinstaller packaging\memscope_entry.py
if errorlevel 1 (
    echo [build] ERROR: PyInstaller failed
    exit /b 1
)

rem ---- 6. smoke test + versioned artifact ----
for /f "tokens=2" %%V in ('dist\memscope.exe --version') do set "VER=%%V"
if not defined VER (
    echo [build] ERROR: smoke test failed ^(memscope.exe --version^)
    exit /b 1
)
copy /y "dist\memscope.exe" "dist\memscope-%VER%.exe" >nul
if errorlevel 1 (
    echo [build] ERROR: copy to dist\memscope-%VER%.exe failed
    exit /b 1
)
for %%F in (dist\memscope-%VER%.exe) do set "EXESIZE=%%~zF"
echo [build] OK: %CD%\dist\memscope-%VER%.exe ^(version %VER%, %EXESIZE% bytes^)
exit /b 0
