@echo off
rem ===========================================================================
rem  monitor leve — build completo (exe + instalador)
rem  Duplo-clique para regenerar o app e o instalador a partir do codigo atual.
rem  Requisitos (uma unica vez):
rem    - Python 3.12 em %%LOCALAPPDATA%%\Programs\Python\Python312
rem    - pip install pyinstaller
rem    - Inno Setup 6 (busca ISCC.exe nos caminhos comuns)
rem ===========================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "PYDIR=%LOCALAPPDATA%\Programs\Python\Python312"
set "PY=%PYDIR%\python.exe"

if not exist "%PY%" (
    echo [ERRO] Python nao encontrado em %PYDIR%.
    echo        Instale: winget install --id Python.Python.3.12 --scope user
    pause
    exit /b 1
)

echo [1/3] Gerando icone caso nao exista...
if not exist "app.ico" (
    "%PY%" -c "from PIL import Image,ImageDraw;S=256;img=Image.new('RGBA',(S,S),(0,0,0,0));d=ImageDraw.Draw(img);cx=S//2;d.rounded_rectangle([8,8,S-8,S-8],radius=48,fill=(13,13,17,255));pts=[(cx,cy-92),(cx+18,cy-62),(cx+44,cy-34),(cx+48,cy-2),(cx+30,cy+28),(cx+8,cy+48),(cx,cy+54),(cx-8,cy+48),(cx-30,cy+28),(cx-48,cy-2),(cx-44,cy-34),(cx-18,cy-62)];d.polygon(pts,fill=(255,85,0,255),outline=(255,179,77,255));d.line([(cx,cy-85),(cx,cy+48)],fill=(255,122,26,255),width=6);img.save('app.ico',sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]);print('  app.ico criado')"
)

echo [2/3] Compilando monitor-leve.exe (PyInstaller)...
"%PY%" -m PyInstaller --noconfirm --onefile --noconsole --name "monitor-leve" --icon app.ico --collect-submodules psutil monitor_bar.py
if errorlevel 1 (
    echo [ERRO] PyInstaller falhou. Verifique se instalou: pip install pyinstaller
    pause
    exit /b 1
)

echo [3/3] Montando instalador (Inno Setup)...
set "ISCC="
for %%p in (
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) do (
    if exist "%%~p" set "ISCC=%%~p"
)

if not defined ISCC (
    echo [ERRO] Inno Setup nao encontrado. Instale: winget install --id JRSoftware.InnoSetup
    pause
    exit /b 1
)

"!ISCC!" installer.iss
if errorlevel 1 (
    echo [ERRO] Compilacao do instalador falhou.
    pause
    exit /b 1
)

echo.
echo Concluido. Artefatos em dist\:
dir /b dist\*.exe
echo.
pause
exit /b 0