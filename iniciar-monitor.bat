@echo off
rem Inicia o monitor de sistema na bandeja.
rem Precisa do Python 3.12 instalado no caminho abaixo (winget).

set "PYDIR=%LOCALAPPDATA%\Programs\Python\Python312"
set "PY=%PYDIR%\python.exe"
set "PYT=%PYDIR%\pythonw.exe"

if not exist "%PY%" (
    echo Python 3.12 nao encontrado em %PYDIR%.
    echo Instale com: winget install --id Python.Python.3.12 --scope user
    pause
    exit /b 1
)

rem pythonw roda sem abrir janela de console.
rem (tkinter precisa de um loop de mensagens; pythonw gerencia isso sem console visivel.)
start "" "%PYT%" "%~dp0monitor_bar.py"
exit /b 0