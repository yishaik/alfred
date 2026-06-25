# Concepts

* [Alfred — architecture overview](overview.md) - Always-on Telegram bot that drives Claude Agent SDK sessions, one per chat/forum-topic, with streaming, permissions, and a secretary.
* [Process & runtime model](process-model.md) - How the bridge runs — supervisor, the uv-venv launcher shim, the "4 python processes is healthy" rule, single-instance lock, and S4U autostart.
