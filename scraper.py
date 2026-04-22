"""
scraper.py
----------
Selenium-based automation to monitor ICAI SPOM exam slot availability.

Target URL: https://spmt.icai.org/ICAI/LoginAction_showSlotDetails.action

How the page works
------------------
  1. Page shows chained dropdowns: Country → State → City → Test Centre.
  2. Selecting each dropdown triggers an AJAX/JS reload of the next.
  3. After selecting a Test Centre, an Exam Date input with a jQuery UI
     Datepicker calendar icon (img.ui-datepicker-trigger) appears.
  4. Clicking the calendar icon loads the datepicker widget dynamically —
     dates are NOT present in the initial HTML.
  5. Available dates have class  → td.datepickerHighlight
     Fully booked dates have    → td.datepickerSlotsFull
     Each <td> carries          → data-month (0-indexed) and data-year

DOM references (confirmed from browser DevTools screenshots):
  Available cell:  <td class="datepickerHighlight" data-handler="selectDay"
                       data-month="4" data-year="2026"><a ...>5</a></td>
  Booked cell:     <td class="datepickerSlotsFull"  data-handler="selectDay"
                       data-month="3" data-year="2026">...</td>
  Calendar div:    #ui-datepicker-div
  Next month btn:  .ui-datepicker-next inside #ui-datepicker-div

Notes
-----
  - All waits use WebDriverWait (explicit). time.sleep is only used for
    short post-interaction settle delays (< 1 s) where JS callbacks fire.
  - The browser is quit in a finally block — no leaked processes.
  - compute_slots_hash / diff_slots are pure functions used by main.py
    to detect changes and find newly available slots.
"""

import hashlib
import json
import logging
import time
from typing import Dict, List, Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

# 0-indexed month names (matching jQuery UI Datepicker's data-month attribute)
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Possible HTML id/name fragments for each dropdown — tried in order
_DROPDOWN_HINTS = {
    "country": ["country", "Country", "ddlCountry", "countryId"],
    "state":   ["state",   "State",   "ddlState",   "stateId"],
    "city":    ["city",    "City",    "ddlCity",     "cityId"],
    "centre":  ["centre",  "Center",  "testCentre",  "testCenter",
                "testcentre", "center", "examcentre", "examCenter"],
}


# ─── Browser setup ─────────────────────────────────────────────────────────────

def _make_driver(headless: bool = True) -> webdriver.Chrome:
    """Create and return a Chrome WebDriver, auto-installing the driver if needed."""
    opts = Options()

    if headless:
        opts.add_argument("--headless=new")          # modern headless flag

    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--log-level=3")               # suppress Chrome console noise
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # Try webdriver-manager first (auto-downloads matching ChromeDriver)
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        logger.debug("  Chrome started via webdriver-manager")
    except Exception:
        # Fall back to system PATH ChromeDriver
        driver = webdriver.Chrome(options=opts)
        logger.debug("  Chrome started via system ChromeDriver")

    driver.implicitly_wait(0)   # use explicit waits only — never mix
    return driver


# ─── Dropdown helpers ──────────────────────────────────────────────────────────

def _find_select_element(driver: webdriver.Chrome, hints: List[str]) -> Optional[webdriver.remote.webelement.WebElement]:
    """
    Find a <select> element whose id or name contains any of the hint strings
    (case-insensitive). Returns the first match or None.
    """
    for hint in hints:
        h = hint.lower()
        for sel in driver.find_elements(By.TAG_NAME, "select"):
            eid  = (sel.get_attribute("id")   or "").lower()
            name = (sel.get_attribute("name") or "").lower()
            if h in eid or h in name:
                return sel
    return None


def _wait_for_select_populated(
    driver: webdriver.Chrome,
    hints: List[str],
    min_options: int = 2,
    timeout: int = 15,
) -> webdriver.remote.webelement.WebElement:
    """
    Wait until the target <select> has at least min_options non-placeholder
    options, then return the element.
    """
    def condition(drv):
        el = _find_select_element(drv, hints)
        if el is None:
            return False
        # Count options that have a real value (skip "-- Select --" placeholders)
        real_opts = [
            o for o in Select(el).options
            if o.get_attribute("value") and o.text.strip()
        ]
        return len(real_opts) >= min_options

    WebDriverWait(driver, timeout).until(condition)
    return _find_select_element(driver, hints)


def _select_option(
    el: webdriver.remote.webelement.WebElement,
    value_text: str,
    step: str,
) -> str:
    """
    Select the option whose visible text *contains* value_text (case-insensitive).
    Returns the matched text or raises ValueError.
    """
    sel     = Select(el)
    options = [o.text.strip() for o in sel.options if o.text.strip()]
    match   = next((o for o in options if value_text.lower() in o.lower()), None)

    if match is None:
        raise ValueError(
            f"[{step}] '{value_text}' not found. Available: {options}"
        )

    sel.select_by_visible_text(match)
    logger.info(f"  [{step}] Selected: '{match}'")
    return match


