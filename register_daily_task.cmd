@echo off
rem Registers the Windows task "cn_flow daily update": Monday-Friday 04:30 local time (US Central = 18:30 Beijing in
rem winter, 17:30 in summer, after the Beijing close), runs daily_update.cmd in a minimised window.  Only for a machine
rem that collects the tick history itself.
rem To remove it later:  schtasks /delete /tn "cn_flow daily update" /f
schtasks /create /tn "cn_flow daily update" /tr "cmd.exe /c start \"cn_flow update\" /min \"%~dp0daily_update.cmd\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 04:30 /f
schtasks /query /tn "cn_flow daily update"
echo.
pause
