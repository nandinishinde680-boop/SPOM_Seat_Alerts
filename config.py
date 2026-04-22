"""
config.py
---------
Central configuration for ICAI SPOM Slot Monitor.

Edit the WATCHLIST and Telegram settings below, OR set the corresponding
environment variables (env vars always override hardcoded values).

Quick-start checklist:
  1. Set TELEGRAM_BOT_TOKEN  (create a bot via @BotFather on Telegram)
  2. Set TELEGRAM_CHAT_IDS   (get your chat ID from @userinfobot)
  3. Optionally set ICAI_USERNAME / ICAI_PASSWORD if the page requires login
  4. Run: python main.py
"""

import os
from dataclasses import dataclass, field
from typing import List


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                  EDIT YOUR PREFERENCES BELOW                             ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@dataclass
class LocationConfig:
    """A single location to monitor."""
    country:  str        = "India"
    state:    str        = "Maharashtra"
    city:     str        = "Mumbai"
    # Use ["all"] to check every available test centre in this city,
    # or list specific centre names: ["Dexit Global Limited"]
    centres:  List[str]  = field(default_factory=lambda: ["all"])


# ── WATCHLIST — add more LocationConfig entries to monitor multiple cities ────
WATCHLIST: List[LocationConfig] = [
    LocationConfig(country="India", state="Maharashtra", city="Mumbai"),
    # LocationConfig(country="India", state="Maharashtra", city="Pune"),
    # LocationConfig(country="India", state="Delhi",        city="New Delhi"),
]

# ── Telegram ──────────────────────────────────────────────────────────────────
# Get token from @BotFather → /newbot → copy the token
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Your Telegram chat ID — get it from @userinfobot or @RawDataBot
# Multiple IDs can be comma-separated: "123456,789012"
_raw_chat_ids = os.environ.get(
    "TELEGRAM_CHAT_IDS",
    os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE"),
)
TELEGRAM_CHAT_IDS: List[str] = [c.strip() for c in _raw_chat_ids.split(",") if c.strip()]

# ── ICAI Portal Login (fill in if the site requires login to view slots) ──────
ICAI_USERNAME: str = os.environ.get("ICAI_USERNAME", "")
ICAI_PASSWORD: str = os.environ.get("ICAI_PASSWORD", "")

# ── Browser ───────────────────────────────────────────────────────────────────
# HEADLESS=true → invisible Chrome (for servers / GitHub Actions)
# HEADLESS=false → visible Chrome (useful for local debugging)
HEADLESS: bool = os.environ.get("HEADLESS", "true").lower() != "false"

# ── Monitoring cadence ────────────────────────────────────────────────────────
# How often to re-check (seconds). 180 = 3 min, 300 = 5 min.
POLL_INTERVAL_SEC: int = int(os.environ.get("POLL_INTERVAL_SEC", "180"))

# Retry logic on scrape failure
MAX_RETRIES:     int = int(os.environ.get("MAX_RETRIES",     "3"))
RETRY_DELAY_SEC: int = int(os.environ.get("RETRY_DELAY_SEC", "15"))

# How many months forward to scan in the calendar (2 = current + 2 more months)
MONTHS_TO_CHECK: int = int(os.environ.get("MONTHS_TO_CHECK", "3"))

# Explicit page-load timeout for Selenium waits (seconds)
PAGE_TIMEOUT_SEC: int = int(os.environ.get("PAGE_TIMEOUT_SEC", "25"))

# ── State file ────────────────────────────────────────────────────────────────
STATE_FILE: str = os.environ.get("STATE_FILE", "state.json")

# ── Alert behaviour ───────────────────────────────────────────────────────────
# Alert immediately on the very first run if slots already exist
ALERT_ON_FIRST_RUN: bool = os.environ.get("ALERT_ON_FIRST_RUN", "true").lower() != "false"

# Send a "bot is running" startup ping to Telegram
SEND_STARTUP_MESSAGE: bool = os.environ.get("SEND_STARTUP_MESSAGE", "false").lower() == "true"

# Target URL (change only if ICAI moves the portal)
SPOM_URL: str = os.environ.get(
    "SPOM_URL",
    "https://spmt.icai.org/ICAI/LoginAction_showSlotDetails.action",
)
