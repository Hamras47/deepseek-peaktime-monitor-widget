# Stop starting the DeepSeek peak / off-peak widget with Windows.
# Removes the shortcut from the current user's Startup folder.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$here\.venv\Scripts\python.exe" "$here\app.py" --remove-autostart
