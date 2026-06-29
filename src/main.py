"""
main.py — eGramSwaraj Activity Planning Bot (v5)

Workflow:
  1. Launch Chromium (non-headless — you log in manually).
  2. Open https://egramswaraj.gov.in
  3. You log in and navigate to addactivity.htm.
  4. Bot detects the form is ready, prints record count.
  5. You press ENTER to start.
  6. Bot processes each pending Excel row:
       • Navigates to addactivity.htm (same session, same cookies).
       • Detects session expiry → pauses for re-login → resumes.
       • Fills all form fields in AJAX dependency order.
       • Optionally clicks Save (submit_form: true in config).
       • Writes result to output Excel with full styling.
  7. Prints final summary.

Usage:
    python src/main.py
    python src/main.py --config config/config.json
    python src/main.py --rows 0,1,2     # 0-based row indices
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import (
    sync_playwright, Browser, BrowserContext, Page,
    TimeoutError as PlaywrightTimeout,
)

sys.path.insert(0, str(Path(__file__).parent))

from logger        import setup_logger
from excel_reader  import ExcelReader
from form_filler   import FormFiller, RETRY_DELAY
from result_writer import ResultWriter

LOGIN_URL  = "https://egramswaraj.gov.in"
FORM_URL   = "https://egramswaraj.gov.in/addactivity.htm"

FORM_READY_TIMEOUT   = 30_000   # ms
SESSION_POLL_MS      = 500      # ms
MAX_SESSION_WAIT_MIN = 10       # minutes


# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════

def load_config(path: str = "config/config.json") -> dict:
    with open(path) as f:
        cfg = json.load(f)
    cfg["headless"]   = False   # always non-headless for manual login
    cfg["login_url"]  = LOGIN_URL
    cfg["target_url"] = FORM_URL
    return cfg


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="eGramSwaraj Activity Planning Bot")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--rows",   default=None,
                    help="0-based row indices to process, e.g. '0,1,2'")
    return ap.parse_args()


# ══════════════════════════════════════════════════════════════════════════════
# Session helpers
# ══════════════════════════════════════════════════════════════════════════════

def _session_expired(page: Page) -> bool:
    url = page.url.lower()
    return (
        "login" in url
        or url.endswith("egramswaraj.gov.in/")
        or url.endswith("egramswaraj.gov.in")
        or "index" in url
    )


def _form_ready(page: Page, timeout_ms: int = FORM_READY_TIMEOUT) -> None:
    """Block until #themeId is visible (confirms form has loaded)."""
    page.wait_for_selector("#themeId", state="visible", timeout=timeout_ms)


