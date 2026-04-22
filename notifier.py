"""
notifier.py
-----------
Sends Telegram alerts when new ICAI SPOM exam slots become available.

Prerequisites
-------------
  1. Create a bot via Telegram's @BotFather → /newbot
     Copy the API token → TELEGRAM_BOT_TOKEN in your .env
  2. Start a chat with your new bot (send it any message).
  3. Find your chat ID: message @userinfobot or @RawDataBot
     Copy the numeric ID → TELEGRAM_CHAT_IDS in your .env

Uses only Python stdlib (urllib) — no extra dependencies beyond selenium.
"""

import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List

logger = logging.getLogger(__name__)

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))


# ─── Low-level send ────────────────────────────────────────────────────────────

def _send_message(bot_token: str, chat_id: str, text: str) -> bool:
    """
    Send a single Telegram message.
    Returns True on success, False on any failure.
    text may use basic HTML: <b>, <i>, <code>, <a href="...">.
    """
    if not bot_token or bot_token == "YOUR_BOT_TOKEN_HERE":
        logger.warning("  Telegram bot token not configured — skipping send")
        return False
    if not chat_id or chat_id == "YOUR_CHAT_ID_HERE":
        logger.warning("  Telegram chat ID not configured — skipping send")
        return False

    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload  = urllib.parse.urlencode({
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    try:
        req  = urllib.request.Request(endpoint, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                return True
            logger.warning(f"  Telegram HTTP {resp.status} for chat {chat_id}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error(f"  Telegram HTTP error {e.code}: {body}")
    except urllib.error.URLError as e:
        logger.error(f"  Telegram network error: {e.reason}")
    except Exception as e:
        logger.error(f"  Telegram unexpected error: {e}")

    return False


# ─── Message builders ──────────────────────────────────────────────────────────

def _build_slot_alert(
    new_slots:   Dict[str, List[dict]],
    city:        str,
    state:       str,
    country:     str   = "India",
    is_first_run: bool = False,
) -> str:
    """
    Build the Telegram HTML message for a slot availability alert.

    new_slots: {centre_name: [{"day":5,"month":4,"year":2026,"readable":"05 May 2026"}, ...]}
    """
    ts = datetime.now(IST).strftime("%d %b %Y  %I:%M %p IST")

    if is_first_run:
        header = "📋 <b>ICAI SPOM — Exam Slots Found (Initial Scan)</b>"
        sub    = "Slots were already available when monitoring started."
    else:
        header = "🎉 <b>ICAI SPOM — NEW Exam Slots Available!</b>"
        sub    = "Book immediately — slots fill up fast!"

    total = sum(len(v) for v in new_slots.values())
    lines = [
        header,
        sub,
        "",
        f"📍 <b>Location:</b> {city}, {state}, {country}",
        f"📊 <b>Total new dates:</b> {total}",
        f"🕐 <b>Detected at:</b> {ts}",
        "",
        "─────────────────────────────",
        "📅 <b>Available Exam Dates:</b>",
    ]

    for centre, slots in new_slots.items():
        lines.append(f"\n🏢 <b>{centre}</b>")
        for s in slots:
            lines.append(f"  ✅ {s['readable']}")

    lines += [
        "",
        "─────────────────────────────",
        '🔗 <a href="https://spmt.icai.org/ICAI/LoginAction_showSlotDetails.action">Open ICAI SPOM Portal</a>',
        "",
        "<i>This is an automated alert. Monitor runs every few minutes.</i>",
    ]

    return "\n".join(lines)


def _build_no_slots_notification(city: str, state: str) -> str:
    """Build a message notifying that all slots are currently full."""
    ts = datetime.now(IST).strftime("%d %b %Y  %I:%M %p IST")
    return (
        f"🔴 <b>ICAI SPOM — No Slots Available</b>\n"
        f"📍 {city}, {state}\n"
        f"🕐 Checked at: {ts}\n\n"
        f"All exam slots are currently fully booked.\n"
        f"The monitor will alert you when new slots open."
    )


# ─── Public API ────────────────────────────────────────────────────────────────

def send_slot_alert(
    new_slots:    Dict[str, List[dict]],
    city:         str,
    state:        str,
    bot_token:    str,
    chat_ids:     List[str],
    country:      str  = "India",
    is_first_run: bool = False,
) -> bool:
    """
    Send a slot availability alert to every configured chat ID.
    Returns True only if ALL sends succeeded.
    """
    if not new_slots:
        logger.debug("  send_slot_alert called with empty new_slots — nothing to send")
        return True

    message = _build_slot_alert(new_slots, city, state, country, is_first_run)
    all_ok  = True

    for chat_id in chat_ids:
        ok = _send_message(bot_token, chat_id, message)
        if ok:
            logger.info(f"  Alert sent to chat {chat_id}")
        else:
            logger.error(f"  Failed to send alert to chat {chat_id}")
            all_ok = False

    return all_ok


def send_test_alert(bot_token: str, chat_ids: List[str]) -> bool:
    """
    Send a test message to verify Telegram credentials are working.
    Safe to call at any time — won't affect monitoring state.
    """
    ts  = datetime.now(IST).strftime("%d %b %Y  %I:%M %p IST")
    msg = (
        "✅ <b>ICAI SPOM Monitor — Test Alert</b>\n\n"
        "Your Telegram integration is working correctly!\n\n"
        "The monitor will automatically send an alert to this chat "
        "whenever new ICAI SPOM exam slots become available.\n\n"
        f"🕐 Test sent at: {ts}"
    )
    return all(_send_message(bot_token, cid, msg) for cid in chat_ids)


def send_startup_notification(
    bot_token: str,
    chat_ids:  List[str],
    locations: list,
    interval:  int,
) -> bool:
    """Optional startup ping so you know the bot is live."""
    loc_text = "\n".join(
        f"  • {loc.city}, {loc.state}" for loc in locations
    )
    msg = (
        "🚀 <b>ICAI SPOM Monitor Started</b>\n\n"
        f"Monitoring locations:\n{loc_text}\n\n"
        f"⏱ Check interval: every {interval}s\n\n"
        "<i>You'll receive an alert when exam slots open up.</i>"
    )
    return all(_send_message(bot_token, cid, msg) for cid in chat_ids)
