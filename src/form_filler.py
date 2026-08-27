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
import html
import re
import random
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from activity_output_handlers import (
    ActivityOutputError,
    ActivityOutputHandlerRegistry,
)
from activity_output_registry import normalize_output_type
from excel_reader import normalize_final_action

logger = logging.getLogger("egram_bot")

SELECTOR_TIMEOUT = 15_000   # ms
OPTIONS_POLL_MS  = 100      # ms — poll interval for AJAX dropdown readiness
OPTIONS_TIMEOUT  = 15_000   # ms — max wait for child dropdown to populate
NETWORK_IDLE_MS  = 8_000    # ms — max wait for networkidle after AJAX trigger


# ══════════════════════════════════════════════════════════════════════════════
# Primitives
# ══════════════════════════════════════════════════════════════════════════════

def _v(record: dict, key: str) -> str:
    """Return stripped string value from record, empty string if missing/None."""
    return str(record.get(key, "") or "").strip()


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _normalize_lookup_text(text: str) -> str:
    value = html.unescape(str(text or "")).replace("\xa0", " ").strip().lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _dropdown_match_key(text: str) -> str:
    """Normalize dropdown text, including whitespace around parentheses."""
    normalized = html.unescape(str(text or "")).replace("\xa0", " ").strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return re.sub(r"\s*([()])\s*", r"\1", normalized)


def _dropdown_match_key_without_whitespace(text: str) -> str:
    return re.sub(r"\s+", "", _dropdown_match_key(text))


def _parse_month_number(value: str) -> int | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None

    if raw.isdigit():
        month_num = int(raw)
        if 1 <= month_num <= 12:
            return month_num
        return None

    month_aliases = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    return month_aliases.get(raw)


def select_start_month(page: Page, selector: str, requested_value: str) -> None:
    requested_text = str(requested_value or "").strip()
    if not requested_text:
        return

    logger.debug("start_month: requested raw='%s' normalized='%s' selector=%s", requested_text, _normalize_text(requested_text), selector)
    logger.debug(
        "start_month: selector state present=%s visible=%s enabled=%s",
        page.locator(selector).count() > 0,
        page.locator(selector).is_visible() if page.locator(selector).count() else False,
        page.locator(selector).is_enabled() if page.locator(selector).count() else False,
    )

    options = wait_for_options(page, selector)
    logger.debug(
        "start_month: options before select=%s",
        [f"{opt.get('value', '')}:{opt.get('text', '')}" for opt in options],
    )
    requested_normalized = _dropdown_match_key(requested_text)
    requested_month_num = _parse_month_number(requested_text)
    matched_option: dict | None = None

    for opt in options:
        option_text = str(opt.get("text", "") or "").strip()
        option_value = str(opt.get("value", "") or "").strip()
        option_month_num = _parse_month_number(option_text) or _parse_month_number(option_value)

        if requested_month_num is not None and option_month_num == requested_month_num:
            matched_option = {"value": option_value, "text": option_text}
            break

        normalized_option_text = _dropdown_match_key(option_text)
        if normalized_option_text == requested_normalized or requested_normalized in normalized_option_text:
            matched_option = {"value": option_value, "text": option_text}
            break

    if not matched_option:
        available = [f"{o['value']}:{o['text']}" for o in options]
        raise ValueError(
            f"'{requested_text}' not found in {selector}. "
            f"Available ({len(available)}): {available}"
        )

    logger.debug(
        "start_month: matched option text='%s' value='%s'",
        matched_option["text"],
        matched_option["value"],
    )

    locator = page.locator(selector)
    locator.select_option(value=matched_option["value"])
    logger.debug(
        "start_month: value immediately after select_option='%s'",
        locator.input_value(),
    )

    page.eval_on_selector(
        selector,
        """
        el => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
            if (typeof el.onchange === 'function') {
                try { el.onchange(); } catch (error) { console.error(error); }
            }
        }
        """,
    )
    logger.debug(
        "start_month: value after events='%s' selected_option='%s'",
        locator.input_value(),
        page.eval_on_selector(selector, "el => (el.selectedOptions[0] ? `${el.selectedOptions[0].value}:${el.selectedOptions[0].textContent.trim()}` : '')"),
    )

    try:
        page.wait_for_function(
            """
            ({ selector, expectedValue }) => {
                const el = document.querySelector(selector);
                return !!el && el.value === expectedValue;
            }
            """,
            arg={"selector": selector, "expectedValue": matched_option["value"]},
            timeout=SELECTOR_TIMEOUT,
        )
    except PlaywrightTimeout as exc:
        current_state = page.eval_on_selector(
            selector,
            "el => ({ value: el.value, selected: el.selectedOptions[0] ? `${el.selectedOptions[0].value}:${el.selectedOptions[0].textContent.trim()}` : '', options: Array.from(el.options).map(o => `${o.value}:${o.textContent.trim()}`) })",
        )
        raise ValueError(
            f"Start Month did not stick after selection. requested='{requested_text}' matched='{matched_option['text']}' value='{matched_option['value']}' state={current_state}"
        ) from exc

    logger.debug(
        "start_month: final confirmed value='%s' selected_option='%s'",
        locator.input_value(),
        page.eval_on_selector(selector, "el => (el.selectedOptions[0] ? `${el.selectedOptions[0].value}:${el.selectedOptions[0].textContent.trim()}` : '')"),
    )


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


def fill_vprp_plan(page: Page, value: str) -> None:
    """Select the Yes/No VPRP Plan radio button exposed by the portal form."""
    requested = _normalize_text(value)
    if not requested:
        return
    if requested not in {"yes", "no"}:
        raise ValueError(f"VPRP Plan must be Yes or No, got: {value!r}")

    section_selector = "#vprpPlandiv"
    radio_selector = f"{section_selector} input[type='radio'][name='vprpplan']"
    try:
        page.wait_for_function(
            """
            ({ sectionSelector, radioSelector }) => {
                const section = document.querySelector(sectionSelector);
                return !!section && getComputedStyle(section).display !== 'none'
                    && document.querySelectorAll(radioSelector).length > 0;
            }
            """,
            arg={"sectionSelector": section_selector, "radioSelector": radio_selector},
            timeout=SELECTOR_TIMEOUT,
        )
    except PlaywrightTimeout as exc:
        diagnostics = page.evaluate(
            """
            ({ sectionSelector, radioSelector }) => {
                const section = document.querySelector(sectionSelector);
                return {
                    sectionFound: !!section,
                    sectionDisplay: section ? getComputedStyle(section).display : null,
                    matchingRadios: Array.from(document.querySelectorAll(radioSelector)).map(radio => ({
                        id: radio.id,
                        name: radio.name,
                        value: radio.value,
                        checked: radio.checked,
                    })),
                };
            }
            """,
            {"sectionSelector": section_selector, "radioSelector": radio_selector},
        )
        logger.error(
            "vprp_plan: section/control wait failed original=%r section=%s radio_locator=%s diagnostics=%s",
            value, section_selector, radio_selector, diagnostics,
        )
        raise ValueError("VPRP Plan radio section or controls were not found") from exc

    diagnostics = page.locator(radio_selector).evaluate_all(
        """
        radios => radios.map(radio => ({
            id: radio.id,
            name: radio.name,
            value: radio.value,
            checked: radio.checked,
            text: radio.parentElement ? radio.parentElement.textContent.trim() : '',
        }))
        """
    )
    logger.debug(
        "vprp_plan: section_found=True original=%r normalized=%r radio_locator=%s options=%s",
        value, requested, radio_selector, diagnostics,
    )

    expected_value = {"yes": "t", "no": "f"}[requested]
    matched_index = next(
        (
            index for index, option in enumerate(diagnostics)
            if str(option["value"]).strip().lower() == expected_value
            or _normalize_text(option["text"]).find(requested) >= 0
        ),
        None,
    )
    if matched_index is None:
        logger.error(
            "vprp_plan: requested option not found original=%r normalized=%r locator=%s options=%s",
            value, requested, radio_selector, diagnostics,
        )
        raise ValueError(f"VPRP Plan {value!r} radio option was not found")

    control = page.locator(radio_selector).nth(matched_index)
    logger.info(
        "vprp_plan: selecting original=%r normalized=%r locator=%s matched=%s",
        value, requested, radio_selector, diagnostics[matched_index],
    )
    control.click(force=True)
    if not control.is_checked():
        raise ValueError(f"VPRP Plan {value!r} radio button was not selected")


