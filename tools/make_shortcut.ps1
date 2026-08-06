# Creates "Finance Dashboard.lnk" in the project root, ready to right-click ->
# Pin to taskbar.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\make_shortcut.ps1
#
# Two things that look odd but are load-bearing:
#
#   * TargetPath is cmd.exe with run.bat as an argument, not run.bat itself.
#     Windows refuses "Pin to taskbar" on a shortcut whose target is a .bat.
#   * Paths are derived from $PSScriptRoot, so this stays personal-path free
#     and safe to commit. The .lnk it produces is NOT -- it embeds absolute
#     paths including your username, which is why *.lnk is gitignored.

$root   = Split-Path -Parent $PSScriptRoot
$runBat = Join-Path $root 'run.bat'
$icon   = Join-Path $root 'app\static\finance-dashboard.ico'
$lnk    = Join-Path $root 'Finance Dashboard.lnk'

foreach ($p in @($runBat, $icon)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "missing: $p" }
}

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath       = "$env:SystemRoot\System32\cmd.exe"
$sc.Arguments        = "/c `"$runBat`""
$sc.WorkingDirectory = $root
$sc.IconLocation     = "$icon,0"
$sc.Description      = 'Local finance dashboard (http://127.0.0.1:8765)'
$sc.WindowStyle      = 7   # minimized; the server console stays reachable
$sc.Save()

$shell.CreateShortcut($lnk) |
    Format-List TargetPath, Arguments, WorkingDirectory, IconLocation, WindowStyle
"created: $lnk"