# ─── Login helper ──────────────────────────────────────────────────────────────

def _try_login(driver: webdriver.Chrome, username: str, password: str):
    """
    If the browser has been redirected to a login page, attempt to log in
    using the supplied credentials. Silently returns if no login form found.
    """
    current = driver.current_url.lower()
    if "login" not in current and "signin" not in current:
        return

    logger.info("  Login page detected — attempting login...")

    # Try common ICAI field IDs for the username
    for uid in ["username", "userid", "UserID", "txtUserName", "txtUsername", "uname"]:
        try:
            el = driver.find_element(By.ID, uid)
            el.clear()
            el.send_keys(username)
            logger.debug(f"    Username entered in #{uid}")
            break
        except NoSuchElementException:
            continue

    # Try common ICAI field IDs for the password
    for pid in ["password", "Password", "txtPassword", "pwd", "pass"]:
        try:
            el = driver.find_element(By.ID, pid)
            el.clear()
            el.send_keys(password)
            logger.debug(f"    Password entered in #{pid}")
            break
        except NoSuchElementException:
            continue

    # Click submit
    for xpath in [
        "//input[@type='submit']",
        "//button[@type='submit']",
        "//input[contains(@value,'Login') or contains(@value,'login')]",
        "//button[contains(text(),'Login') or contains(text(),'Sign')]",
    ]:
        try:
            driver.find_element(By.XPATH, xpath).click()
            time.sleep(2.0)
            logger.info(f"  Post-login URL: {driver.current_url}")
            return
        except NoSuchElementException:
            continue

    logger.warning("  Could not find login submit button — manual login may be needed")


# ─── Calendar helpers ──────────────────────────────────────────────────────────

def _open_calendar(driver: webdriver.Chrome, timeout: int = 15):
    """
    Click the jQuery UI datepicker trigger icon and wait for the calendar
    widget (#ui-datepicker-div) to become visible.
    """
    trigger_selectors = [
        (By.CSS_SELECTOR, "img.ui-datepicker-trigger"),
        (By.CSS_SELECTOR, "button.ui-datepicker-trigger"),
        (By.CSS_SELECTOR, ".ui-datepicker-trigger"),
        (By.XPATH,        "//img[contains(@class,'ui-datepicker-trigger')]"),
        (By.XPATH,        "//a[contains(@class,'ui-datepicker-trigger')]"),
    ]

    wait = WebDriverWait(driver, timeout)
    clicked = False

    for by, locator in trigger_selectors:
        try:
            trigger = wait.until(EC.element_to_be_clickable((by, locator)))
            driver.execute_script("arguments[0].click();", trigger)  # JS click avoids interception
            clicked = True
            logger.debug("  Calendar trigger clicked")
            break
        except (TimeoutException, ElementClickInterceptedException, WebDriverException):
            continue

    if not clicked:
        raise RuntimeError(
            "Could not find or click the calendar trigger. "
            "The page structure may have changed."
        )

    # Wait for the datepicker widget to appear
    wait.until(EC.visibility_of_element_located((By.ID, "ui-datepicker-div")))
    time.sleep(0.6)  # let jQuery UI finish rendering the month tables
    logger.debug("  Calendar widget visible")


def _parse_visible_months(driver: webdriver.Chrome) -> List[Tuple[int, int, int]]:
    """
    Extract all (day, month_0indexed, year) tuples from currently visible
    datepickerHighlight cells in the open jQuery UI datepicker widget.

    data-month is 0-indexed (0=January … 11=December), same as JS Date.
    """
    slots: List[Tuple[int, int, int]] = []

    try:
        picker = driver.find_element(By.ID, "ui-datepicker-div")
    except NoSuchElementException:
        logger.warning("  #ui-datepicker-div not found — calendar may have closed")
        return slots

    highlight_cells = picker.find_elements(By.CSS_SELECTOR, "td.datepickerHighlight")
    logger.debug(f"  datepickerHighlight cells visible: {len(highlight_cells)}")

    for cell in highlight_cells:
        try:
            raw_month = cell.get_attribute("data-month")
            raw_year  = cell.get_attribute("data-year")

            if not raw_month or not raw_year:
                continue

            month = int(raw_month)   # 0-indexed
            year  = int(raw_year)

            # Date number lives inside <a> (clickable) or <span> (disabled)
            try:
                day_text = cell.find_element(By.TAG_NAME, "a").text.strip()
            except NoSuchElementException:
                try:
                    day_text = cell.find_element(By.TAG_NAME, "span").text.strip()
                except NoSuchElementException:
                    day_text = cell.text.strip()

            if day_text.isdigit():
                slots.append((int(day_text), month, year))

        except (StaleElementReferenceException, ValueError):
            continue

    return slots


