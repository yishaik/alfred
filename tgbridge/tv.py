"""Direct, serialized LG webOS control with persisted pairing.

Purpose:  Keep one LAN connection for the bridge's TV remote.
Inputs:   Authorized actions, private IPv4 address, optional Wake-on-LAN MAC.
Outputs:  TV commands and pairing/settings in the ignored state directory.
Key fns:  LGRemote.execute, configure, close.
Deps:     aiowebostv (loaded on first connection), config state helpers.
Note:     Failed commands are never replayed; the next action reconnects.
Updated:  2026-09-12
"""

import asyncio
import ipaddress
import os
import re
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from .config import STATE_DIR, load_json, save_json

BUTTONS = frozenset({
    "UP", "DOWN", "LEFT", "RIGHT", "ENTER", "BACK", "HOME", "EXIT",
    "MENU", "INFO", "RED", "GREEN", "YELLOW", "BLUE", *map(str, range(10)),
})
NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class TVError(Exception):
    """An actionable message safe to show in the chat."""


def local_ip(value: str) -> str:
    try:
        addr = ipaddress.IPv4Address(value)
        if any(addr in network for network in NETWORKS):
            return str(addr)
    except ipaddress.AddressValueError:
        pass
    raise TVError("יש להזין כתובת IPv4 של הטלוויזיה ברשת המקומית, למשל 192.168.1.50.")


def mac_address(value: str) -> str:
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", value):
        raise TVError("כתובת MAC צריכה להיות בפורמט AA:BB:CC:DD:EE:FF.")
    return value.replace("-", ":").lower()


def broadcast_ip(value: str) -> str:
    return value if value == "255.255.255.255" else local_ip(value)


def _make_client(host, key):
    try:
        from aiowebostv import WebOsClient
    except ImportError as exc:
        raise TVError("תמיכת הטלוויזיה חסרה בשרת. יש להתקין את requirements.txt.") from exc
    return WebOsClient(host, key, connect_timeout=3)


def _wake(mac, broadcast):
    packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, 9))


