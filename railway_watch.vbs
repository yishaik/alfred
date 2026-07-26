' Launches the railway watcher hidden, via the supervised .bat wrapper so a hard
' crash gets restarted (bare pythonw died silently, with no log and no restart).
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\Projects\telegram-claude-bridge"
sh.Run "cmd /c ""D:\Projects\telegram-claude-bridge\start_railway_watch.bat""", 0, False
