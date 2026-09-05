@echo off
echo Restarting The Mesozoic services...

net stop EvrimaBot
net stop miniEniac-RCON

timeout /t 3 /nobreak > nul

net start miniEniac-RCON
net start EvrimaBot

echo Services restarted.
pause