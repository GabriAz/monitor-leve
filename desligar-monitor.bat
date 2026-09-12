@echo off
rem Encerra qualquer instancia do monitor (barra na taskbar).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*monitor_bar.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
exit /b 0