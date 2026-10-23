"""Keep the suite independent of the machine it runs on.

tgbridge.config loads ROOT/.env at import time, so on a deployed box the real
admin list, chat IDs and tokens leaked into tests (admin-gated handlers then
refused the user-less fake updates). Point it at a file that doesn't exist.
"""
import os

os.environ["BRIDGE_ENV_FILE"] = os.path.join(os.path.dirname(__file__), ".no-such-env")
for _k in ("BRIDGE_ADMIN_IDS", "BRIDGE_ALLOWED_USER_IDS"):
    os.environ.pop(_k, None)
