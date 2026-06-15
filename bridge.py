#!/usr/bin/env python3
"""Telegram <-> Claude Code bridge — entry point.

The implementation lives in the tgbridge/ package (Agent SDK edition).
This shim keeps start_bridge.bat / .vbs / the Startup shortcut working.
"""

from tgbridge.main import main

if __name__ == "__main__":
    main()
