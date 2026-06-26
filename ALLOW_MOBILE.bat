@echo off
chcp 65001 >nul
REM Povoli pristup k workeru z mobilu/jinych zarizeni na stejne WiFi (port 8790).
REM Spusti se s admin pravy (vyskoci UAC). Staci spustit JEDNOU.

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Zadam o administratorska prava...
  powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs"
  exit /b
)

netsh advfirewall firewall delete rule name="PZ AI DAB ALL 8790" >nul 2>&1
netsh advfirewall firewall add rule name="PZ AI DAB ALL 8790" dir=in action=allow protocol=TCP localport=8790 profile=private,domain
echo.
echo [OK] Port 8790 povolen pro LAN/mobil (Private + Domain site).
echo      Na mobilu (stejna WiFi) otevri:  http://%COMPUTERNAME%  nebo  http://[IP-PC]:8790/
echo.
pause