def _dispatch_input_events(page: Page, selector: str) -> None:
    page.eval_on_selector(
        selector,
        """
        el => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: '0' }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """,
    )


def _to_number_or_none(value: str) -> float | None:
    raw = str(value or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


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
    Select one or more <select> options by visible text.
    Waits for real options to appear before trying.
    """
    opts = wait_for_options(page, selector)
    original = str(text or "")
    requested = original.strip()
    requested_values = [part.strip() for part in requested.split("|") if part.strip()]
    if len(requested_values) > 1 and not page.locator(selector).get_attribute("multiple"):
        raise ValueError(
            f"Multiple values supplied for single-select {selector}: {requested_values}"
        )

    normalized_requested = _dropdown_match_key(requested)
    compact_requested = _dropdown_match_key_without_whitespace(requested)

    if len(requested_values) > 1:
        selected_options: list[dict] = []
        unmatched_values: list[str] = []
        for requested_value in requested_values:
            normalized_value = _dropdown_match_key(requested_value)
            compact_value = _dropdown_match_key_without_whitespace(requested_value)
            matched = next(
                (
                    opt for opt in opts
                    if str(opt["text"] or "").strip() == requested_value
                    or _dropdown_match_key(str(opt["text"] or "").strip()) == normalized_value
                    or _dropdown_match_key_without_whitespace(str(opt["text"] or "").strip()) == compact_value
                ),
                None,
            )
            if matched:
                selected_options.append(matched)
            else:
                unmatched_values.append(requested_value)

        if unmatched_values:
            raise ValueError(
                f"Values {unmatched_values} not found in {selector}. "
                f"Available: {[o['text'] for o in opts]}"
            )

        logger.info(
            "dropdown match selector=%s original=%r normalized=%r matched=%r mode=multi_normalized",
            selector, original, [_dropdown_match_key(v) for v in requested_values],
            [opt["text"] for opt in selected_options],
        )
        page.locator(selector).select_option(
            value=[opt["value"] for opt in selected_options]
        )
        return

    for opt in opts:
        option_text = str(opt["text"] or "").strip()
        if option_text == requested:
            logger.debug(
                "dropdown match selector=%s original=%r normalized=%r matched=%r mode=exact",
                selector, original, normalized_requested, option_text,
            )
            page.locator(selector).select_option(value=opt["value"])
            return

    for opt in opts:
        option_text = str(opt["text"] or "").strip()
        if _dropdown_match_key(option_text) == normalized_requested:
            logger.info(
                "dropdown match selector=%s original=%r normalized=%r matched=%r mode=normalized",
                selector, original, normalized_requested, option_text,
            )
            page.locator(selector).select_option(value=opt["value"])
            return

    for opt in opts:
        option_text = str(opt["text"] or "").strip()
        if _dropdown_match_key_without_whitespace(option_text) == compact_requested:
            logger.info(
                "dropdown match selector=%s original=%r normalized=%r matched=%r mode=normalized_no_whitespace",
                selector, original, normalized_requested, option_text,
            )
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
    requested_norm = _dropdown_match_key(search_text)
    trigger = page.locator(
        f"#{hidden_select_id} + .select2-container .select2-selection"
    )

    for attempt in range(1, 2):
        trigger.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
        trigger.click()

        search_box = page.locator(".select2-search__field")
        search_box.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
        search_box.fill(search_text)

        results = page.locator(
            ".select2-results__option:not(.select2-results__option--loading):not([aria-disabled='true'])"
        )
        results.first.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)

        chosen_index = -1
        chosen_text = ""
        result_count = results.count()

        # Pass 1: exact normalized match.
        for idx in range(result_count):
            candidate = (results.nth(idx).inner_text() or "").strip()
            if _dropdown_match_key(candidate) == requested_norm:
                chosen_index = idx
                chosen_text = candidate
                break

        # Pass 2: substring match.
        if chosen_index < 0:
            for idx in range(result_count):
                candidate = (results.nth(idx).inner_text() or "").strip()
                candidate_norm = _dropdown_match_key(candidate)
                if requested_norm in candidate_norm or candidate_norm in requested_norm:
                    chosen_index = idx
                    chosen_text = candidate
                    break

        # Pass 3: highlighted fallback.
        if chosen_index < 0:
            highlighted = page.locator(".select2-results__option--highlighted").first
            if highlighted.count():
                chosen_text = (highlighted.inner_text() or "").strip()
                highlighted.click()
            else:
                chosen_text = (results.first.inner_text() or "").strip()
                results.first.click()
        else:
            results.nth(chosen_index).click()

        _wait_idle(page)

        selected_text = page.eval_on_selector(
            f"#{hidden_select_id} + .select2-container .select2-selection__rendered",
            "el => (el.textContent || '').trim()",
        )
        selected_norm = _dropdown_match_key(selected_text)
        expected_seen = requested_norm == selected_norm or requested_norm in selected_norm or selected_norm in requested_norm
        selected_value = page.eval_on_selector(f"#{hidden_select_id}", "el => String(el.value || '').trim()")

        if expected_seen and selected_value:
            return

        logger.warning(
            "select2 retry %d/3 id=%s requested='%s' clicked='%s' selected='%s' value='%s'",
            attempt,
            hidden_select_id,
            search_text,
            chosen_text,
            selected_text,
            selected_value,
        )

    raise RuntimeError(
        f"Select2 selection did not persist for #{hidden_select_id}: requested='{search_text}'"
    )


def open_checkbox_panel(page: Page, container_selector: str, panel_id: str) -> None:
    """
    Open a custom multiselect panel by clicking its .selectBox trigger,
    scoped to the specific container to avoid hitting the wrong panel.
    Falls back to JavaScript if click doesn't work immediately.
    """
    selectbox_sel = f"{container_selector} .selectBox"
    panel_sel = f"#{panel_id}"
    
    # Try clicking the selectBox to trigger onclick
    try:
        page.locator(selectbox_sel).click()
    except Exception:
        pass
    page.wait_for_timeout(200)  # Give JavaScript time to execute
    
    # Check if panel is now visible
    if not page.locator(panel_sel).is_visible():
        # If click didn't work, try using JavaScript to call the show function
        # Map panel_id to the JS function name (e.g., "checkboxes" → "showCheckboxes", "checkboxess" → "showCheckboxess")
        show_func = f"show{panel_id[0].upper() + panel_id[1:]}"
        try:
            page.evaluate(f"window.{show_func}?.()")
            page.wait_for_timeout(200)
        except Exception:
            pass  # Function might not exist, that's okay
        
        # Finally, try making it visible via CSS as fallback
        try:
            page.evaluate(f"document.getElementById('{panel_id}').style.display = 'block'")
        except Exception:
            pass
    
    # Now wait for it to be visible
    page.locator(panel_sel).wait_for(state="visible", timeout=SELECTOR_TIMEOUT)


def _normalize_multiselect_text(text: str) -> str:
    """Normalize text for multiselect matching: strip, lowercase, collapse whitespace."""
    return " ".join(text.strip().lower().split())


def _choose_random_pdi_labels(available_labels: list[str]) -> list[str]:
    """Choose a unique random PDI subset that follows the portal selection rules."""
    if not available_labels:
        return []

    if len(available_labels) >= 5:
        sample_size = random.choice([4, 5])
    else:
        sample_size = random.randint(1, len(available_labels))

    sample_size = min(sample_size, len(available_labels))
    return random.sample(available_labels, k=sample_size)


def select_multiselect_checkboxes(page: Page, panel_id: str, excel_values: str, 
                                   row: int, field_name: str) -> None:
    """
    Enhanced multiselect handler supporting | or , delimiters.
    
    Args:
        page: Playwright Page
        panel_id: Container ID for the checkbox panel (e.g., "checkboxes")
        excel_values: Pipe or comma-separated visible indicator labels
        row: Row index for logging
        field_name: Field name for logging/debugging (e.g., "pdi_indicator")
        
    Raises:
        RuntimeError: If requested indicator is not found or cannot be selected
    """
    if not excel_values or not excel_values.strip():
        logger.debug("row=%d %s: empty values, skipping", row, field_name)
        return
    
    # Parse values using | or , as delimiter
    requested_labels = [
        v.strip() for v in excel_values.split("|") if v.strip()
    ] if "|" in excel_values else [excel_values.strip()]
    requested_normalized = {_normalize_multiselect_text(label) for label in requested_labels}
    
    logger.debug("row=%d %s: requested indicators (%d): %s", 
                 row, field_name, len(requested_normalized), requested_labels)
    
    # Collect all available checkboxes
    labels = page.locator(f"#{panel_id} label").all()
    logger.debug("row=%d %s: found %d total labels in panel", row, field_name, len(labels))
    
    available_map = {}  # normalized_text -> {checkbox_elem, is_enabled, is_checked, visible_text}
    matched_normalized = set()
    
    for label in labels:
        visible_text = (label.inner_text() or "").strip()
        normalized_text = _normalize_multiselect_text(visible_text)
        
        # Get the checkbox element
        checkbox = label.locator("input[type='checkbox']")
        if not checkbox.count():
            logger.debug("row=%d %s: label has no checkbox: '%s'", row, field_name, visible_text)
            continue
        
        is_enabled = checkbox.is_enabled()
        is_checked = checkbox.is_checked()
        
        logger.debug("row=%d %s: checkbox '%s' enabled=%s checked=%s", 
                     row, field_name, visible_text, is_enabled, is_checked)
        
        available_map[normalized_text] = {
            "visible_text": visible_text,
            "checkbox": checkbox,
            "is_enabled": is_enabled,
            "is_checked": is_checked,
        }
        
        if normalized_text in requested_normalized:
            matched_normalized.add(normalized_text)
    
    # Verify all requested indicators were found
    unmatched = requested_normalized - matched_normalized
    if unmatched:
        available_labels = [v["visible_text"] for v in available_map.values()]
        logger.error("row=%d %s: requested indicators NOT found: %s | available: %s",
                     row, field_name, unmatched, available_labels)
        raise RuntimeError(
            f"Activity Indicators not found: {unmatched} | "
            f"Available: {available_labels}"
        )
    
    # Select all matched checkboxes that are enabled
    for normalized_text in matched_normalized:
        info = available_map[normalized_text]
        checkbox = info["checkbox"]
        visible_text = info["visible_text"]
        
        if not info["is_enabled"]:
            logger.warning("row=%d %s: checkbox is disabled, cannot select: '%s'",
                           row, field_name, visible_text)
            continue
        
        if info["is_checked"]:
            logger.debug("row=%d %s: checkbox already selected: '%s'",
                         row, field_name, visible_text)
            continue
        
        # Try normal click first
        try:
            logger.debug("row=%d %s: clicking checkbox: '%s'", row, field_name, visible_text)
            checkbox.click()
            
            # Verify it was selected
            if checkbox.is_checked():
                logger.debug("row=%d %s: checkbox selected successfully: '%s'",
                             row, field_name, visible_text)
            else:
                logger.warning("row=%d %s: checkbox click did not select it: '%s'",
                               row, field_name, visible_text)
        except Exception as e:
            logger.warning("row=%d %s: normal click failed on '%s': %s, trying JS fallback",
                           row, field_name, visible_text, str(e)[:100])
            try:
                # Fallback: use JavaScript to click
                checkbox.evaluate("el => el.click()")
                if checkbox.is_checked():
                    logger.debug("row=%d %s: JS fallback successful: '%s'",
                                 row, field_name, visible_text)
                else:
                    logger.warning("row=%d %s: JS fallback also failed to select: '%s'",
                                   row, field_name, visible_text)
            except Exception as js_err:
                logger.error("row=%d %s: both click methods failed for '%s': %s",
                             row, field_name, visible_text, str(js_err)[:100])


# ══════════════════════════════════════════════════════════════════════════════
# Debug snapshot (only on field failure)
# ══════════════════════════════════════════════════════════════════════════════

def _snapshot(page: Page, field: str, row: int, shots_dir: Path, err: str, fatal: bool = True) -> None:
    stem = f"row{row}_{field}_error"
    try:
        page.screenshot(path=str(shots_dir / f"{stem}.png"), full_page=False)
    except Exception:
        pass
    try:
        (shots_dir / f"{stem}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    if fatal:
        logger.error("FAIL | row=%d field=%s | %s", row, field, err[:250])
    else:
        logger.warning("NON_FATAL | row=%d field=%s | %s", row, field, err[:250])


def _capture_pdi_empty_state(page: Page, container, row: int, shots_dir: Path, reason: str) -> None:
    """Capture targeted diagnostics when no PDI options are detected."""
    stem = f"row{row}_pdi_indicator_no_options"

    screenshot_path = shots_dir / f"{stem}.png"
    html_path = shots_dir / f"{stem}.html"

    try:
        page.screenshot(path=str(screenshot_path), full_page=False)
    except Exception as exc:
        logger.warning("row=%d pdi: failed to capture no-options screenshot: %s", row, str(exc)[:120])

    container_html = ""
    element_inventory: list[dict] = []
    try:
        container_html = container.evaluate("el => el.outerHTML || ''")
        element_inventory = container.evaluate(
            """
            el => Array.from(el.querySelectorAll('*')).map(node => {
                const text = (node.textContent || '').replace(/\\s+/g, ' ').trim();
                return {
                    tag: (node.tagName || '').toLowerCase(),
                    id: node.id || '',
                    name: node.getAttribute('name') || '',
                    className: node.className || '',
                    type: node.getAttribute('type') || '',
                    value: node.getAttribute('value') || '',
                    visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                    text: text.slice(0, 120),
                };
            })
            """
        )
    except Exception as exc:
        logger.warning("row=%d pdi: failed to read container diagnostics: %s", row, str(exc)[:120])

    try:
        html_path.write_text(container_html, encoding="utf-8")
    except Exception as exc:
        logger.warning("row=%d pdi: failed to write container HTML snapshot: %s", row, str(exc)[:120])

    logger.error(
        "row=%d pdi: no options detected | reason=%s | screenshot=%s | html=%s",
        row,
        reason,
        screenshot_path,
        html_path,
    )
    logger.error("row=%d pdi: container element inventory count=%d", row, len(element_inventory))
    for idx, item in enumerate(element_inventory):
        logger.error("row=%d pdi: container element[%d]=%s", row, idx, item)


def _wait_for_pdi_options(page: Page, container, row: int):
    """Wait for PDI panel visibility and for checkbox options to stabilize."""
    checkbox_panel = container.locator("#checkboxes").first
    if not checkbox_panel.count():
        logger.warning("row=%d pdi: #checkboxes panel does not exist", row)
        return None, []

    if not checkbox_panel.is_visible():
        logger.debug("row=%d pdi: #checkboxes not visible, attempting to open", row)
        try:
            select_box = container.locator(".selectBox").first
            if select_box.count():
                select_box.click()
                page.wait_for_timeout(300)
        except Exception as exc:
            logger.warning("row=%d pdi: failed to click selectBox: %s", row, str(exc)[:100])

    try:
        checkbox_panel.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
    except PlaywrightTimeout:
        logger.warning("row=%d pdi: #checkboxes failed to become visible", row)
        return checkbox_panel, []

    selector = "input[type='checkbox'][name^='missionAntodayaDet']"
    deadline = time.time() + OPTIONS_TIMEOUT / 1000
    last_count = -1
    stable_checks = 0

    while True:
        current_count = checkbox_panel.locator(selector).count()
        if current_count != last_count:
            logger.debug(
                "row=%d pdi: option_count_probe count=%d stable_checks=%d",
                row,
                current_count,
                stable_checks,
            )

        if current_count > 0:
            if current_count == last_count:
                stable_checks += 1
            else:
                stable_checks = 0
            if stable_checks >= 2:
                break

        last_count = current_count
        if time.time() > deadline:
            logger.warning(
                "row=%d pdi: options did not stabilize before timeout (last_count=%d)",
                row,
                current_count,
            )
            break
        page.wait_for_timeout(OPTIONS_POLL_MS)

    return checkbox_panel, checkbox_panel.locator(selector).all()


# ══════════════════════════════════════════════════════════════════════════════
# Pre-submit validation
# ══════════════════════════════════════════════════════════════════════════════

def _check_page_errors(page: Page) -> list[dict]:
    """Collect all visible validation errors from the form."""
    errors = []
    
    # Check for alert elements with error text
    for el in page.locator(".alert:visible, span.text-danger:visible").all():
        txt = (el.inner_text() or "").strip()
        cls = el.get_attribute("class") or ""
        element_id = el.get_attribute("id") or ""
        if txt and "customhide" not in cls:
            errors.append({
                "id": element_id,
                "text": txt[:200],
                "type": "alert"
            })
    
    # Check for elements whose IDs end with "Error"
    error_ids = [
        "themeIdError", "activityNameIdError", "activityFocusError",
        "activityTypeError", "activityDescError", "activityDescLenghtError",
        "activityMAError", "activityForError", "activityActIsError",
        "submjrPrmptError", "minorPrmptError", "activitycostLessError",
        "activitydurationError", "activitydurationMonthLimitYearError",
        "activitydurationMonthLimitError", "activitydurationDaysLimitError",
        "activitystartYearError", "activitystartmonthError",
        "activityexpeBenefiError", "activityexpeBenefiLimitError",
        "errorindicativeUnitCostId", "costlessTotalCostError",
        "indicativeTotalCostWrongValueError", "activityeexpctdResultError",
        "flagshipSchemeError", "supportedMinistriesError", "activityTotalCostError",
        "activityTotalCostLimitExceedError", "activityoperationalError",
        "activitymainAstCateError", "activitymainsubCateError",
        "activitymaintotUnitError", "mainAstNumOfUntLimitError",
        "mainassetUnitCostError", "mainassetUnitCostLimitError",
        "activityoutputError"
    ]
    
    for error_id in error_ids:
        el = page.locator(f"#{error_id}:visible").first
        if el.count():
            txt = (el.inner_text() or "").strip()
            if txt:
                errors.append({
                    "id": error_id,
                    "text": txt[:200],
                    "type": "error_element"
                })
    
    return errors


# ══════════════════════════════════════════════════════════════════════════════
# Panchayat Advancement Index (Mission Antyodaya) Handler
# ══════════════════════════════════════════════════════════════════════════════

def select_panchayat_advancement_indicators(
    page: Page, 
    excel_values: str, 
    row: int,
    shots_dir: Path | None = None,
) -> None:
    """
    Portal-specific handler for Panchayat Advancement Index / Mission Antyodaya indicators.
    
    This MUST NOT reuse stale WebElements after Focus Area AJAX loading.
    Portal validation checks: element id="activityMAError"
    Portal portal handler: onclick="hideshowMaData(maCode)"
    """
    raw_excel_value = str(excel_values or "")
    logger.debug("row=%d pdi: raw excel pdi_indicator=%r", row, raw_excel_value)
    if not raw_excel_value.strip():
        logger.debug(
            "row=%d pdi: excel value is empty, continuing with portal-driven auto-selection",
            row,
        )
    
    # Step 1: Resolve visible container instance (portal can render duplicate IDs)
    try:
        page.locator("#maDivId").first.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
    except PlaywrightTimeout:
        logger.warning("row=%d pdi: #maDivId was not visible within timeout", row)

    containers = page.locator("#maDivId")
    container = None
    for idx in range(containers.count()):
        candidate = containers.nth(idx)
        if candidate.is_visible():
            container = candidate
            break

    if container is None:
        raise RuntimeError("PDI container #maDivId is not visible")
    
    # Step 2: Wait for portal loading to finish
    try:
        loading_indicator = page.locator("#watchId")
        if loading_indicator.count():
            # Wait for the loading indicator to be hidden
            try:
                page.wait_for_function(
                    "() => { const el = document.getElementById('watchId'); "
                    "if (!el) return true; "
                    "const style = window.getComputedStyle(el); "
                    "return style.display === 'none' || el.hidden || el.classList.contains('hidden'); }",
                    timeout=OPTIONS_TIMEOUT
                )
            except PlaywrightTimeout:
                logger.debug("row=%d pdi: watchId loading indicator did not finish", row)
    except Exception:
        pass
    
    # Step 3: Verify panel and wait for options to load/stabilize
    checkbox_panel, checkbox_inputs = _wait_for_pdi_options(page, container, row)
    if checkbox_panel is None:
        raise RuntimeError("PDI checkbox panel #checkboxes was not found")

    logger.debug("row=%d pdi: found %d checkbox inputs in panel", row, len(checkbox_inputs))
    
    if not checkbox_inputs:
        logger.warning("row=%d pdi: no checkbox inputs found after waiting and querying", row)
        if shots_dir is not None:
            _capture_pdi_empty_state(
                page,
                container,
                row,
                shots_dir,
                reason="No missionAntodayaDet checkboxes present after waits",
            )
        raise RuntimeError("No PDI indicator options are available on the form")
    
    # Step 6: Build map of available indicators from parent labels
    available_map = {}  # normalized_text -> {input_elem, label_text, checkbox_id, checkbox_value, is_enabled, is_checked}
    duplicate_labels: dict[str, int] = {}
    
    for input_elem in checkbox_inputs:
        checkbox_id = input_elem.get_attribute("id") or ""
        checkbox_name = input_elem.get_attribute("name") or ""
        checkbox_value = input_elem.get_attribute("value") or ""
        
        # Get parent label
        label_elem = input_elem.locator("xpath=ancestor::label[1]")
        if not label_elem.count():
            logger.debug("row=%d pdi: checkbox %s has no parent label", row, checkbox_id)
            continue
        
        label_text = (label_elem.inner_text() or "").strip()
        normalized_text = _normalize_multiselect_text(label_text)
        
        is_enabled = input_elem.is_enabled()
        is_checked = input_elem.is_checked()
        
        # Skip only empty labels; "None" can be a valid portal option.
        if label_text == "":
            logger.debug("row=%d pdi: skipping invalid checkbox: value=%s label='%s'", 
                        row, checkbox_value, label_text)
            continue
        
        logger.debug(
            "row=%d pdi: available checkbox id=%s value=%s label='%s' enabled=%s checked=%s",
            row, checkbox_id, checkbox_value, label_text, is_enabled, is_checked
        )
        
        if normalized_text in available_map:
            duplicate_labels[normalized_text] = duplicate_labels.get(normalized_text, 1) + 1
            logger.debug(
                "row=%d pdi: duplicate label instance ignored to preserve canonical checkbox: '%s' id=%s",
                row,
                label_text,
                checkbox_id,
            )
            continue

        available_map[normalized_text] = {
            "input": input_elem,
            "label_text": label_text,
            "id": checkbox_id,
            "name": checkbox_name,
            "value": checkbox_value,
            "is_enabled": is_enabled,
            "is_checked": is_checked,
        }

    parsed_labels = [part.strip() for part in raw_excel_value.split("|") if part.strip()]
    requested_labels = list(dict.fromkeys(parsed_labels))
    logger.debug(
        "row=%d pdi: parsed indicators from excel delimiter='|' parsed=%s deduped=%s",
        row,
        parsed_labels,
        requested_labels,
    )

    if requested_labels:
        if len(requested_labels) < 5:
            selected_labels = random.sample(requested_labels, k=len(requested_labels))
        else:
            sample_size = random.choice([4, 5])
            selected_labels = random.sample(requested_labels, k=sample_size)
        selection_source = "excel"
    else:
        selected_labels = _choose_random_pdi_labels([v["label_text"] for v in available_map.values()])
        selection_source = "auto"
        if not selected_labels:
            logger.warning("row=%d pdi: no valid indicators available for auto-selection", row)
            raise RuntimeError("No valid PDI indicator options are available on the form")

    requested_normalized = {
        _normalize_multiselect_text(label) for label in selected_labels
    }

    logger.debug(
        "row=%d pdi: random selection source=%s parsed_count=%d selected_count=%d selected_labels=%s",
        row,
        selection_source,
        len(requested_labels),
        len(selected_labels),
        selected_labels,
    )

    portal_options = [v["label_text"] for v in available_map.values()]
    logger.debug(
        "row=%d pdi: portal options detected count=%d options=%s",
        row,
        len(portal_options),
        portal_options,
    )
    
    # Step 7: Resolve selected labels one-by-one with detailed diagnostics
    matched_infos: list[dict] = []
    unmatched: list[str] = []
    for requested_label in selected_labels:
        normalized_text = _normalize_multiselect_text(requested_label)
        logger.debug(
            "row=%d pdi: searching requested indicator='%s' normalized='%s'",
            row,
            requested_label,
            normalized_text,
        )

        info = available_map.get(normalized_text)
        if not info:
            unmatched.append(requested_label)
            logger.warning(
                "row=%d pdi: match not found for requested indicator='%s' reason='normalized text not present in portal options'",
                row,
                requested_label,
            )
            continue

        logger.debug(
            "row=%d pdi: match found requested='%s' portal_label='%s' id=%s value=%s enabled=%s checked=%s",
            row,
            requested_label,
            info["label_text"],
            info["id"],
            info["value"],
            info["is_enabled"],
            info["is_checked"],
        )
        matched_infos.append(info)

    if unmatched:
        logger.warning(
            "row=%d pdi: unmatched selected indicators=%s",
            row,
            unmatched,
        )
    if not matched_infos:
        raise RuntimeError(
            f"PDI indicators from Excel were not found on the form: {unmatched}"
        )
    
    # Step 8: Select matched checkboxes and verify checked state per checkbox
    for info in matched_infos:
        input_elem = info["input"]
        label_text = info["label_text"]
        checkbox_id = info["id"]
        checkbox_value = info["value"]
        
        if not info["is_enabled"]:
            raise RuntimeError(f"PDI checkbox is disabled: {label_text}")
        
        if info["is_checked"]:
            logger.debug("row=%d pdi: checkbox already selected: %s | selection_success=True", row, label_text)
            continue
        
        # Step 8a: Click the checkbox
        try:
            logger.debug("row=%d pdi: clicking checkbox id=%s value=%s label='%s'",
                        row, checkbox_id, checkbox_value, label_text)
            input_elem.scroll_into_view_if_needed()
            input_elem.click()
            page.wait_for_timeout(100)
        except Exception as e:
            logger.warning("row=%d pdi: normal click failed: %s, trying label click fallback", row, str(e)[:80])
            try:
                input_elem.locator("xpath=ancestor::label[1]").click()
                page.wait_for_timeout(100)
            except Exception as label_err:
                logger.error("row=%d pdi: label click fallback also failed: %s", row, str(label_err)[:80])
                continue
        
        # Step 8b: Verify checkbox state immediately after click
        try:
            is_checked_after = input_elem.is_checked()
            dom_checked = input_elem.evaluate("el => el.checked")
            logger.debug(
                "row=%d pdi: after click - is_selected=%s dom_checked=%s for '%s'",
                row, is_checked_after, dom_checked, label_text
            )
            
            if not is_checked_after and not dom_checked:
                logger.error("row=%d pdi: selection_success=False label='%s'", row, label_text)
                raise RuntimeError(f"PDI checkbox not selected after click: {label_text}")
            logger.debug("row=%d pdi: selection_success=True label='%s'", row, label_text)
        except Exception as e:
            raise RuntimeError(str(e))
    
    # Step 9: Final verification - count total selected
    try:
        selected_count = checkbox_panel.locator(
            "input[type='checkbox'][name^='missionAntodayaDet']:checked"
        ).count()
        logger.debug("row=%d pdi: total selected indicators: %d", row, selected_count)
        if selected_count == 0:
            raise RuntimeError("NO PDI indicators are selected after processing")
    except Exception as e:
        raise RuntimeError(f"PDI final selection verification failed: {e}") from e
    
    logger.info("row=%d pdi: selection complete", row)

    # Step 10: Diagnostics - compare selected states and portal-style missionAdStatus
    if duplicate_labels:
        logger.debug("row=%d pdi: duplicate label counts=%s", row, duplicate_labels)

    diagnostic = page.evaluate(
        """
        () => {
            const inputs = Array.from(document.querySelectorAll('#checkboxes input[type="checkbox"][id^="missionAdId"]'));
            const byId = {};
            for (const el of inputs) {
                byId[el.id] = byId[el.id] || [];
                byId[el.id].push({
                    checked: !!el.checked,
                    value: el.value || '',
                    name: el.name || '',
                    disabled: !!el.disabled,
                });
            }

            let missionAdStatus = false;
            inputs.forEach((_, index) => {
                const probe = document.querySelector('#missionAdId' + parseInt(index, 10));
                if (probe && probe.checked) {
                    missionAdStatus = true;
                }
            });

            return {
                missionAdStatus,
                byId,
                visibleError: !!document.querySelector('#activityMAError:not(.customhide)'),
            };
        }
        """
    )
    logger.debug("row=%d pdi: portal_diagnostic=%s", row, diagnostic)


# ══════════════════════════════════════════════════════════════════════════════
# Supported Department Handler
# ══════════════════════════════════════════════════════════════════════════════


def _option_signature(page: Page, selector: str) -> str:
    return page.eval_on_selector(
        selector,
        """
        el => Array.from(el.options)
            .map(o => `${o.value}:${(o.textContent || '').trim()}`)
            .join('|')
        """,
    )


def wait_for_options_refresh(page: Page, selector: str, previous_signature: str) -> list[dict]:
    deadline = time.time() + OPTIONS_TIMEOUT / 1000
    last_signature = previous_signature
    while True:
        signature = _option_signature(page, selector)
        if signature and signature != last_signature:
            opts = page.eval_on_selector(
                selector,
                "el => Array.from(el.options)"
                "       .filter(o => o.value !== '')"
                "       .map(o => ({value: o.value, text: o.textContent.trim()}))"
            )
            if opts:
                return opts
            last_signature = signature
        if time.time() > deadline:
            all_opts = page.eval_on_selector(
                selector,
                "el => Array.from(el.options).map(o => o.textContent.trim())"
            )
            raise TimeoutError(
                f"{selector} did not refresh (timeout {OPTIONS_TIMEOUT}ms). "
                f"Options present: {all_opts}"
            )
        page.wait_for_timeout(OPTIONS_POLL_MS)


def wait_for_options_stable(page: Page, selector: str, stable_checks: int = 2) -> list[dict]:
    deadline = time.time() + OPTIONS_TIMEOUT / 1000
    last_signature = ""
    stable_count = 0
    while True:
        signature = _option_signature(page, selector)
        if signature and signature == last_signature:
            stable_count += 1
            if stable_count >= stable_checks:
                return page.eval_on_selector(
                    selector,
                    "el => Array.from(el.options)"
                    "       .filter(o => o.value !== '')"
                    "       .map(o => ({value: o.value, text: o.textContent.trim()}))"
                )
        else:
            stable_count = 0
            last_signature = signature
        if time.time() > deadline:
            raise TimeoutError(f"{selector} options did not become stable (timeout {OPTIONS_TIMEOUT}ms)")
        page.wait_for_timeout(OPTIONS_POLL_MS)

def select_supported_department(
    page: Page,
    department_text: str,
    row: int
) -> None:
    """
    Select Supported Department with explicit wait for options to refresh after Flagship Scheme.
    Portal function: getSupportedMinistryListByFlagshipScheme(this.value)
    """
    if not department_text or not department_text.strip():
        logger.debug("row=%d: supported_department empty, skipping", row)
        return
    
    # Step 1: Check container visibility
    container_div = page.locator("#supportedMinistriesDivId")
    if not container_div.count() or not container_div.is_visible():
        logger.debug("row=%d: supported_department container not visible, skipping", row)
        return
    
    logger.debug("row=%d: Supported Department - requesting: '%s'", row, department_text)
    
    # Step 2: Locate select element
    select_elem = page.locator("#supportedMinistriesId")
    if not select_elem.count():
        logger.warning("row=%d: supported_department select #supportedMinistriesId not found", row)
        return
    
    # Step 3: Wait explicitly for non-empty options (portal may be refreshing from Flagship Scheme)
    logger.debug("row=%d: Waiting for supported_department options to populate", row)
    try:
        deadline = time.time() + OPTIONS_TIMEOUT / 1000
        while True:
            opts = page.eval_on_selector(
                "#supportedMinistriesId",
                "el => Array.from(el.options)"
                "       .filter(o => o.value !== '')"
                "       .map(o => ({value: o.value, text: o.textContent.trim()}))"
            )
            if opts:
                logger.debug("row=%d: supported_department options available: %d options", row, len(opts))
                break
            if time.time() > deadline:
                available = page.eval_on_selector(
                    "#supportedMinistriesId",
                    "el => Array.from(el.options).map(o => o.textContent.trim())"
                )
                logger.warning(
                    "row=%d: supported_department options never populated. Available: %s",
                    row, available
                )
                return
            page.wait_for_timeout(OPTIONS_POLL_MS)
    except Exception as e:
        logger.warning("row=%d: failed to check supported_department options: %s", row, str(e)[:100])
        return
    
    # Step 4: Use the shared exact/normalized dropdown matcher.
    try:
        select_by_text(page, "#supportedMinistriesId", department_text)
        logger.info("row=%d: supported_department selected: '%s'", row, department_text)
    except Exception as e:
        logger.warning("row=%d: supported_department option not found: %s", row, str(e)[:180])
        raise RuntimeError(f"Supported Department option not found: {department_text}")


# ══════════════════════════════════════════════════════════════════════════════
# FormFiller
# ══════════════════════════════════════════════════════════════════════════════

class FormFiller:

    def __init__(self, page: Page, config: dict,
                 screenshots_dir: str = "screenshots",
                 output_repository=None) -> None:
        self.page      = page
        self.config    = config
        self.output_repository = output_repository
        self.output_registry = ActivityOutputHandlerRegistry(page)
        self.shots_dir = Path(screenshots_dir)
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.page.on("console", self._log_browser_console)
        self.page.on("pageerror", self._log_browser_pageerror)

    def _log_browser_console(self, msg) -> None:
        message_type = getattr(msg, "type", "log")
        message_text = getattr(msg, "text", "")
        if callable(message_text):
            try:
                message_text = message_text()
            except Exception:
                message_text = ""
        logger.debug("browser_console[%s]: %s", message_type, message_text)

    def _log_browser_pageerror(self, exc) -> None:
        logger.warning("browser_pageerror: %s", exc)

    def _get_optional_value(self, record: dict, *keys: str) -> str:
        for key in keys:
            value = _v(record, key)
            if value:
                return value
        return ""

    def _normalize_output_type(self, value: str) -> str:
        return normalize_output_type(value)

    def _activity_output_visible(self) -> bool:
        div = self.page.locator("#outputActvityTypeDivId")
        return div.count() and div.is_visible()

    def _collect_output_radio_diagnostics(self) -> list[dict]:
        radios: list[dict] = []
        items = self.page.locator("#outputActvityTypeDivId input[type='radio'][name='outputTyp']")
        for idx in range(items.count()):
            radio = items.nth(idx)
            radio_id = radio.get_attribute("id") or ""
            value = radio.get_attribute("value") or ""
            name = radio.get_attribute("name") or ""
            label_text = self.page.evaluate(
                """
                (id) => {
                    const el = document.getElementById(id);
                    if (!el) return '';
                    const parent = el.parentElement;
                    if (!parent) return '';
                    const text = parent.textContent || '';
                    return text.replace(/\\s+/g, ' ').trim();
                }
                """,
                radio_id,
            ) if radio_id else ""

            radios.append(
                {
                    "id": radio_id,
                    "name": name,
                    "value": value,
                    "visible": radio.is_visible(),
                    "enabled": radio.is_enabled(),
                    "checked": radio.is_checked(),
                    "label": label_text,
                }
            )
        return radios

    def _fill_activity_output(self, record: dict, row_index: int) -> bool:
        output_type_raw = self._get_optional_value(record, "activity_output_type")
        if not output_type_raw:
            logger.debug("row=%d activity_output: no Activity Output value in Excel", row_index)
            return False

        activity_key = _v(record, "activity_key")
        if not activity_key:
            raise ActivityOutputError("Activity_Key is required")

        if not self._activity_output_visible():
            raise ActivityOutputError("Activity Output section is not visible")

        selected_output = self._normalize_output_type(output_type_raw)

        logger.debug(
            "row=%d activity_output: requested='%s' normalized='%s' diagnostics=%s",
            row_index,
            output_type_raw,
            selected_output,
            self._collect_output_radio_diagnostics(),
        )
        if not selected_output:
            raise ActivityOutputError(f"Unsupported Activity Output: {output_type_raw}")

        if self.output_repository is None:
            raise ActivityOutputError("Activity Output repository is not configured")

        pdi_error_visible = self.page.locator("#activityMAError:not(.customhide)").count() > 0
        if pdi_error_visible:
            raise ActivityOutputError(
                "Cannot proceed to Activity Output because activityMAError is visible"
            )

        portal_validation_state = self.page.evaluate(
            """
            () => {
                const checkboxes = Array.from(document.querySelectorAll('#checkboxes input[type="checkbox"][id^="missionAdId"]'));
                const checked = checkboxes.filter(cb => cb.checked).map(cb => ({id: cb.id, value: cb.value, name: cb.name}));
                return {
                    checked,
                    activityMAErrorVisible: !!document.querySelector('#activityMAError:not(.customhide)'),
                    outputAreaVisible: !!document.querySelector('#outputActvityTypeDivId') && getComputedStyle(document.querySelector('#outputActvityTypeDivId')).display !== 'none',
                };
            }
            """
        )
        logger.debug("[%s] pre_training_validation_state=%s", activity_key, portal_validation_state)

        detail_record = self.output_repository.get_output_record(
            output_type_raw,
            activity_key,
        )
        logger.info("[%s] Activity Output detail record found", activity_key)

        handler = self.output_registry.resolve(output_type_raw)
        handler.process(
            activity_key=activity_key,
            detail_record=detail_record,
            row_index=row_index,
        )
        return True

    def _verify_main_state_after_output(self, record: dict) -> None:
        pdi = _v(record, "pdi_indicator")
        if not pdi:
            return
        selected = self.page.locator(
            "#maDivId #checkboxes input[type='checkbox'][name^='missionAntodayaDet']:checked"
        ).count()
        if selected == 0:
            raise RuntimeError(
                "Panchayat Advancement Index Indicator state was lost after Activity Output submission"
            )

    # ── Public entry point ────────────────────────────────────────────────────

    def fill_form(self, record: dict, row_index: int) -> dict[str, str]:
        """
        Fill one row from the Excel data into the open addactivity.htm form.
        Follows the AJAX dependency chain precisely.
        Returns {"application_number": str, "transaction_id": str}.
        Raises RuntimeError listing every failed field if any step fails.
        """
        errors: list[str] = []
        warnings: list[str] = []
        p = self.page

        def attempt(field: str, fn, fatal: bool = True) -> None:
            try:
                fn()
            except Exception as exc:
                msg = str(exc)
                target = errors if fatal else warnings
                target.append(f"[{field}] {msg}")
                level = "warning" if fatal else "non-fatal"
                logger.warning("row=%d field=%s %s %s", row_index, field, level, msg[:200])
                _snapshot(p, field, row_index, self.shots_dir, msg, fatal=fatal)

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
        
        # CRITICAL: Wait for portal's watchId spinner to disappear
        # This ensures getMissionAntyodayaMas() AJAX has finished populating checkboxes
        # networkidle completes while portal AJAX is still in progress
        try:
            p.wait_for_function(
                "() => { "
                "  const el = document.getElementById('watchId'); "
                "  if (!el) return true; "
                "  const style = window.getComputedStyle(el); "
                "  return style.display === 'none' || el.hidden || el.classList.contains('hidden'); "
                "}",
                timeout=OPTIONS_TIMEOUT
            )
            logger.debug("row=%d: watchId spinner finished, checkboxes should be loaded", row_index)
        except PlaywrightTimeout:
            logger.warning("row=%d: watchId spinner did not finish, PDI checkboxes may not load", row_index)

        # ── 4. Activity Type ──────────────────────────────────────────────────
        attempt("activity_type",
                lambda: select_by_text(p, "#activityTypeListId", _v(record, "activity_type")))

        # ── 5. Activity Description ───────────────────────────────────────────
        attempt("activity_description",
                lambda: safe_fill(p, "#activityDescId", _v(record, "activity_description")))

        # ── 6. VPRP Plan ─────────────────────────────────────────────────────
        vprp_plan = _v(record, "vprp_plan")
        if vprp_plan:
            attempt("vprp_plan", lambda: fill_vprp_plan(p, vprp_plan))

        # ── 7. PDI / Panchayat Advancement Index Indicator ────────────────────
        # Use dedicated portal-specific handler
        pdi = _v(record, "pdi_indicator")
        def _pdi():
            select_panchayat_advancement_indicators(p, pdi, row_index, self.shots_dir)
        attempt("pdi_indicator", _pdi)

        # ── 8. Shareable Activity (radio, conditional) ────────────────────────
        shareable = _v(record, "shareable").lower()
        def _shareable():
            if not shareable:
                return
            # Check if the shareable section is visible
            div = p.locator("#shareablediv")
            if not div.count() or not div.is_visible():
                logger.debug("row=%d: shareable section not visible", row_index)
                return
            if shareable in ("yes", "true", "1"):
                p.locator("input[name='shareable'][value='true']").click()
            else:
                p.locator("input[name='shareable'][value='false']").click()
        attempt("shareable", _shareable)

        # ── 8. Activity For ───────────────────────────────────────────────────
        # Static options: All | GEN | SC | ST
        attempt("activity_for",
                lambda: select_by_text(p, "#activityFor", _v(record, "activity_for")))

        # ── 9. Targeted Populace ──────────────────────────────────────────────
        # Second multiselect; inner panel id="checkboxess" (double-s).
        targeted = _v(record, "targeted_populace")
        def _targeted():
            if not targeted:
                return
            open_checkbox_panel(p, ".multiselect:has(#checkboxess)", "checkboxess")
            select_multiselect_checkboxes(p, "checkboxess", targeted, row_index, "targeted_populace")
            try:
                p.locator("#activityDescId").click()
            except Exception:
                pass
        attempt("targeted_populace", _targeted)

        # ── 10. Activity Nature ────────────────────────────────────────────────
        # onchange → showHideDivForWorkType() → getMajorHeadList() via DWR
        # HTML options: New/Fresh | Operational | Maintenance | Upgradation
        attempt("activity_nature",
                lambda: select_by_text(p, "#workTypId", _v(record, "activity_nature")))
        _wait_idle(p)

        # ── 11. Dynamic Major Head (conditional) ───────────────────────────────
        # This field is only visible after Activity Nature is selected for certain values
        major = _v(record, "major_head_choice")
        def _major():
            if not major:
                return
            div = p.locator("#subMajorHeadDivId")
            if not div.count() or not div.is_visible():
                logger.debug("row=%d: major head section not visible", row_index)
                return
            select = p.locator("#submjrPrmptId")
            if select.count():
                select_by_text(p, "#submjrPrmptId", major)
                _wait_idle(p)  # Wait for dependent Minor Head to populate
        attempt("major_head_choice", _major)

        # ── 12. Dynamic Minor Head (conditional) ───────────────────────────────
        # Only visible after Major Head is selected
        minor = _v(record, "minor_head_choice")
        def _minor():
            if not minor:
                return
            div = p.locator("#subMinorHeadDivId")
            if not div.count() or not div.is_visible():
                logger.debug("row=%d: minor head section not visible", row_index)
                return
            select = p.locator("#minorPrmptId")
            if select.count():
                select_by_text(p, "#minorPrmptId", minor)
                _wait_idle(p)
        attempt("minor_head_choice", _minor)

        # ── 13. Is directly funded by Panchayat? (radio) ─────────────────────
        # HTML: activityForCostlessFlagNoId  value="0" → Yes (funded)
        #        activityForCostlessFlagYesId value="1" → No  (not funded / costless)
        funded = _v(record, "is_directly_funded_by_panchayat").lower()
        def _funded():
            if not funded:
                return
            if funded in ("yes", "1", "true"):
                p.locator("#activityForCostlessFlagNoId").click()
            else:
                p.locator("#activityForCostlessFlagYesId").click()
            _wait_idle(p)  # Wait for cost section visibility to update
        attempt("is_directly_funded_by_panchayat", _funded)

        # ── 14. Estimated completion time ─────────────────────────────────────
        attempt("estimated_completion_year",
                lambda: safe_fill(p, "#totDurYearId", _v(record, "estimated_completion_year")))
        attempt("estimated_completion_month",
                lambda: safe_fill(p, "#totDurMonId",  _v(record, "estimated_completion_month")))
        attempt("estimated_completion_days",
                lambda: safe_fill(p, "#totDurDayId",  _v(record, "estimated_completion_days")))

        # ── 15. Start year / month ────────────────────────────────────────────
        # onchange on startYearId → populateStartMonthList() → populates months
        previous_start_month_signature = _option_signature(p, "#startMonthId")
        attempt("start_year",
                lambda: select_by_text(p, "#startYearId",  _v(record, "start_year")))
        # Wait for the portal to rebuild the month list after the year change.
        try:
            wait_for_options_refresh(p, "#startMonthId", previous_start_month_signature)
            wait_for_options_stable(p, "#startMonthId")
        except TimeoutError:
            logger.debug("row=%d: start month options did not populate", row_index)
        
        attempt(
            "start_month",
            lambda: select_start_month(p, "#startMonthId", _v(record, "start_month")),
            fatal=True,
        )

        # ── 16. Expected beneficiaries ────────────────────────────────────────
        attempt("expected_beneficiary_general",
                lambda: safe_fill(p, "#expctdMenGenId", _v(record, "expected_beneficiary_general")))
        attempt("expected_beneficiary_sc",
                lambda: safe_fill(p, "#expctdMenScId",  _v(record, "expected_beneficiary_sc")))
        attempt("expected_beneficiary_st",
                lambda: safe_fill(p, "#expctdMenStId",  _v(record, "expected_beneficiary_st")))
        # Trigger keyup-driven calculations after filling beneficiary fields.
        try:
            _dispatch_input_events(p, "#expctdMenGenId")
            _dispatch_input_events(p, "#expctdMenScId")
            _dispatch_input_events(p, "#expctdMenStId")
            p.evaluate(
                """
                () => {
                    if (typeof totalsumexpectedBeneficiaries === 'function') {
                        totalsumexpectedBeneficiaries();
                    }
                }
                """
            )
        except Exception:
            pass

        # ── 17. Indicative Unit Cost (conditional) ────────────────────────────
        unit_cost = _v(record, "indicative_unit_cost")
        def _unit_cost():
            if not unit_cost:
                return
            div = p.locator("#indicativeUnitCostDivId")
            if not div.count() or not div.is_visible():
                logger.debug("row=%d: indicative unit cost section not visible", row_index)
                return
            safe_fill(p, "#indicativeUnitCostId", unit_cost)
            # Trigger keyup-driven total cost calculation.
            try:
                _dispatch_input_events(p, "#indicativeUnitCostId")
                p.evaluate(
                    """
                    () => {
                        const unit = document.getElementById('indicativeUnitCostId');
                        if (typeof calculateTotalIndicativeCost === 'function') {
                            calculateTotalIndicativeCost(unit ? unit.value : '');
                        }
                    }
                    """
                )
            except Exception:
                pass
        attempt("indicative_unit_cost", _unit_cost)

        # ── 18. Total Indicative Cost (verify, portal-calculated) ──────────────
        # Just verify this auto-calculated field after filling unit cost
        def _verify_total_cost():
            div = p.locator("#costlessTotalCostDivId")
            if div.count() and div.is_visible():
                field = p.locator("#costlessTotalCostId")
                if field.count():
                    total = (field.input_value() or "").strip()
                    expected_total = _v(record, "total_indicative_cost")
                    logger.debug(
                        "row=%d: total indicative cost calculated as: %s (excel: %s)",
                        row_index,
                        total,
                        expected_total,
                    )
                    expected_num = _to_number_or_none(expected_total)
                    actual_num = _to_number_or_none(total)
                    if expected_num is not None and actual_num is not None and abs(expected_num - actual_num) > 0.0001:
                        logger.warning(
                            "row=%d: total indicative cost mismatch ignored because portal recalculates from live inputs (excel=%s, portal=%s)",
                            row_index,
                            expected_total,
                            total,
                        )
        attempt("total_indicative_cost", _verify_total_cost)

        # ── 19. Expected Result (conditional) ──────────────────────────────────
        expected_result = _v(record, "expected_results")
        def _expected_result():
            if not expected_result:
                return
            div = p.locator("#expctdResultDivId")
            if not div.count() or not div.is_visible():
                logger.debug("row=%d: expected result section not visible", row_index)
                return
            safe_fill(p, "#expctdResultId", expected_result)
        attempt("expected_results", _expected_result)

        # ── 20. Flagship Scheme (conditional) ──────────────────────────────────
        flagship = _v(record, "flagship_scheme")
        def _flagship():
            if not flagship:
                return
            div = p.locator("#flagshipSchemeDivId")
            if not div.count() or not div.is_visible():
                raise ValueError("Flagship Scheme section is not visible")
            select = p.locator("#flagshipSchemeId")
            if not select.count():
                raise ValueError("Flagship Scheme control #flagshipSchemeId was not found")

            # select_by_text only accepts an exact or normalized-equivalent option;
            # it never falls back to a different scheme.
            select_by_text(p, "#flagshipSchemeId", flagship)
            selected_text = select.locator("option:checked").inner_text().strip()
            if _dropdown_match_key(selected_text) != _dropdown_match_key(flagship):
                raise ValueError(
                    f"Flagship Scheme selection verification failed: requested={flagship!r} "
                    f"selected={selected_text!r}"
                )
            logger.info(
                "row=%d: flagship scheme selected exact_requested=%r matched=%r",
                row_index, flagship, selected_text,
            )
            _wait_idle(p)  # Wait for Supported Department to refresh
        attempt("flagship_scheme", _flagship)

        # ── 21. Supported Department (conditional, depends on Flagship Scheme) ─
        department = _v(record, "select_supported_department")
        def _department():
            if not department:
                return
            select_supported_department(p, department, row_index)
        attempt("select_supported_department", _department, fatal=False)

        # ── 22. Estimated Total Cost ──────────────────────────────────────────
        # This field can be hidden or enabled depending on funding and other conditions
        estimated_cost = _v(record, "estimated_total_cost")
        def _estimated_cost():
            if not estimated_cost:
                return
            div = p.locator("#totalCostDivId")
            if not div.count() or not div.is_visible():
                logger.debug("row=%d: estimated total cost section not visible", row_index)
                return
            field = p.locator("#totalCostId")
            if field.count() and field.is_enabled():
                safe_fill(p, "#totalCostId", estimated_cost)
        attempt("estimated_total_cost", _estimated_cost)

        # ── 23. Operational Type (conditional) ────────────────────────────────
        op_type = _v(record, "operation_type")
        def _op_type():
            if not op_type:
                return
            div = p.locator("#oprationalActivityTypeDivId")
            if not div.count() or not div.is_visible():
                logger.debug("row=%d: operational type section not visible", row_index)
                return
            select = p.locator("#operationTypeId")
            if select.count():
                try:
                    select_by_text(p, "#operationTypeId", op_type)
                    _wait_idle(p)
                except Exception:
                    logger.debug("row=%d: operational type option not found", row_index)
        attempt("operation_type", _op_type)

        # ── 24. Operational Remarks (conditional) ────────────────────────────
        op_remarks = _v(record, "operation_remarks")
        def _op_remarks():
            if not op_remarks:
                return
            field = p.locator("#operationRemarksId")
            if field.count() and field.is_visible():
                safe_fill(p, "#operationRemarksId", op_remarks)
        attempt("operation_remarks", _op_remarks)

        # ── 25. Activity Output / Dynamic Output workflow ────────────────────
        pdi_failed = any(err.startswith("[pdi_indicator]") for err in errors)
        if pdi_failed:
            logger.warning(
                "row=%d activity_output_popup skipped because pdi_indicator validation failed",
                row_index,
            )
        else:
            try:
                output_processed = self._fill_activity_output(record, row_index)
                if output_processed:
                    self._verify_main_state_after_output(record)
            except Exception as exc:
                msg = str(exc)
                errors.append(f"[activity_output] {msg}")
                logger.warning("row=%d activity_output_popup %s", row_index, msg[:200])
                _snapshot(p, "activity_output", row_index, self.shots_dir, msg)

        # ── 26. Form submission ───────────────────────────────────────────────
        app_number = ""
        txn_id     = ""

        if self.config.get("submit_form", False) and not errors:
            page_errors = _check_page_errors(p)
            if page_errors:
                # Format error messages for display
                error_messages = []
                for err in page_errors:
                    err_text = err.get("text", "Unknown error")
                    err_id = err.get("id", "")
                    if err_id:
                        error_messages.append(f"[{err_id}] {err_text}")
                    else:
                        error_messages.append(err_text)
                errors.append(f"[validation] {'; '.join(error_messages)}")
                logger.warning(
                    "row=%d: portal validation errors: %s",
                    row_index, "; ".join(error_messages)
                )
            else:
                try:
                    final_action = normalize_final_action(_v(record, "final_action"))
                    if not final_action:
                        raise RuntimeError("Final Action is required")

                    if final_action == "Save and Forward":
                        logger.info("[%s] Final Action: Save and Forward", _v(record, "activity_key"))
                        p.locator("#saveAndForwardId").click()
                    else:
                        logger.info("[%s] Final Action: Save", _v(record, "activity_key"))
                        p.locator("#saveAsDraftId").click()

                    _wait_idle(p)
                    app_number = self._text(p, "[id*='appNo'],[id*='applicationNo'],.app-number")
                    txn_id     = self._text(p, "[id*='txn'],[id*='transaction'],.txn-id")
                except Exception as exc:
                    errors.append(f"[submit] {exc}")

        if errors:
            raise RuntimeError(" | ".join(errors))

        if warnings:
            logger.warning("row=%d non-fatal field issues: %s", row_index, " | ".join(warnings))

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
