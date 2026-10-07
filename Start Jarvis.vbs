' Starts Ultron without any console window (uses the private .venv made by setup.bat if present).
' If Ultron is already running, opens the command center instead.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)

running = False
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
http.setTimeouts 1000, 1000, 1500, 1500
http.Open "GET", "http://127.0.0.1:7777/api/state", False
http.Send
If Err.Number = 0 Then If http.Status = 200 Then running = True
On Error GoTo 0

If running And WScript.Arguments.Count = 0 Then
  sh.Run "http://localhost:7777", 1, False
  WScript.Quit
End If

py = dir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw"
sh.CurrentDirectory = dir
sh.Run """" & py & """ """ & dir & "\jarvis.py""", 0, False
