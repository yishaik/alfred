#!/usr/bin/env python3
"""One-off: render a rich-text sampler with the CURRENT tgbridge.fmt and send it
straight to the owner chat via the Bot API (independent of the bridge poller)."""
import json
import urllib.parse
import urllib.request
from pathlib import Path

from tgbridge.fmt import md_to_html

env = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

token = env["BRIDGE_BOT_TOKEN"]
chat_id = env["BRIDGE_CHAT_ID"]

MD = """\
# Rich Text Test (H1, underlined)
## Subheading (H2, underlined)
### Smaller heading (H3, bold)

**bold**, __also bold__, *italic*, _also italic_, ~~strikethrough~~, a ||spoiler||, \
inline `code`, and a [link](https://example.com).

- bullet one
- bullet **two**
+ plus-sign bullet
* star bullet

---

> A short blockquote.

> Expandable quote — line 1
> line 2
> line 3
> line 4
> line 5 (over four lines, so this collapses behind a tap)

```python
def hello(name):
    return f"hi {name}"
```

| Feature  | Status |     Notes      |
|:---------|:------:|---------------:|
| bold     |   ok   |     ** / __    |
| table    |   ok   |  aligned mono  |
| spoiler  |   ok   |  tap to reveal |
"""

html = md_to_html(MD)
data = urllib.parse.urlencode({
    "chat_id": chat_id,
    "text": html,
    "parse_mode": "HTML",
    "disable_web_page_preview": "true",
}).encode()
url = f"https://api.telegram.org/bot{token}/sendMessage"
try:
    with urllib.request.urlopen(url, data, timeout=20) as r:
        resp = json.load(r)
    print("OK" if resp.get("ok") else "FAIL", "->", resp.get("ok"))
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", "replace")
    print("HTTP", e.code, body)
    print("--- rendered HTML was ---")
    print(html)
