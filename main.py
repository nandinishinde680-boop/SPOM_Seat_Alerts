"""
main.py
-------
Entry point for the ICAI SPOM Slot Monitor.

Run modes
---------
  python main.py             → Continuous monitoring loop (default)
  python main.py --once      → Single check run, then exit
  python main.py --test      → Send a test Telegram alert and exit
  python main.py --debug     → Scrape and print results (no alerts, visible browser)
  python main.py --reset     → Clear saved state (forces re-alert on next run)

Environment variables (set in .env or shell)
--------------------------------------------
  TELEGRAM_BOT_TOKEN   Required. From @BotFather.
  TELEGRAM_CHAT_IDS    Required. Comma-separated Telegram chat IDs.
  ICAI_USERNAME        Optional. ICAI portal login (if page requires auth).
  ICAI_PASSWORD        Optional. ICAI portal password.
  HEADLESS             Optional. true/false. Default: true.
  POLL_INTERVAL_SEC    Optional. Seconds between checks. Default: 180.
  MONTHS_TO_CHECK      Optional. Calendar months to scan. Default: 3.
  ALERT_ON_FIRST_RUN   Optional. true/false. Default: true.
  SEND_STARTUP_MESSAGE Optional. true/false. Default: false.

Deployment
----------
  Local:          python main.py
  GitHub Actions: see .github/workflows/monitor.yml
  Railway/Render: set start command to "python main.py"
"""

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Dict

# Load .env file if python-dotenv is installed (not required)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import config
from scraper import scrape_slots, compute_slots_hash, diff_slots
from notifier import (
    send_slot_alert,
    send_test_alert,
    send_startup_notification,
)
# Read State and City from GitHub Action inputs
USER_STATE = os.getenv('USER_STATE', 'Maharashtra')
USER_CITY = os.getenv('USER_CITY', 'Pune')
# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ─── Graceful shutdown ────────────────────────────────────────────────────────
_running = True

def _handle_signal(signum, _frame):
    global _running
    logger.info(f"Signal {signum} received — shutting down cleanly...")
    _running = False

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# ─── State management ─────────────────────────────────────────────────────────

def load_state() -> dict:
    path = Path(config.STATE_FILE)
    if path.exists():
        try:
            with open(path, "r") as f:
                data = json.load(f)
            logger.info(f"Loaded state from {config.STATE_FILE} ({len(data)} entr{'y' if len(data)==1 else 'ies'})")
            return data
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"State file unreadable ({e}) — starting fresh")
    return {}


