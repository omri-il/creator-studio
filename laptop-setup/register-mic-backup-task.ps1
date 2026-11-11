# register-mic-backup-task.ps1
# Run ONCE on a machine, from the MAIN checkout's laptop-setup\ (the task points at
# this folder). No admin needed: schtasks takes a MINUTE schedule from a standard user.
# Creates the task "DJIMicBackup": every 2 minutes, mic-backup.ps1 checks whether the
# DJI Mic is plugged in and backs up its new recordings.

$TaskName = "DJIMicBackup"
$VbsPath  = "$PSScriptRoot\mic-backup-hidden.vbs"

foreach ($required in @("$PSScriptRoot\mic-backup.ps1", $VbsPath)) {
    if (-not (Test-Path $required)) { Write-Error "Not found: $required"; exit 1 }
}

$TaskCommand = "wscript.exe //B //Nologo `"$VbsPath`""
schtasks /create /tn $TaskName /tr $TaskCommand /sc minute /mo 2 /f | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error "Could not register '$TaskName' (schtasks exit $LASTEXITCODE)."; exit 1 }
Write-Host "Task '$TaskName' registered (every 2 minutes) -> $VbsPath"