class LGRemote:
    def __init__(self, state_file: Path = STATE_DIR / "tv.json", *, client_factory=None):
        self.state_file = state_file
        saved = load_json(state_file, {})
        self.settings = saved if isinstance(saved, dict) else {}
        # Environment supplies defaults; an explicit /tv connect is persistent.
        self.settings.setdefault("host", os.environ.get("BRIDGE_TV_HOST", ""))
        self.settings.setdefault("mac", os.environ.get("BRIDGE_TV_MAC", ""))
        self.settings.setdefault("broadcast", os.environ.get("BRIDGE_TV_BROADCAST", "255.255.255.255"))
        self._factory = client_factory or _make_client
        self._client = None
        self._lock = asyncio.Lock()

    @property
    def host(self):
        return self.settings.get("host", "")

    @property
    def paired(self):
        return bool(self.settings.get("client_key"))

    def _save(self):
        try:
            save_json(self.state_file, self.settings)
        except OSError as exc:
            raise TVError("הגדרות הטלוויזיה לא נשמרו. בדוק מקום פנוי והרשאות לתיקיית state בשרת.") from exc

    @asynccontextmanager
    async def _operation(self):
        # Do not deliver old taps long after a slow/offline TV recovers.
        try:
            await asyncio.wait_for(self._lock.acquire(), 2)
        except TimeoutError as exc:
            raise TVError("הטלוויזיה מטפלת בפעולה קודמת. הלחיצה לא נשלחה; נסה שוב בעוד רגע.") from exc
        try:
            yield
        finally:
            self._lock.release()

    async def _disconnect(self):
        client, self._client = self._client, None
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), 5)
            except Exception:
                pass

    async def close(self):
        async with self._lock:
            await self._disconnect()

    async def configure(self, host: str, *, pair=False):
        host = local_ip(host)
        async with self._lock:
            changed = host != self.host
            if changed or pair:
                await self._disconnect()
                self.settings.pop("client_key", None)
                if changed:
                    self.settings["mac"] = ""
                    self.settings["broadcast"] = "255.255.255.255"
            self.settings["host"] = host
            self._save()

    async def configure_wake(self, mac: str, broadcast: str = "255.255.255.255"):
        mac, broadcast = mac_address(mac), broadcast_ip(broadcast)
        async with self._lock:
            self.settings.update(mac=mac, broadcast=broadcast)
            self._save()

    async def _connect(self):
        if not self.host:
            raise TVError("להגדרה ראשונית: /tv connect IP — כשהטלוויזיה דלוקה ובאותה רשת.")
        host = local_ip(self.host)
        if self._client is not None and self._client.is_connected():
            return self._client
        await self._disconnect()
        self._client = self._factory(host, self.settings.get("client_key"))
        if not await asyncio.wait_for(self._client.connect(), 30):
            raise TVError("החיבור לטלוויזיה לא הושלם. נסה /tv status שוב.")
        if self._client.client_key != self.settings.get("client_key"):
            self.settings["client_key"] = self._client.client_key
            self._save()
        return self._client

    async def execute(self, action: str, value=None):
        """Connect lazily, serialize taps, and surface failures without retries."""
        # Validate before opening a connection or causing a pairing prompt.
        if action == "button" and value not in BUTTONS:
            raise TVError("כפתור לא נתמך.")
        if action == "volume" and (type(value) is not int or not 0 <= value <= 100):
            raise TVError("עוצמת הקול חייבת להיות מספר שלם בין 0 ל־100.")
        if action in {"app", "input"} and (not isinstance(value, str) or not value or len(value) > 256):
            raise TVError("חסר מזהה אפליקציה או כניסה תקין.")
        if action == "text" and (not isinstance(value, str) or not value or len(value) > 500):
            raise TVError("יש להזין טקסט באורך 1–500 תווים.")
        allowed = {"status", "button", "volume", "volup", "voldown", "mute", "unmute",
                   "toggle_mute", "play", "pause", "off", "on", "apps", "inputs",
                   "app", "input", "channel_up", "channel_down", "text"}
        if action not in allowed:
            raise TVError("פעולה לא נתמכת. פתח /tv להצגת השלט.")
        async with self._operation():
            try:
                if action == "on":
                    mac = self.settings.get("mac", "")
                    if not mac:
                        raise TVError("להדלקה יש להגדיר תחילה /tv mac AA:BB:CC:DD:EE:FF ולהפעיל בטלוויזיה הדלקה דרך הרשת.")
                    await asyncio.to_thread(_wake, mac_address(mac), broadcast_ip(self.settings["broadcast"]))
                    return "נשלחה בקשת הדלקה. המתן כמה שניות ולחץ על מצב כדי לבדוק."
                client = await self._connect()
                async with asyncio.timeout(8):
                    return await self._command(client, action, value)
            except TVError:
                raise
            except asyncio.CancelledError:
                await self._disconnect()
                raise
            except Exception as exc:
                await self._disconnect()
                if type(exc).__name__ == "WebOsTvPairError":
                    raise TVError("הצימוד לא אושר. הפעל /tv pair ואשר את הבקשה על מסך הטלוויזיה.") from exc
                if isinstance(exc, TimeoutError):
                    raise TVError("הטלוויזיה לא ענתה בזמן. ודא שהיא דלוקה; אם מופיעה בקשת צימוד, אשר אותה. הפעולה לא אומתה.") from exc
                raise TVError("הפעולה לא אומתה. בדוק שהטלוויזיה דלוקה ובאותה רשת, ואז נסה /tv status.") from exc

    async def _command(self, client, action, value):
        if action == "status":
            volume = await client.get_volume()
            muted = await client.get_muted()
            app = await client.get_current_app()
            return f"מחובר ל־{self.host}\nעוצמה: {volume if volume is not None else '?'}{' · מושתק' if muted else ''}\nאפליקציה: {app or 'לא ידועה'}"
        if action in {"apps", "inputs"}:
            return await (client.get_apps() if action == "apps" else client.get_inputs()) or []
        if action == "button":
            await client.button(value)
            return f"נשלח כפתור {value}"
        if action == "volume":
            await client.set_volume(value)
            return f"עוצמת הקול הוגדרה ל־{value}"
        if action in {"mute", "unmute", "toggle_mute"}:
            muted = action == "mute"
            if action == "toggle_mute":
                current = await client.get_muted()
                if type(current) is not bool:
                    raise TVError("לא ניתן לקרוא את מצב ההשתקה. השתמש ב־/tv mute או /tv unmute.")
                muted = not current
            await client.set_mute(muted)
            return "הקול הושתק" if muted else "ההשתקה בוטלה"
        if action == "app":
            await client.launch_app(value)
            return "נשלחה בקשה לפתיחת האפליקציה"
        if action == "input":
            await client.set_input(value)
            return "נשלחה בקשה להחלפת כניסה"
        if action == "text":
            await client.request("com.webos.service.ime/insertText", {"text": value, "replace": 0})
            return "הטקסט נשלח לשדה הפעיל בטלוויזיה"
        methods = {
            "volup": (client.volume_up, "עוצמת הקול הוגברה"),
            "voldown": (client.volume_down, "עוצמת הקול הונמכה"),
            "play": (client.play, "נשלחה בקשת ניגון"),
            "pause": (client.pause, "נשלחה בקשת השהיה"),
            "off": (client.power_off, "נשלחה בקשת כיבוי"),
            "channel_up": (client.channel_up, "נשלחה בקשה לערוץ הבא"),
            "channel_down": (client.channel_down, "נשלחה בקשה לערוץ הקודם"),
        }
        method, message = methods[action]
        await method()
        return message
