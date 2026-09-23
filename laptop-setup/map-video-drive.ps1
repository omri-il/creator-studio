# map-video-drive.ps1
# Run automatically by scheduled task — maps the home PC's drives on this laptop:
#   home E: -> E:  (same letter, so E:\Video Projects\... paths resolve identically)
#   home D: -> S:  (the laptop's own D: is its card-reader slot; S: matches the
#                   letter the Surface used, see Remote-HDD)
# Log: map-video-drive.log, written next to this script.

$HomePcIp   = "100.111.186.101"
# The named shares published by the Remote-HDD project (granted to the limited
# `netshare` account). NOT the \\IP\e admin share - that one requires signing in
# as an administrator of the home PC.
$Mappings = [ordered]@{
    "E" = "\\$HomePcIp\DriveE"
    "S" = "\\$HomePcIp\DriveD"
}
$LogFile    = "$PSScriptRoot\map-video-drive.log"
$MaxLogLines = 50

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Add-Content -Path $LogFile -Value $line
}

# Keep log trim
if (Test-Path $LogFile) {
    $lines = Get-Content $LogFile
    if ($lines.Count -gt $MaxLogLines) {
        $lines | Select-Object -Last $MaxLogLines | Set-Content $LogFile
    }
}

# Work out which letters still need mapping
$todo = @()
foreach ($DriveLetter in $Mappings.Keys) {
    $SharePath = $Mappings[$DriveLetter]
    $existing = Get-PSDrive -Name $DriveLetter -ErrorAction SilentlyContinue
    if (-not $existing) {
        $todo += $DriveLetter
    } elseif ($existing.DisplayRoot -ne $SharePath) {
        # Letter exists but points somewhere else (e.g. local USB drive)
        Write-Log "WARNING: ${DriveLetter}: exists but points to '$($existing.DisplayRoot)' - not overwriting."
    }
    # else: already mapped correctly - nothing to do
}
if ($todo.Count -eq 0) { exit 0 }

# Ping home PC (1 attempt, 1-second timeout)
$ping = Test-Connection -ComputerName $HomePcIp -Count 1 -Quiet -ErrorAction SilentlyContinue
if (-not $ping) {
    Write-Log "Home PC ($HomePcIp) unreachable - skipping."
    exit 0
}

# Map the drives
foreach ($DriveLetter in $todo) {
    $SharePath = $Mappings[$DriveLetter]
    try {
        $result = net use "${DriveLetter}:" $SharePath /persistent:no 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Mapped ${DriveLetter}: -> $SharePath"
        } else {
            Write-Log "ERROR mapping ${DriveLetter}: -> $SharePath : $result"
        }
    } catch {
        Write-Log "EXCEPTION: $_"
    }
}