def _scan_calendar_months(
    driver: webdriver.Chrome,
    months_forward: int = 2,
) -> List[Tuple[int, int, int]]:
    """
    Collect available slot tuples from the current view and then navigate
    forward month-by-month to gather more data.
    Returns a deduplicated, sorted list of (day, month_0idx, year) tuples.
    """
    all_raw: List[Tuple[int, int, int]] = []
    wait = WebDriverWait(driver, 10)

    # Current view (may already show 2 months side-by-side)
    all_raw.extend(_parse_visible_months(driver))

    for i in range(months_forward):
        try:
            next_btn = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "#ui-datepicker-div .ui-datepicker-next")
                )
            )
            # Check the button isn't disabled (end of booking window)
            if "ui-state-disabled" in (next_btn.get_attribute("class") or ""):
                logger.debug(f"  Next-month button disabled at step {i+1} — stopping")
                break
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(0.7)
            all_raw.extend(_parse_visible_months(driver))
        except (TimeoutException, WebDriverException) as e:
            logger.debug(f"  Could not navigate to next month (step {i+1}): {e}")
            break

    # Deduplicate and sort
    seen = set()
    unique: List[Tuple[int, int, int]] = []
    for item in all_raw:
        if item not in seen:
            seen.add(item)
            unique.append(item)

    unique.sort(key=lambda t: (t[2], t[1], t[0]))   # sort by year, month, day
    return unique


def _close_calendar(driver: webdriver.Chrome):
    """Dismiss the calendar by pressing Escape or clicking the page body."""
    try:
        from selenium.webdriver.common.keys import Keys
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        time.sleep(0.3)
    except Exception:
        try:
            driver.execute_script(
                "var el = document.getElementById('ui-datepicker-div');"
                "if (el) el.style.display = 'none';"
            )
        except Exception:
            pass


def _get_centre_options(driver: webdriver.Chrome) -> List[str]:
    """Return all meaningful Test Centre option texts."""
    el = _find_select_element(driver, _DROPDOWN_HINTS["centre"])
    if el is None:
        return []
    return [
        o.text.strip()
        for o in Select(el).options
        if o.text.strip() and o.get_attribute("value")
    ]


# ─── Main public scraping function ────────────────────────────────────────────

