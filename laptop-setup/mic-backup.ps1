# mic-backup.ps1
# Backs up the DJI Mic transmitter's own recordings ("mic 2" of the Osmo Pocket 4)
# whenever it is plugged into this machine. Run every 2 minutes by the scheduled
# task "DJIMicBackup" (register-mic-backup-task.ps1); safe to run by hand too.
#
# 1. Finds any removable drive with a TX_MIC* folder of .wav files (the mic's
#    internal storage; it gets whatever letter is free, so never trust a letter).
# 2. Copies every .wav not yet backed up to the LOCAL archive
#       C:\Users\<you>\Videos\DJI Mic Archive\<yyyy-MM-dd>\<original name>.wav
#    (date taken from the file name, e.g. TX00_MIC041_20261012_133945_orig.wav).
#    Copy goes to <name>.part first and is renamed on success, so a file under its
#    final name is always complete. An existing same-size file = already done.
# 3. When the home PC's D: is mapped as S: (laptop-setup\map-video-drive.ps1),
#    copies anything the home archive S:\DJI Mic Archive lacks. Never overwrites,
#    never deletes, in either place. Recordings made away from home reach the home
#    PC the next time it is reachable.
# Nothing is ever deleted from the mic itself.

param(
    [string]$LocalArchive = (Join-Path ([Environment]::GetFolderPath('MyVideos')) 'DJI Mic Archive'),
    [string]$HomeArchive  = 'S:\DJI Mic Archive',
    [switch]$NoNotify
)

$ErrorActionPreference = 'Stop'
$LogPath = Join-Path $PSScriptRoot 'mic-backup.log'

function Log([string]$msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
    Write-Host $line
}

function Notify([string]$title, [string]$text) {
    if ($NoNotify) { return }
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Information
        $n.Visible = $true
        $n.ShowBalloonTip(8000, $title, $text, 'Info')
        Start-Sleep -Seconds 6
        $n.Dispose()
    } catch { }
}

# One run at a time: a first full backup takes longer than the 2-minute cycle.
$mutex = New-Object System.Threading.Mutex($false, 'Local\DJIMicBackup')
if (-not $mutex.WaitOne(0)) { exit 0 }

