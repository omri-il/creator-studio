' mic-backup-hidden.vbs
' Runs mic-backup.ps1 with no console window (same reason as map-video-drive-hidden.vbs:
' powershell.exe flashes a console every cycle even with -WindowStyle Hidden).

Dim shell, fso, scriptDir, ps1
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = fso.BuildPath(scriptDir, "mic-backup.ps1")

shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & ps1 & """", 0, False
