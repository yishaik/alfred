# LG webOS remote

Send `/tv` to Alfred or open **Panel → 📺 שלט**. The inline keyboard controls
navigation, Home/Back, volume, mute/unmute, playback, channels, installed apps,
HDMI inputs and power. Commands use a persistent connection directly from the
bridge process to the TV. No model turn or separate `tv-daemon` is involved.

## Setup

The bridge computer must reach the TV on the local network. A bridge hosted on
a cloud server cannot reach a home TV unless local-network routing is provided.
Only private IPv4 addresses are accepted.

Install the repository requirements with Python 3.11 or later:

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```

After the bridge loads the updated code, turn the TV on and send
`/tv connect 192.168.1.50` with its actual IP. Accept the pairing prompt **on the
TV**, then use `/tv`. Pairing is reused automatically; `/tv pair` explicitly
requests fresh pairing. Changing the TV address clears its old key and MAC.

The private pairing key and configuration live in `state/tv.json` (or under
`BRIDGE_STATE_DIR`), excluded from Git. Do not share this file. Optional
environment defaults are `BRIDGE_TV_HOST`, `BRIDGE_TV_MAC` and
`BRIDGE_TV_BROADCAST`. Settings saved through `/tv` take precedence over defaults.

The current process loads handlers only at startup. Follow `AGENTS.md` for
orchestrator-managed restart; editing source does not hot-load the remote.
Telegram `/restart` resets an agent session and does not load Python changes.

## Commands

| Command | Action |
| --- | --- |
| `/tv`, `/tv status` | Remote with current volume and foreground app |
| `/tv help` | Setup instructions |
| `/tv connect IP`, `/tv pair [IP]` | Connect or request fresh pairing |
| `/tv vol 12` | Set volume, integer 0–100 |
| `/tv mute`, `/tv unmute` | Set mute explicitly; remote button toggles it |
| `/tv apps`, `/tv inputs` | Paginated selection from the TV's actual catalog |
| `/tv app APP_ID`, `/tv input HDMI_1` | Select a known app or input ID |
| `/tv button UP` | Navigation button; service-menu buttons are not supported |
| `/tv text some text` | Type into the TV's currently focused text field |
| `/tv on`, `/tv off` | Wake-on-LAN request or power-off command |
| `/tv mac AA:BB:CC:DD:EE:FF [BROADCAST_IP]` | Configure Wake-on-LAN |

Wake-on-LAN requires the TV's network-interface MAC and the TV's network-wake
setting (wording varies by model). An optional subnet broadcast address helps
on computers with several network adapters. Sending a wake packet does not
prove the TV powered on; wait and use **מצב** to check. Wake behavior has not
been physically verified on this installation.

## Behavior and troubleshooting

- TV commands have the same chat authorization as bridge commands and ignore
  bot-authored updates. Inline callbacks have their own `tv:` namespace and
  work without creating a Claude/Codex session, including in forum topics.
- Connections and commands have timeouts. Commands are serialized. A tap that
  waits over two seconds for another action is rejected instead of replayed
  later. A failed command is never retried automatically; the next tap connects
  again as needed. Failures stay visible in the remote panel.
- Older arrow/volume reply-keyboard labels still work. Plain `OK` is ordinary
  chat; use the new inline **OK** button for the TV.
- If offline, check power, IP address, Wi-Fi isolation and that the bridge
  computer can reach ports 3000/3001. Newer TVs can require secure WebSocket;
  the client supports the TV's self-signed certificate locally. No public
  listener or inbound port-forward is required.
- App, input and text support varies by firmware and the active app. Navigation
  buttons report that a button was sent; the TV does not acknowledge the visible
  effect of each pointer-socket button.

Protocol references: [LG Connect SDK WebOSTVService](https://connectsdk.com/en/latest/apis-and/and-webostvservice.html)
and [aiowebostv](https://github.com/home-assistant-libs/aiowebostv).

## Verification

```powershell
uv pip install --python .venv/Scripts/python.exe pytest
.venv/Scripts/python.exe -m pytest tests/test_tv_remote.py -q
```

Tests cover pairing persistence, connection reuse, invalid targets, command
validation, reconnection, timeout/cancellation cleanup, ordered taps, mute,
Wake-on-LAN packet bytes, authorization, catalogs and chat routing. They do not
send Telegram messages or control a physical TV. Live verification separately
reads TV state and catalogs without changing playback or power.