def _goto_form(page: Page, timeout_ms: int = FORM_READY_TIMEOUT) -> None:
    page.goto(FORM_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    _form_ready(page, timeout_ms)


def _wait_for_login(page: Page, log) -> None:
    """Poll until user is on addactivity.htm with form loaded."""
    deadline = time.time() + MAX_SESSION_WAIT_MIN * 60
    log.info("Waiting for manual login -> addactivity.htm ...")
    while True:
        if time.time() > deadline:
            raise TimeoutError(
                f"No login detected after {MAX_SESSION_WAIT_MIN} min."
            )
        if "addactivity.htm" in page.url.lower():
            try:
                _form_ready(page, 5_000)
                log.info("Form ready at: %s", page.url)
                return
            except Exception:
                pass
        page.wait_for_timeout(SESSION_POLL_MS)


def _handle_expiry(page: Page, log, row: int) -> None:
    log.warning("Session expired at row %d | URL: %s", row, page.url)
    print("\n" + "=" * 60)
    print("  [SESSION] SESSION EXPIRED")
    print("  Please log in again in the browser window.")
    print(f"  Then navigate to: {FORM_URL}")
    print("  Automation will resume automatically.")
    print("=" * 60 + "\n")
    _wait_for_login(page, log)
    log.info("Session restored. Resuming from row %d.", row)


# ══════════════════════════════════════════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════════════════════════════════════════

def run(config: dict, filter_rows: list[int] | None = None) -> None:
    log = setup_logger(config["log_file"])
    log.info("=" * 60)
    log.info("eGramSwaraj Bot START (v5)")
    log.info("=" * 60)

    reader = ExcelReader(config["input_file"])
    reader.load()
    writer = ResultWriter(config["input_file"], config["output_file"])

    total   = reader.total_rows()
    done    = reader.success_count()
    pending = total - done
    log.info("rows: total=%d done=%d pending=%d", total, done, pending)

    t0            = time.time()
    success_count = done
    fail_count    = 0

    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        ctx: BrowserContext = browser.new_context(
            viewport=None, no_viewport=True,
        )
        page: Page = ctx.new_page()
        page.set_default_timeout(config.get("selector_timeout_ms", 15_000))

        # ── Open login page ───────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("  eGramSwaraj Activity Planning Bot  (v5)")
        print("=" * 60)
        print(f"\n  [BROWSER] Opening {LOGIN_URL} ...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded",
                  timeout=config.get("page_timeout_ms", 30_000))

        print("  [OK] Browser ready.")
        print("\n  [LOGIN] Please log in and navigate to:")
        print(f"         {FORM_URL}")
        print("\n  The bot will wait automatically.\n")

        # ── Wait for manual login ─────────────────────────────────────────────
        _wait_for_login(page, log)

        # -- ENTER gate ---------------------------------------------------------------
        print("\n" + "-" * 60)
        print(f"  [OK] Form detected")
        print(f"  [DATA] Pending records : {pending}")
        print(f"  [FILE] Output file     : {config['output_file']}")
        print("-" * 60)
        try:
            input("\n  [READY] Press ENTER to start automation ... ")
        except EOFError:
            pass
        print()

        filler = FormFiller(
            page=page,
            config=config,
            screenshots_dir=config.get("screenshots_dir", "screenshots"),
        )

        # ── Process rows ──────────────────────────────────────────────────────
        for row_index, record in reader.pending_rows():
            if filter_rows is not None and row_index not in filter_rows:
                continue

            label = (record.get("activity_name") or f"Row-{row_index}")[:60]
            log.info("-- row %d | %s", row_index, label)
            print(f"  [ROW] [{row_index + 1}/{total}]  {label}")

            last_err  = ""
            succeeded = False

            for attempt in range(1, config.get("retry_count", 3) + 1):
                try:
                    _goto_form(page, config.get("page_timeout_ms", 30_000))

                    if _session_expired(page):
                        _handle_expiry(page, log, row_index)
                        _goto_form(page, config.get("page_timeout_ms", 30_000))

                    result    = filler.fill_form(record, row_index)
                    succeeded = True

                    if config.get("take_success_screenshots", False):
                        filler.take_screenshot(f"row_{row_index}_success")

                    writer.write_result(row_index=row_index, status="SUCCESS")
                    log.info("row %d SUCCESS (attempt %d)", row_index, attempt)
                    print(f"       [OK] SUCCESS")
                    success_count += 1
                    break

                except Exception as exc:
                    last_err = str(exc)
                    log.warning("row %d attempt %d FAIL: %s",
                                row_index, attempt, last_err[:300])
                    try:
                        filler.take_screenshot(
                            f"row_{row_index}_attempt{attempt}_error")
                    except Exception:
                        pass

                    if _session_expired(page):
                        _handle_expiry(page, log, row_index)
                        continue   # retry without counting this as an attempt

                    if attempt < config.get("retry_count", 3):
                        time.sleep(RETRY_DELAY)

            if not succeeded:
                fail_count += 1
                writer.write_result(
                    row_index=row_index,
                    status="FAILED",
                    error_message=last_err[:2000],
                )
                log.error("row %d FAILED after all retries", row_index)
                print(f"       [FAIL] FAILED  (see logs/automation.log)")

        browser.close()

    elapsed = time.time() - t0
    writer.write_summary(total, success_count, fail_count, elapsed)

    rate = f"{success_count / total * 100:.1f}%" if total else "0%"
    print("\n" + "=" * 60)
    print(f"  DONE  {elapsed:.0f}s")
    print(f"  Total   : {total}")
    print(f"  Success : {success_count}")
    print(f"  Failed  : {fail_count}")
    print(f"  Rate    : {rate}")
    print(f"  Output  : {config['output_file']}")
    print("=" * 60 + "\n")
    log.info("DONE %.0fs total=%d success=%d failed=%d rate=%s",
             elapsed, total, success_count, fail_count, rate)


def main() -> None:
    args   = parse_args()
    config = load_config(args.config)
    filter_rows = (
        [int(r.strip()) for r in args.rows.split(",")]
        if args.rows else None
    )
    run(config, filter_rows)


if __name__ == "__main__":
    main()
