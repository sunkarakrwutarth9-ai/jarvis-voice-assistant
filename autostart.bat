@echo off
rem Start J.A.R.V.I.S. automatically when you sign in to Windows.
rem   autostart.bat        turn it on
rem   autostart.bat off    turn it off
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Jarvis.lnk"
if /i "%~1"=="off" (
  del "%LINK%" 2>nul
  echo Jarvis will no longer start with Windows.
  goto :eof
)
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LINK%'); $s.TargetPath='wscript.exe'; $s.Arguments='\"%~dp0Start Jarvis.vbs\"'; $s.WorkingDirectory='%~dp0'; $s.Description='Start J.A.R.V.I.S.'; $s.Save()"
echo Jarvis will now start automatically every time you sign in to Windows.
