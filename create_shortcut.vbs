Set WshShell = CreateObject("WScript.Shell")
Set oLink = WshShell.CreateShortcut(WshShell.SpecialFolders("Startup") & "\stackchan.lnk")
oLink.TargetPath = "C:\stackchan\start_stackchan.bat"
oLink.WorkingDirectory = "C:\stackchan"
oLink.WindowStyle = 7
oLink.Save
WScript.Echo "Shortcut created in Startup folder"
