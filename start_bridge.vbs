' Silent launcher for the Telegram<->Claude bridge (no console window).
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c """"D:\Projects\telegram-claude-bridge\start_bridge.bat""""", 0, False