try {
    # Keep the log short.
    if ((Test-Path $LogPath) -and ((Get-Item $LogPath).Length -gt 512KB)) {
        $keep = Get-Content $LogPath -Tail 2000
        Set-Content -Path $LogPath -Value $keep -Encoding UTF8
    }

    # ---- 1. find the mic -------------------------------------------------------
    function Find-Mics {
        $found = @()
        foreach ($d in [System.IO.DriveInfo]::GetDrives()) {
            try {
                if ($d.DriveType -ne 'Removable' -or -not $d.IsReady) { continue }
                $dirs = @(Get-ChildItem -LiteralPath $d.RootDirectory.FullName -Directory -Filter 'TX_MIC*' -ErrorAction SilentlyContinue)
                if ($dirs.Count -gt 0) { $found += [pscustomobject]@{ Root = $d.RootDirectory.FullName; Dirs = $dirs } }
            } catch { }
        }
        return ,$found
    }

    # The mic's USB link drops for a few seconds every minute or so (seen 2026-11-11:
    # disk errors in the System log, the drive gone and back without unplugging), and
    # it may come back under another letter. So a failed copy waits for the file to be
    # reachable again (any letter) and retries it.
    function Wait-MicFile([string]$dirName, [string]$fileName, [int]$timeoutSec = 90) {
        $deadline = (Get-Date).AddSeconds($timeoutSec)
        while ((Get-Date) -lt $deadline) {
            foreach ($m in (Find-Mics)) {
                $p = Join-Path (Join-Path $m.Root $dirName) $fileName
                if (Test-Path -LiteralPath $p) { return $p }
            }
            Start-Sleep -Seconds 3
        }
        return $null
    }

    $mics = Find-Mics

    # ---- 2. mic -> local archive ----------------------------------------------
    foreach ($mic in $mics) {
        $files = @($mic.Dirs | ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -File -Filter '*.wav' -ErrorAction SilentlyContinue })
        $todo = @()
        foreach ($f in $files) {
            if ($f.Name -match '_(20\d{2})(\d{2})(\d{2})_\d{6}') { $day = "$($Matches[1])-$($Matches[2])-$($Matches[3])" }
            else { $day = $f.LastWriteTime.ToString('yyyy-MM-dd') }
            $destDir = Join-Path $LocalArchive $day
            $dest = Join-Path $destDir $f.Name
            if (Test-Path -LiteralPath $dest) {
                if ((Get-Item -LiteralPath $dest).Length -eq $f.Length) { continue }
                # Same name, different size: keep both, never overwrite.
                $dest = Join-Path $destDir ('{0}_{1}{2}' -f $f.BaseName, $f.Length, $f.Extension)
                if ((Test-Path -LiteralPath $dest) -and (Get-Item -LiteralPath $dest).Length -eq $f.Length) { continue }
            }
            $todo += [pscustomobject]@{ Src = $f; Dest = $dest; DestDir = $destDir }
        }
        if ($todo.Count -eq 0) { continue }

        $bytes = [long]0; foreach ($t in $todo) { $bytes += $t.Src.Length }
        $gb = [math]::Round($bytes / 1GB, 2)
        Log "Mic at $($mic.Root): $($todo.Count) new recording(s), $gb GB -> $LocalArchive"
        Notify 'DJI Mic backup' "Copying $($todo.Count) new recording(s) ($gb GB). Keep the mic plugged in."

        $ok = 0; $failed = 0; $gone = $false
        foreach ($t in $todo) {
            $part = "$($t.Dest).part"
            $dirName = $t.Src.Directory.Name
            $done = $false
            for ($attempt = 1; $attempt -le 5 -and -not $done; $attempt++) {
                $src = Wait-MicFile $dirName $t.Src.Name
                if (-not $src) { $gone = $true; break }
                try {
                    New-Item -ItemType Directory -Force -Path $t.DestDir | Out-Null
                    [System.IO.File]::Copy($src, $part, $true)
                    if ((Get-Item -LiteralPath $part).Length -ne $t.Src.Length) { throw 'size mismatch after copy' }
                    (Get-Item -LiteralPath $part).LastWriteTime = $t.Src.LastWriteTime
                    Move-Item -LiteralPath $part -Destination $t.Dest
                    $done = $true
                } catch {
                    Log "  retry $attempt $($t.Src.Name): $($_.Exception.Message.Trim())"
                    Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 5
                }
            }
            if ($done) { $ok++ }
            elseif ($gone) { Log "  mic gone for 90 s - stopping; the next run continues"; break }
            else { $failed++; Log "  FAILED $($t.Src.Name) after 5 attempts" }
        }
        $left = $todo.Count - $ok
        Log "  done: $ok copied, $left left"
        if ($left -gt 0) { Notify 'DJI Mic backup' "$ok copied, $left not yet (the mic disconnected). Keep it plugged in - it retries every 2 minutes."; continue }
        Notify 'DJI Mic backup' "Done: $ok recording(s) backed up. You can unplug the mic."
    }

    # ---- 3. local archive -> home PC archive -----------------------------------
    if ((Test-Path -LiteralPath $LocalArchive) -and (Test-Path -LiteralPath (Split-Path $HomeArchive -Qualifier))) {
        # /XC /XN /XO = only files the home archive does not have at all: never overwrite.
        $out = & robocopy $LocalArchive $HomeArchive *.wav /E /XC /XN /XO /XF *.part /R:1 /W:5 /NP /NJH /NJS /NDL
        $code = $LASTEXITCODE
        $copied = @($out | Where-Object { $_ -match '\.wav\s*$' }).Count
        if ($code -ge 8) { Log "Home archive sync FAILED (robocopy $code): $($out -join ' | ')" }
        elseif ($copied -gt 0) { Log "Home archive: $copied file(s) copied to $HomeArchive" }
    }
} catch {
    Log "ERROR: $($_.Exception.Message)"
} finally {
    $mutex.ReleaseMutex()
}
