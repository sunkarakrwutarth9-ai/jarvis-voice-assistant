' Starts J.A.R.V.I.S. without any console window (uses the private .venv made by setup.bat if present).
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
py = dir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw"
CreateObject("WScript.Shell").Run """" & py & """ """ & dir & "\jarvis.py""", 0, False
