# Makes the "A股资金流向" shortcuts (desktop and Start menu).  They open app.py, the desktop program, with no console.
# WScript can't save a Chinese file name on an English-locale system, so each is saved under an English name and renamed.
$root = $PSScriptRoot
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    $tmp = Join-Path $dir "cn_flow_shortcut.lnk"
    $lnk = $shell.CreateShortcut($tmp)
    $lnk.TargetPath = Join-Path $root ".venv\Scripts\pythonw.exe"
    $lnk.Arguments = "-I -X utf8 `"$(Join-Path $root 'app.py')`""
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$(Join-Path $root 'ui\icon.ico'),0"
    $lnk.Description = "A-share money flow (read only, places no orders)"
    $lnk.Save()
    $final = Join-Path $dir "A股资金流向.lnk"
    Move-Item -LiteralPath $tmp -Destination $final -Force
    Write-Output "shortcut: $final"
}
