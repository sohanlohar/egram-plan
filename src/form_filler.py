"""
form_filler.py — eGramSwaraj Activity Planning Bot (v5)

Only fields that physically exist in the HTML form are handled.
Removed from previous versions:
  ✗ major_ids / major_names  (not a form field)
  ✗ minor_ids / minor_names  (not a form field)
  ✗ flagship_ids / flagship_names  (hidden div, only for costless activities)
  ✗ asset_ids/names/sub_ids/sub_names  (hidden div, conditional)
  ✗ output_ids / output_names  (hidden div, set server-side)

AJAX dependency chain (must fill in this exact order):
  #themeId
    → AJAX → #themeActivityNameID  (Select2)
      → AJAX → #focusAreaId
        → AJAX → #activityTypeListId
                  PDI checkboxes (#maDivId > #checkboxes)
  #activityFor
  targeted populace (#checkboxess)
  #workTypId
    → DWR AJAX → #subMajorHeadDivId / #subMinorHeadDivId  (conditional)
  radios → text fields → start year/month → beneficiaries → total cost
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger("egram_bot")

SELECTOR_TIMEOUT = 15_000   # ms
OPTIONS_POLL_MS  = 100      # ms — poll interval for AJAX dropdown readiness
OPTIONS_TIMEOUT  = 15_000   # ms — max wait for child dropdown to populate
NETWORK_IDLE_MS  = 8_000    # ms — max wait for networkidle after AJAX trigger
RETRY_DELAY      = 2        # seconds between row-level retries


# ══════════════════════════════════════════════════════════════════════════════
# Primitives
# ══════════════════════════════════════════════════════════════════════════════

def _v(record: dict, key: str) -> str:
    """Return stripped string value from record, empty string if missing/None."""
    return str(record.get(key, "") or "").strip()


def _wait_idle(page: Page) -> None:
    """Wait for in-flight AJAX to finish. Short-circuits if page stays busy."""
    try:
        page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_MS)
    except PlaywrightTimeout:
        pass


def safe_fill(page: Page, selector: str, value: str) -> None:
    """Clear a text/textarea field and type a value. No triple-click needed."""
    loc = page.locator(selector)
    loc.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
    loc.clear()
    loc.fill(value)


def wait_for_options(page: Page, selector: str) -> list[dict]:
    """
    Poll <select> until real options appear (not just the blank placeholder).
    Returns list of {value, text} dicts.
    Raises TimeoutError with current option list if never populated.
    """
    deadline = time.time() + OPTIONS_TIMEOUT / 1000
    while True:
        opts = page.eval_on_selector(
            selector,
            "el => Array.from(el.options)"
            "       .filter(o => o.value !== '')"
            "       .map(o => ({value: o.value, text: o.textContent.trim()}))"
        )
        if opts:
            return opts
        if time.time() > deadline:
            all_opts = page.eval_on_selector(
                selector,
                "el => Array.from(el.options).map(o => o.textContent.trim())"
            )
            raise TimeoutError(
                f"{selector} never populated (timeout {OPTIONS_TIMEOUT}ms). "
                f"Options present: {all_opts}"
            )
        page.wait_for_timeout(OPTIONS_POLL_MS)


def select_by_text(page: Page, selector: str, text: str) -> None:
    """
    Select a <select> option by visible text (exact match first, then substring).
    Waits for real options to appear before trying.
    """
    opts = wait_for_options(page, selector)
    for opt in opts:
        if opt["text"] == text or text in opt["text"]:
            page.locator(selector).select_option(value=opt["value"])
            return
    available = [o["text"] for o in opts]
    raise ValueError(
        f"'{text}' not found in {selector}. "
        f"Available ({len(available)}): {available}"
    )


def fill_select2(page: Page, hidden_select_id: str, search_text: str) -> None:
    """
    Drive a Select2 widget through its rendered UI.
    The hidden <select> must NOT be manipulated directly — Select2 intercepts
    all events and its onchange chain (which populates child dropdowns) only
    fires when interaction goes through the Select2 span elements.
    """
    trigger = page.locator(
        f"#{hidden_select_id} + .select2-container .select2-selection"
    )
    trigger.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
    trigger.click()

    search_box = page.locator(".select2-search__field")
    search_box.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
    search_box.fill(search_text)

    # Wait for results list to appear in DOM (not a blind sleep)
    page.locator(".select2-results__option").first.wait_for(
        state="visible", timeout=SELECTOR_TIMEOUT
    )

    # Prefer highlighted result; fall back to first result
    highlighted = page.locator(".select2-results__option--highlighted").first
    if highlighted.count():
        highlighted.click()
    else:
        page.locator(".select2-results__option").first.click()

    _wait_idle(page)   # let onchange AJAX (focus_area load) settle


def open_checkbox_panel(page: Page, container_selector: str, panel_id: str) -> None:
    """
    Open a custom multiselect panel by clicking its .selectBox trigger,
    scoped to the specific container to avoid hitting the wrong panel.
    """
    page.locator(f"{container_selector} .selectBox").click()
    page.locator(f"#{panel_id}").wait_for(state="visible", timeout=SELECTOR_TIMEOUT)


def tick_checkboxes(page: Page, panel_id: str, values_csv: str) -> None:
    """
    Tick checkboxes whose label text matches any value in the comma-separated string.
    Case-insensitive. Skips already-checked boxes.
    """
    if not values_csv.strip():
        return
    targets = {v.strip().lower() for v in values_csv.split(",")}
    for label in page.locator(f"#{panel_id} label").all():
        if (label.inner_text() or "").strip().lower() in targets:
            cb = label.locator("input[type='checkbox']")
            if cb.count() and not cb.is_checked():
                cb.click()


# ══════════════════════════════════════════════════════════════════════════════
# Debug snapshot (only on field failure)
# ══════════════════════════════════════════════════════════════════════════════

def _snapshot(page: Page, field: str, row: int, shots_dir: Path, err: str) -> None:
    stem = f"row{row}_{field}_error"
    try:
        page.screenshot(path=str(shots_dir / f"{stem}.png"), full_page=False)
    except Exception:
        pass
    try:
        (shots_dir / f"{stem}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    logger.error("FAIL | row=%d field=%s | %s", row, field, err[:250])


# ══════════════════════════════════════════════════════════════════════════════
# Pre-submit validation
# ══════════════════════════════════════════════════════════════════════════════

def _check_page_errors(page: Page) -> list[str]:
    issues = []
    for el in page.locator("span.text-danger:visible, .alert.text-danger:visible").all():
        txt = (el.inner_text() or "").strip()
        cls = el.get_attribute("class") or ""
        if txt and "customhide" not in cls:
            issues.append(txt)
    return issues


# ══════════════════════════════════════════════════════════════════════════════
# FormFiller
# ══════════════════════════════════════════════════════════════════════════════

class FormFiller:

    def __init__(self, page: Page, config: dict,
                 screenshots_dir: str = "screenshots") -> None:
        self.page      = page
        self.config    = config
        self.shots_dir = Path(screenshots_dir)
        self.shots_dir.mkdir(parents=True, exist_ok=True)

    # ── Public entry point ────────────────────────────────────────────────────

    def fill_form(self, record: dict, row_index: int) -> dict[str, str]:
        """
        Fill one row from the Excel data into the open addactivity.htm form.
        Follows the AJAX dependency chain precisely.
        Returns {"application_number": str, "transaction_id": str}.
        Raises RuntimeError listing every failed field if any step fails.
        """
        errors: list[str] = []
        p = self.page

        def attempt(field: str, fn) -> None:
            try:
                fn()
            except Exception as exc:
                msg = str(exc)
                errors.append(f"[{field}] {msg}")
                logger.warning("row=%d field=%s %s", row_index, field, msg[:200])
                _snapshot(p, field, row_index, self.shots_dir, msg)

        # ── 1. Theme ──────────────────────────────────────────────────────────
        # onchange → getActivityNameListWithoutComponent() → populates Select2
        attempt("theme", lambda: select_by_text(p, "#themeId", _v(record, "theme")))
        _wait_idle(p)

        # ── 2. Activity Name (Select2) ────────────────────────────────────────
        # onchange → getActivityNameIdDetails() → populates #focusAreaId
        attempt("activity_name",
                lambda: fill_select2(p, "themeActivityNameID", _v(record, "activity_name")))

        # ── 3. Focus Area ─────────────────────────────────────────────────────
        # onchange → getMissionAntyodayaMas() → populates PDI checkboxes
        #          → (implied) populates #activityTypeListId
        attempt("focus_area",
                lambda: select_by_text(p, "#focusAreaId", _v(record, "focus_area")))
        _wait_idle(p)

        # ── 4. Activity Type ──────────────────────────────────────────────────
        attempt("activity_type",
                lambda: select_by_text(p, "#activityTypeListId", _v(record, "activity_type")))

        # ── 5. Activity Description ───────────────────────────────────────────
        attempt("activity_description",
                lambda: safe_fill(p, "#activityDescId", _v(record, "activity_description")))

        # ── 6. PDI / Panchayat Advancement Index Indicator ───────────────────
        # Panel lives inside div#maDivId; trigger scoped to avoid wrong panel.
        pdi = _v(record, "pdi_indicator")
        def _pdi():
            open_checkbox_panel(p, "#maDivId", "checkboxes")
            tick_checkboxes(p, "checkboxes", pdi)
            p.locator("#activityDescId").click()   # dismiss panel
        attempt("pdi_indicator", _pdi)

        # ── 7. Activity For ───────────────────────────────────────────────────
        # Static options: All | GEN | SC | ST
        attempt("activity_for",
                lambda: select_by_text(p, "#activityFor", _v(record, "activity_for")))

        # ── 8. Targeted Populace ──────────────────────────────────────────────
        # Second multiselect; inner panel id="checkboxess" (double-s).
        targeted = _v(record, "targeted_populace")
        def _targeted():
            open_checkbox_panel(p, ".multiselect:has(#checkboxess)", "checkboxess")
            tick_checkboxes(p, "checkboxess", targeted)
            p.locator("#activityDescId").click()
        attempt("targeted_populace", _targeted)

        # ── 9. Activity Nature ────────────────────────────────────────────────
        # onchange → showHideDivForWorkType() → getMajorHeadList() via DWR
        # HTML options: New/Fresh | Operational | Maintenance | Upgradation
        attempt("activity_nature",
                lambda: select_by_text(p, "#workTypId", _v(record, "activity_nature")))
        _wait_idle(p)   # wait for any dynamic updates after activity nature selection

        # ── 10. Is directly funded by Panchayat? (radio) ─────────────────────
        # HTML: activityForCostlessFlagNoId  value="0" → Yes (funded)
        #        activityForCostlessFlagYesId value="1" → No  (not funded / costless)
        funded = _v(record, "is_directly_funded_by_panchayat").lower()
        def _funded():
            if funded in ("yes", "1", "true"):
                p.locator("#activityForCostlessFlagNoId").click()
            else:
                p.locator("#activityForCostlessFlagYesId").click()
        attempt("is_directly_funded_by_panchayat", _funded)

        # ── 11. Estimated completion time ─────────────────────────────────────
        attempt("estimated_completion_year",
                lambda: safe_fill(p, "#totDurYearId", _v(record, "estimated_completion_year")))
        attempt("estimated_completion_month",
                lambda: safe_fill(p, "#totDurMonId",  _v(record, "estimated_completion_month")))
        attempt("estimated_completion_days",
                lambda: safe_fill(p, "#totDurDayId",  _v(record, "estimated_completion_days")))

        # ── 12. Start year / month ────────────────────────────────────────────
        # onchange on startYearId → populateStartMonthList() → populates months
        attempt("start_year",
                lambda: select_by_text(p, "#startYearId",  _v(record, "start_year")))
        attempt("start_month",
                lambda: select_by_text(p, "#startMonthId", _v(record, "start_month")))

        # ── 13. Expected beneficiaries ────────────────────────────────────────
        attempt("expected_beneficiary_general",
                lambda: safe_fill(p, "#expctdMenGenId", _v(record, "expected_beneficiary_general")))
        attempt("expected_beneficiary_sc",
                lambda: safe_fill(p, "#expctdMenScId",  _v(record, "expected_beneficiary_sc")))
        attempt("expected_beneficiary_st",
                lambda: safe_fill(p, "#expctdMenStId",  _v(record, "expected_beneficiary_st")))

        # ── 14. Estimated total cost ──────────────────────────────────────────
        attempt("estimated_total_cost",
                lambda: safe_fill(p, "#totalCostId", _v(record, "estimated_total_cost")))

        # ── 15. Form submission ───────────────────────────────────────────────
        app_number = ""
        txn_id     = ""

        if self.config.get("submit_form", False):
            page_errors = _check_page_errors(p)
            if page_errors:
                errors.append(f"[validation] {'; '.join(page_errors)}")
            else:
                try:
                    # Choose button based on config: "save" (default) or "save_and_forward"
                    submit_action = self.config.get("submit_action", "save").lower()
                    if submit_action == "save_and_forward":
                        p.locator("#saveAndForwardId").click()
                    else:
                        p.locator("#saveAsDraftId").click()
                    _wait_idle(p)
                    app_number = self._text(p, "[id*='appNo'],[id*='applicationNo'],.app-number")
                    txn_id     = self._text(p, "[id*='txn'],[id*='transaction'],.txn-id")
                except Exception as exc:
                    errors.append(f"[submit] {exc}")

        if errors:
            raise RuntimeError(" | ".join(errors))

        return {"application_number": app_number, "transaction_id": txn_id}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _text(self, page: Page, selector: str) -> str:
        try:
            el = page.query_selector(selector)
            return el.inner_text().strip() if el else ""
        except Exception:
            return ""

    def take_screenshot(self, label: str) -> str:
        path = self.shots_dir / f"{label}.png"
        self.page.screenshot(path=str(path), full_page=False)
        return str(path)
