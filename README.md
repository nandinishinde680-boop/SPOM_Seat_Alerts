# ICAI SPOM Slot Monitor 🔔

Automatically monitors the [ICAI SPOM exam booking portal](https://spmt.icai.org/ICAI/LoginAction_showSlotDetails.action)
and sends a **Telegram alert the moment new exam slots open up**.

Built on Selenium (browser automation) because the calendar is rendered
dynamically by jQuery UI — it cannot be scraped with plain HTTP requests.

---

## How it works

```
Open browser → Select Country/State/City/Centre → Click calendar icon
→ Read td.datepickerHighlight (available) vs td.datepickerSlotsFull (booked)
→ Compare with last saved state → Alert via Telegram if new slots appeared
→ Sleep N minutes → Repeat
```

---

## Quick start (local)

### 1 — Prerequisites

| Requirement | Version |
|-------------|---------|
| Python      | 3.10+   |
| Google Chrome | latest |

> **ChromeDriver is installed automatically** by `webdriver-manager`.
> You do NOT need to download it manually.

### 2 — Clone and install

```bash
git clone https://github.com/YOUR_USER/SPOM-Monitor.git
cd SPOM-Monitor
pip install -r requirements.txt
```

### 3 — Create a Telegram Bot

1. Open Telegram → search **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the token (e.g. `1234567890:ABCdefGHIjklmNOpqrSTUvwxYZ`)
4. Send any message to your new bot (required to activate the chat)
5. Message **@userinfobot** to get your numeric chat ID

### 4 — Configure

```bash
cp .env.example .env
# Edit .env with your values:
```

```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklmNOpqrSTUvwxYZ
TELEGRAM_CHAT_IDS=123456789
```

Everything else has sensible defaults. See `.env.example` for all options.

### 5 — Test your Telegram config

```bash
python main.py --test
```
You should receive a message in Telegram within a few seconds.

### 6 — Debug scrape (visible browser)

```bash
python main.py --debug
```
This opens Chrome visibly so you can watch it navigate the ICAI portal.
No alerts are sent.

### 7 — Start monitoring

```bash
python main.py
```
The monitor will:
- Check slots every 3 minutes (configurable)
- Send an alert immediately if slots are already available (first run)
- Alert again each time *new* slots appear
- Save state to `state.json` between runs to avoid duplicate alerts

---

## Run modes

| Command | Description |
|---------|-------------|
| `python main.py` | Continuous loop (default) |
| `python main.py --once` | Single check, then exit |
| `python main.py --debug` | Visible browser, print results, no alerts |
| `python main.py --test` | Send test Telegram message |
| `python main.py --reset` | Delete state.json (re-alerts on next run) |

---

## Monitor multiple cities

Edit `config.py`:

```python
WATCHLIST = [
    LocationConfig(country="India", state="Maharashtra", city="Mumbai"),
    LocationConfig(country="India", state="Maharashtra", city="Pune"),
    LocationConfig(country="India", state="Delhi",       city="New Delhi"),
]
```

Or monitor specific centres only (instead of all):

```python
LocationConfig(
    country="India", state="Maharashtra", city="Mumbai",
    centres=["Dexit Global Limited"],   # partial name match is fine
)
```

---

## Configuration reference

All options can be set via `.env` or environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | *(required)* | Bot token from @BotFather |
| `TELEGRAM_CHAT_IDS` | *(required)* | Comma-separated Telegram chat IDs |
| `ICAI_USERNAME` | *(empty)* | ICAI login (only if page requires auth) |
| `ICAI_PASSWORD` | *(empty)* | ICAI password |
| `HEADLESS` | `true` | `false` = visible Chrome for debugging |
| `POLL_INTERVAL_SEC` | `180` | Seconds between checks (min 120 recommended) |
| `MONTHS_TO_CHECK` | `3` | Months forward to scan in calendar |
| `MAX_RETRIES` | `3` | Retry attempts on scrape failure |
| `RETRY_DELAY_SEC` | `15` | Seconds between retries |
| `PAGE_TIMEOUT_SEC` | `25` | Selenium element-wait timeout |
| `ALERT_ON_FIRST_RUN` | `true` | Alert if slots exist on the very first run |
| `SEND_STARTUP_MESSAGE` | `false` | Send a "bot started" ping to Telegram |

---

## Deploy on GitHub Actions (free, cloud-hosted)

This is the **recommended** deployment for 24/7 monitoring without keeping
your laptop on.

### Setup

1. Push this project to a **private** GitHub repository
2. Go to repo **Settings → Secrets and variables → Actions**
3. Add these secrets:

| Secret | Value |
|--------|-------|
| `TELEGRAM_BOT_TOKEN` | Your bot token |
| `TELEGRAM_CHAT_IDS` | Your chat ID(s) |
| `ICAI_USERNAME` | *(leave empty if not needed)* |
| `ICAI_PASSWORD` | *(leave empty if not needed)* |

4. GitHub Actions will automatically run every **5 minutes**.

> ⚠️ GitHub Actions has a minimum cron interval of 5 minutes.
> For 2–3 minute checks, use Railway or run locally.

---

## Deploy on Railway (2–3 minute checks)

Railway runs a persistent process (like a VPS), so you get faster polling.

1. Create a free Railway account at [railway.app](https://railway.app)
2. New Project → Deploy from GitHub repo
3. Add environment variables in Railway dashboard
4. Set start command: `python main.py`

Railway will keep the process running 24/7.

---

## Project structure

```
SPOM-Monitor/
├── config.py          ← Edit watchlist and defaults here
├── scraper.py         ← All Selenium automation logic
├── notifier.py        ← Telegram alert functions
├── main.py            ← CLI entry point and monitoring loop
├── requirements.txt
├── .env.example       ← Copy to .env and fill in your values
├── state.json         ← Auto-created; tracks last-seen slot state
└── .github/
    └── workflows/
        └── monitor.yml  ← GitHub Actions schedule
```

---

## DOM reference (from ICAI portal DevTools)

```html
<!-- Available date (green) -->
<td class="datepickerHighlight" data-handler="selectDay"
    data-month="4" data-year="2026">
  <a class="ui-state-default">5</a>
</td>

<!-- Fully booked date (red) -->
<td class="datepickerSlotsFull" data-handler="selectDay"
    data-month="3" data-year="2026">
  <a class="ui-state-default">27</a>
</td>

<!-- Calendar trigger icon -->
<img class="ui-datepicker-trigger" src="..." />
```

`data-month` is **0-indexed** (0 = January … 11 = December), matching
the JavaScript `Date` object convention.

---

## Troubleshooting

**`selenium.common.exceptions.WebDriverException: ChromeDriver not found`**
→ Run `pip install webdriver-manager` and make sure Chrome is installed.

**`TimeoutException` on dropdown**
→ The ICAI portal may be slow. Increase `PAGE_TIMEOUT_SEC=40` in your `.env`.

**No centres found**
→ Run `python main.py --debug` to see the browser navigate live.
   The dropdown ID names may have changed — check DevTools on the page.

**Telegram alert not received**
→ Run `python main.py --test` and check the terminal for error messages.
   Make sure you've sent at least one message to your bot first.

**State keeps re-alerting the same dates**
→ The hash comparison handles this automatically. If you want to reset:
   `python main.py --reset`

---

## Credits

Inspired by the [ITT_OC batch monitor](https://github.com/your-friend/ITT_OC)
by a friend, adapted and extended for the SPOM slot booking calendar.