def save_state(state: dict):
    try:
        with open(config.STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        logger.debug(f"State saved to {config.STATE_FILE}")
    except IOError as e:
        logger.error(f"Could not save state: {e}")


def _state_key(country: str, state: str, city: str) -> str:
    return f"{country}|{state}|{city}"


# ─── Core check cycle ─────────────────────────────────────────────────────────

def run_check(state: Dict, verbose: bool = False) -> bool:
    """
    Run one complete monitoring cycle across all WATCHLIST locations.

    Returns True if any Telegram alert was sent during this cycle.
    Updates `state` in-place and persists it to disk.
    """
    any_alert_sent = False
# Use the inputs provided in the GitHub Action UI instead of the config file
    watchlist = [{"country": "India", "state": USER_STATE, "city": USER_CITY, "centres": []}]
    
    for loc_data in watchlist:
      # Define the variable that caused the error
        is_first_run = not bool(state)
        # Create a simple object-like structure to keep the rest of the code working
        class Loc: pass
        loc = Loc()
        loc.country = loc_data["country"]
        loc.state = loc_data["state"]
        loc.city = loc_data["city"]
        loc.centres = loc_data["centres"]

        key          = _state_key(loc.country, loc.state, loc.city)
        prev_entry   = state.get(key, {})
  

        logger.info(
            f"═══ {loc.country} / {loc.state} / {loc.city} "
            f"{'(first run)' if is_first_run else ''} ═══"
        )

        # ── Scrape with retries ────────────────────────────────────────────────
        results = None
        last_error = None

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                logger.info(
                    f"  Scraping... (attempt {attempt}/{config.MAX_RETRIES})"
                )
                results = scrape_slots(
                    country         = loc.country,
                    state           = loc.state,
                    city            = loc.city,
                    centres         = loc.centres,
                    headless        = config.HEADLESS,
                    username        = config.ICAI_USERNAME,
                    password        = config.ICAI_PASSWORD,
                    months_to_check = config.MONTHS_TO_CHECK,
                    page_timeout    = config.PAGE_TIMEOUT_SEC,
                    url             = config.SPOM_URL,
                )
                last_error = None
                break

            except Exception as e:
                last_error = e
                logger.error(f"  Attempt {attempt} failed: {e}")
                if attempt < config.MAX_RETRIES:
                    logger.info(f"  Retrying in {config.RETRY_DELAY_SEC}s...")
                    time.sleep(config.RETRY_DELAY_SEC)

        if results is None:
            logger.error(
                f"  All {config.MAX_RETRIES} attempts failed for {key}. "
                f"Last error: {last_error}. Skipping this location."
            )
            continue

        # ── Summarise findings ─────────────────────────────────────────────────
        new_hash    = compute_slots_hash(results)
        changed     = (new_hash != prev_hash)
        total_slots = sum(len(v) for v in results.values())

        if total_slots > 0:
            logger.info(f"  Found {total_slots} available slot(s) total:")
            for centre, slots in results.items():
                if slots:
                    logger.info(
                        f"    {centre}: "
                        + ", ".join(s["readable"] for s in slots)
                    )
        else:
            logger.info("  No slots available currently")

        # ── Decide whether to alert ────────────────────────────────────────────
        if is_first_run:
            logger.info("  First run — establishing baseline state")
            if total_slots > 0 and config.ALERT_ON_FIRST_RUN:
                logger.info(f"  Slots found on first run → sending alert")
                ok = send_slot_alert(
                    new_slots    = results,
                    city         = loc.city,
                    state        = loc.state,
                    country      = loc.country,
                    bot_token    = config.TELEGRAM_BOT_TOKEN,
                    chat_ids     = config.TELEGRAM_CHAT_IDS,
                    is_first_run = True,
                )
                if ok:
                    any_alert_sent = True
            elif total_slots == 0:
                logger.info(
                    "  No slots on first run — "
                    "will alert when slots become available"
                )
            else:
                logger.info(
                    "  ALERT_ON_FIRST_RUN is false — "
                    "baseline saved silently"
                )

        elif changed:
            newly_available = diff_slots(prev_results, results)
            newly_count     = sum(len(v) for v in newly_available.values())

            if newly_count > 0:
                logger.info(
                    f"  ✨ {newly_count} NEW slot(s) appeared → sending alert!"
                )
                ok = send_slot_alert(
                    new_slots = newly_available,
                    city      = loc.city,
                    state     = loc.state,
                    country   = loc.country,
                    bot_token = config.TELEGRAM_BOT_TOKEN,
                    chat_ids  = config.TELEGRAM_CHAT_IDS,
                )
                if ok:
                    any_alert_sent = True
            else:
                # Slots changed but only existing ones were removed
                removed = sum(len(v) for v in prev_results.values()) - total_slots
                logger.info(
                    f"  State changed but no new slots "
                    f"({removed} slot(s) removed) — no alert"
                )
        else:
            logger.info(
                f"  No change detected (hash: {new_hash[:12]}…)"
            )

        # ── Persist updated state ──────────────────────────────────────────────
        state[key] = {
            "hash":         new_hash,
            "results":      results,
            "last_checked": time.strftime("%Y-%m-%d %H:%M:%S"),
            "country":      loc.country,
            "state":        loc.state,
            "city":         loc.city,
        }
        save_state(state)

    return any_alert_sent


# ─── CLI mode implementations ─────────────────────────────────────────────────

def mode_monitor():
    """Continuous monitoring loop."""
    global _running

    logger.info("╔══════════════════════════════════════════════╗")
    logger.info("║    ICAI SPOM Slot Monitor — Starting Up      ║")
    logger.info("╚══════════════════════════════════════════════╝")
    logger.info(f"  Monitoring {len(config.WATCHLIST)} location(s)")
    logger.info(f"  Poll interval : {config.POLL_INTERVAL_SEC}s ({config.POLL_INTERVAL_SEC//60}m {config.POLL_INTERVAL_SEC%60}s)")
    logger.info(f"  Months ahead  : {config.MONTHS_TO_CHECK}")
    logger.info(f"  Headless mode : {config.HEADLESS}")
    logger.info(f"  State file    : {config.STATE_FILE}")
    logger.info(f"  Max retries   : {config.MAX_RETRIES}")

    for loc in config.WATCHLIST:
        logger.info(f"  Watch: {loc.country} / {loc.state} / {loc.city}")

    if config.SEND_STARTUP_MESSAGE:
        send_startup_notification(
            config.TELEGRAM_BOT_TOKEN,
            config.TELEGRAM_CHAT_IDS,
            config.WATCHLIST,
            config.POLL_INTERVAL_SEC,
        )

    state = load_state()

    while _running:
        try:
            logger.info("─── Checking slots...")
            run_check(state)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Unexpected monitor error: {e}", exc_info=True)

        if not _running:
            break

        logger.info(
            f"Sleeping {config.POLL_INTERVAL_SEC}s until next check. "
            f"(Ctrl+C to stop)"
        )
        # Sleep in 1-second increments so SIGTERM is handled quickly
        for _ in range(config.POLL_INTERVAL_SEC):
            if not _running:
                break
            time.sleep(1)

    logger.info("Monitor stopped. Goodbye!")


def mode_once():
    """Single check cycle — useful for cron / GitHub Actions."""
    logger.info("Running single check cycle...")
    state = load_state()
    run_check(state)
    logger.info("Single check complete.")


def mode_debug():
    """Scrape and print without sending any alerts (visible browser)."""
    print("\n" + "═" * 60)
    print("  DEBUG MODE — scraping with visible browser, no alerts")
    print("═" * 60 + "\n")

    original_headless    = config.HEADLESS
    config.HEADLESS      = False       # force visible for debugging

    for loc in config.WATCHLIST:
        print(f"\n▶ {loc.country} / {loc.state} / {loc.city}")
        try:
            results = scrape_slots(
                country         = loc.country,
                state           = loc.state,
                city            = loc.city,
                centres         = loc.centres,
                headless        = False,
                username        = config.ICAI_USERNAME,
                password        = config.ICAI_PASSWORD,
                months_to_check = config.MONTHS_TO_CHECK,
                page_timeout    = config.PAGE_TIMEOUT_SEC,
                url             = config.SPOM_URL,
            )
            if not results:
                print("  No results returned (no centres found?)")
                continue

            for centre, slots in results.items():
                print(f"\n  🏢 {centre}")
                if slots:
                    for s in slots:
                        print(f"     ✅ {s['readable']}")
                else:
                    print("     ❌ No available slots")

        except Exception as e:
            print(f"  ERROR: {e}")

    config.HEADLESS = original_headless
    print()


def mode_test():
    """Send a test Telegram message."""
    print("Sending test Telegram alert...")
    ok = send_test_alert(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_IDS)
    if ok:
        print("✅ Test alert sent! Check your Telegram.")
    else:
        print("❌ Failed to send — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS")
    return 0 if ok else 1


def mode_reset():
    """Clear saved state so the next run treats everything as new."""
    path = Path(config.STATE_FILE)
    if path.exists():
        path.unlink()
        print(f"✅ State file '{config.STATE_FILE}' deleted.")
        print("   Next run will re-establish the baseline and alert on any found slots.")
    else:
        print(f"ℹ  No state file found at '{config.STATE_FILE}' — nothing to reset.")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--debug" in args:
        mode_debug()
    elif "--test" in args:
        sys.exit(mode_test())
    elif "--once" in args:
        mode_once()
    elif "--reset" in args:
        mode_reset()
    else:
        mode_monitor()