def scrape_slots(
    country:         str,
    state:           str,
    city:            str,
    centres:         List[str],
    headless:        bool = True,
    username:        str  = "",
    password:        str  = "",
    months_to_check: int  = 3,
    page_timeout:    int  = 25,
    url:             str  = "https://spmt.icai.org/ICAI/LoginAction_showSlotDetails.action",
) -> Dict[str, List[dict]]:
    """
    Open the ICAI SPOM booking page, navigate the chained dropdowns, open
    the datepicker for each Test Centre, and collect all available dates.

    Returns:
        {
          "Dexit Global Limited - Mumbai": [
              {"day": 5, "month": 4, "year": 2026, "readable": "05 May 2026"},
              ...
          ],
          ...
        }

    Raises an exception on unrecoverable errors (caller should retry).
    """
    driver  = _make_driver(headless=headless)
    results: Dict[str, List[dict]] = {}

    try:
        # ── Navigate to target URL ─────────────────────────────────────────────
        logger.info(f"  Opening: {url}")
        driver.get(url)
        wait = WebDriverWait(driver, page_timeout)

        # Wait for the page body and at least one <select>
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # Handle login redirect if credentials supplied
        if username and password:
            _try_login(driver, username, password)
            if driver.current_url.rstrip("/") != url.rstrip("/"):
                driver.get(url)
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # Wait for at least the Country dropdown to exist
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "select")))
        logger.debug(f"  Page loaded: {driver.title}")

        # ── Country ────────────────────────────────────────────────────────────
        logger.info(f"  Selecting Country: {country}")
        country_el = _wait_for_select_populated(
            driver, _DROPDOWN_HINTS["country"], min_options=1, timeout=page_timeout
        )
        if country_el is None:
            raise RuntimeError("Country dropdown not found on page")
        _select_option(country_el, country, "Country")
        time.sleep(1.2)   # wait for State dropdown to populate via JS

        # ── State ──────────────────────────────────────────────────────────────
        logger.info(f"  Selecting State: {state}")
        state_el = _wait_for_select_populated(
            driver, _DROPDOWN_HINTS["state"], min_options=2, timeout=page_timeout
        )
        if state_el is None:
            raise RuntimeError("State dropdown not found or not populated")
        _select_option(state_el, state, "State")
        time.sleep(1.2)   # wait for City dropdown

        # ── City ───────────────────────────────────────────────────────────────
        logger.info(f"  Selecting City: {city}")
        city_el = _wait_for_select_populated(
            driver, _DROPDOWN_HINTS["city"], min_options=2, timeout=page_timeout
        )
        if city_el is None:
            raise RuntimeError("City dropdown not found or not populated")
        _select_option(city_el, city, "City")
        time.sleep(1.2)   # wait for Test Centre dropdown

        # ── Discover available centres ─────────────────────────────────────────
        logger.debug("  Waiting for Test Centre dropdown to populate...")
        _wait_for_select_populated(
            driver, _DROPDOWN_HINTS["centre"], min_options=1, timeout=page_timeout
        )
        all_centres = _get_centre_options(driver)

        if not all_centres:
            logger.warning("  No Test Centre options found — nothing to check")
            return {}

        logger.info(f"  Found {len(all_centres)} centre(s)")
        for c in all_centres:
            logger.debug(f"    • {c}")

        # Determine which centres to iterate
        if "all" in [c.lower() for c in centres]:
            centres_to_check = all_centres
        else:
            centres_to_check = [
                avail for avail in all_centres
                if any(wanted.lower() in avail.lower() for wanted in centres)
            ]
            if not centres_to_check:
                logger.warning(
                    f"  None of the requested centres {centres} matched "
                    f"available options {all_centres}"
                )
                return {}

        # ── Per-centre calendar scrape ─────────────────────────────────────────
        for centre_name in centres_to_check:
            logger.info(f"  ─── Checking centre: {centre_name}")

            try:
                # Re-fetch the centre element (may be stale after prior iteration)
                centre_el = _find_select_element(driver, _DROPDOWN_HINTS["centre"])
                if centre_el is None:
                    # The page may have reset — re-select City first
                    logger.debug("  Centre dropdown missing — re-selecting City...")
                    city_el2 = _find_select_element(driver, _DROPDOWN_HINTS["city"])
                    if city_el2:
                        _select_option(city_el2, city, "City-retry")
                        time.sleep(1.2)
                    centre_el = _wait_for_select_populated(
                        driver, _DROPDOWN_HINTS["centre"], min_options=1,
                        timeout=page_timeout
                    )

                if centre_el is None:
                    logger.error(f"  Cannot find centre dropdown for '{centre_name}' — skipping")
                    results[centre_name] = []
                    continue

                _select_option(centre_el, centre_name, f"Centre")
                time.sleep(0.6)

                # Open the calendar icon next to the Exam Date field
                _open_calendar(driver, timeout=page_timeout)

                # Collect available slots across configured months
                raw_slots = _scan_calendar_months(driver, months_forward=months_to_check)

                # Format results
                slot_list = [
                    {
                        "day":      day,
                        "month":    month,    # 0-indexed (4 = May)
                        "year":     year,
                        "readable": f"{day:02d} {MONTH_NAMES[month]} {year}",
                    }
                    for (day, month, year) in raw_slots
                ]

                results[centre_name] = slot_list

                if slot_list:
                    logger.info(
                        f"  ✅ {len(slot_list)} available slot(s): "
                        + ", ".join(s["readable"] for s in slot_list)
                    )
                else:
                    logger.info(f"  ❌ No available slots for {centre_name}")

            except Exception as e:
                logger.error(f"  Error processing centre '{centre_name}': {e}", exc_info=True)
                results[centre_name] = []

            finally:
                _close_calendar(driver)

    except Exception as e:
        logger.error(f"Scrape failed: {e}", exc_info=True)
        raise

    finally:
        driver.quit()
        logger.debug("  Browser closed cleanly")

    return results


# ─── State / diff helpers ──────────────────────────────────────────────────────

def compute_slots_hash(results: Dict[str, List[dict]]) -> str:
    """
    Produce a stable SHA-256 hash of a results dict.
    Centre names and slot lists are both sorted so that reordering does NOT
    produce a different hash (avoids false-positive change alerts).
    """
    normalised = {
        centre: sorted(slots, key=lambda s: (s["year"], s["month"], s["day"]))
        for centre, slots in sorted(results.items())
    }
    serialized = json.dumps(normalised, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def diff_slots(
    old_results: Dict[str, List[dict]],
    new_results: Dict[str, List[dict]],
) -> Dict[str, List[dict]]:
    """
    Return only *newly available* slots — i.e. slots present in new_results
    but absent from old_results. Used to avoid re-alerting on unchanged dates.

    Returns: {centre_name: [new_slot, ...]} — empty centres are omitted.
    """
    new_slots: Dict[str, List[dict]] = {}

    for centre, slots in new_results.items():
        old_keys = {
            (s["day"], s["month"], s["year"])
            for s in old_results.get(centre, [])
        }
        newly_open = [
            s for s in slots
            if (s["day"], s["month"], s["year"]) not in old_keys
        ]
        if newly_open:
            new_slots[centre] = newly_open

    return new_slots
