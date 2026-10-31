# Create a Windows shortcut with Ctrl+Alt+J hotkey for AK voice command
$projectDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$shortcutPath = "$([Environment]::GetFolderPath('StartMenu'))\Programs\AK Voice.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "python.exe"
$shortcut.Arguments = "main.py --voice-once"
$shortcut.WorkingDirectory = $projectDir
$shortcut.Description = "AK Voice Assistant - Ctrl+Alt+J"
$shortcut.Hotkey = "Ctrl+Alt+J"
$shortcut.Save()

Write-Host "Shortcut created at: $shortcutPath"
Write-Host "Pin it to Start or Taskbar, or place on Desktop."
Write-Host "Press Ctrl+Alt+J anytime to trigger AK."
