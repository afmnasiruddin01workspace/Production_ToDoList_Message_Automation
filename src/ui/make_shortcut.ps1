# Creates a Start Menu shortcut for the manual-send UI. Deliberately NOT on the Desktop: on this
# machine the Desktop is a Google-Drive-synced folder, and a shortcut there would sync a path from
# this laptop to the cloud (plan 05, R18).
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$dir  = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$link = Join-Path $dir 'Production ToDo - Manual Send.lnk'
if ($Remove) {
    if (Test-Path $link) { [System.IO.File]::Delete($link) }
    Write-Host 'Shortcut removed.'
    return
}
$target = Join-Path $PSScriptRoot 'start_ui.cmd'
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($link)
$s.TargetPath = $target
$s.WorkingDirectory = $PSScriptRoot
$s.WindowStyle = 7   # minimised console; the browser opens by itself
$s.Description = 'Production ToDo - send the list by hand and see the delivery log'
$s.Save()
Write-Host "Shortcut created: $link"
