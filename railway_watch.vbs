Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\Projects\telegram-claude-bridge"
sh.Run """D:\Projects\telegram-claude-bridge\.venv\Scripts\pythonw.exe"" railway_watch.py", 0, False
