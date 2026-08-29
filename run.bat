@echo off
REM Startar Ollama Studio pa Windows.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py ollama_studio.py
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python ollama_studio.py
    goto :end
)

echo Kunde inte hitta Python. Installera Python 3.8+ fran https://www.python.org/downloads/
pause

:end
endlocal
