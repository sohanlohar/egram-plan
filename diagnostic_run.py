"""
DIAGNOSTIC MODE: Run automation for one row with complete browser state capture
"""
import logging
import sys
import json
import os
from pathlib import Path

# Fix encoding for Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from src.logger import setup_logger
from src.excel_reader import ExcelReader
from src.form_filler import FormFiller

logger = setup_logger(__name__)

def print_section(title):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print('='*80)

def get_browser_state(page, focus_on=""):
    """Capture current browser state"""
    state = {}
    try:
        # Core field values
        fields = {
            'themeId': '#themeId',
            'themeActivityNameID': '#themeActivityNameID',
            'focusAreaId': '#focusAreaId',
            'activityTypeListId': '#activityTypeListId',
            'activityDescId': '#activityDescId',
            'workTypId': '#workTypId',
            'activityFor': '#activityFor',
            'flagshipSchemeId': '#flagshipSchemeId',
            'supportedMinistriesId': '#supportedMinistriesId',
            'totalCostId': '#totalCostId',
            'outputActvityAstId102': '#outputActvityAstId102',
            'ouptputTypId': '#ouptputTypId',
            'showTrainingDetailsPopup': '#showTrainingDetailsPopup',
        }
        
        for field_name, selector in fields.items():
            try:
                el = page.query_selector(selector)
                if el:
                    if 'Popup' in field_name:
                        state[field_name] = {
                            'display': el.evaluate("el => window.getComputedStyle(el).display"),
                            'visibility': el.evaluate("el => window.getComputedStyle(el).visibility"),
                            'visible': el.is_visible() if el else False,
                            'className': el.get_attribute("class") or ""
                        }
                    elif field_name in ['outputActvityAstId102']:
                        state[field_name] = {
                            'selected': el.is_checked(),
                            'checked': el.evaluate("el => el.checked"),
                            'value': el.get_attribute("value"),
                            'onclick': el.get_attribute("onclick") or ""
                        }
                    elif field_name in ['themeActivityNameID']:
                        state[field_name] = {
                            'value': el.input_value() if el else "",
                            'visible_text': page.evaluate(
                                f"() => {{"
                                f"  const sel = document.getElementById('{field_name}');"
                                f"  if (!sel) return '';"
                                f"  const container = sel.nextElementSibling;"
                                f"  if (!container) return '';"
                                f"  const text = container.querySelector('.select2-selection__rendered');"
                                f"  return text ? text.textContent.trim() : '';"
                                f"}}"
                            )
                        }
                    else:
                        state[field_name] = {
                            'value': el.input_value() if el else el.inner_text(),
                            'type': el.get_attribute("type") or el.tag_name,
                            'disabled': el.evaluate("el => el.disabled"),
                            'visible': el.is_visible()
                        }
            except Exception as e:
                state[field_name] = f"ERROR: {str(e)[:80]}"
        
        return state
    except Exception as e:
        return {"error": str(e)[:200]}

def diagnostic_run():
    """Run ONE row with comprehensive diagnostics"""
    config_path = Path("config/config.json")
    if not config_path.exists():
        logger.error("Config not found")
        return

    with open(config_path) as f:
        config = json.load(f)
    
    # Force single row, no submit yet
    config['submit_form'] = False
    config['headless'] = False
    
    reader = ExcelReader(config['input_file'])
    records = list(reader.pending_rows())
    
    if not records:
        logger.error("No records in Excel")
        return
    
    print_section("PHASE 3 DIAGNOSTIC: ONE ROW TEST")
    print(f"\nTesting ROW 0 of {len(records)} total rows")
    
    row_index, record = records[0]
    print(f"\nExcel Row {row_index} Data:")
    for key in ['theme', 'activity_name', 'focus_area', 'activity_type', 'pdi_indicator', 
                'flagship_scheme', 'select_supported_department', 'activity_output_type',
                'training_category', 'training_organized_by', 'training_subject',
                'training_total_trainees', 'training_total_duration_days']:
        val = record.get(key, '')
        print(f"  {key}: {repr(val)}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.get("headless", False))
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Navigate to form
            print_section("NAVIGATING TO FORM")
            page.goto("https://egramswaraj.gov.in/addactivity.htm", wait_until="networkidle")
            print("✓ Form page loaded")
            
            # Wait for manual login
            print_section("WAITING FOR MANUAL LOGIN")
            print("Please log in manually in the browser... Waiting up to 5 minutes...")
            try:
                page.wait_for_selector("#themeId:visible", timeout=5*60*1000)
                print("✓ Login detected, theme dropdown appeared")
            except PlaywrightTimeout:
                print("✗ Login timeout or form never appeared")
                return
            
            # Create form filler
            filler = FormFiller(page, config)
            
            # Now run diagnostics for each step
            print_section("STEP 1: FILL BASIC FIELDS")
            
            # Theme
            print("\n1. Selecting Theme...")
            try:
                from src.form_filler import select_by_text
                select_by_text(page, "#themeId", record['theme'])
                page.wait_for_load_state("networkidle", timeout=8000)
                print(f"✓ Theme selected: {record['theme']}")
            except Exception as e:
                print(f"✗ Theme failed: {e}")
                return
            
            # Activity Name (Select2)
            print("\n2. Selecting Activity Name...")
            try:
                from src.form_filler import fill_select2
                fill_select2(page, "themeActivityNameID", record['activity_name'])
                print(f"✓ Activity Name selected: {record['activity_name']}")
            except Exception as e:
                print(f"✗ Activity Name failed: {e}")
                return
            
            # Focus Area
            print("\n3. Selecting Focus Area...")
            try:
                select_by_text(page, "#focusAreaId", record['focus_area'])
                page.wait_for_load_state("networkidle", timeout=8000)
                print(f"✓ Focus Area selected: {record['focus_area']}")
            except Exception as e:
                print(f"✗ Focus Area failed: {e}")
                return
            
            print_section("ADVANCEMENT INDEX DIAGNOSTICS AFTER FOCUS AREA")
            
            # Get focus area state
            focus_val = page.locator("#focusAreaId").input_value()
            focus_text = page.locator("#focusAreaId option:checked").inner_text()
            
            print(f"\nFocus Area selected value: {focus_val}")
            print(f"Focus Area selected text: {focus_text}")
            
            # PDI diagnostics
            watch_id = page.query_selector("#watchId")
            if watch_id:
                watch_class = watch_id.get_attribute("class") or ""
                print(f"watchId className: {watch_class}")
            
            ma_div = page.query_selector("#maDivId")
            print(f"maDivId exists: {ma_div is not None}")
            if ma_div:
                print(f"maDivId visible: {ma_div.is_visible()}")
            
            checkboxes_div = page.query_selector("#maDivId #checkboxes")
            print(f"checkboxes div exists: {checkboxes_div is not None}")
            if checkboxes_div:
                display = checkboxes_div.evaluate("el => window.getComputedStyle(el).display")
                print(f"checkboxes.style.display: {display}")
            
            # Count checkboxes
            checkbox_count = page.locator("#maDivId #checkboxes input[type='checkbox']").count()
            enabled_count = page.locator(
                "#maDivId #checkboxes input[type='checkbox']:not([disabled])"
            ).count()
            checked_count = page.locator(
                "#maDivId #checkboxes input[type='checkbox']:checked"
            ).count()
            
            print(f"Total checkboxes: {checkbox_count}")
            print(f"Enabled checkboxes: {enabled_count}")
            print(f"Checked checkboxes: {checked_count}")
            
            # List all checkboxes
            print("\nAll checkboxes in panel:")
            checkboxes = page.locator("#maDivId #checkboxes input[type='checkbox']").all()
            for i, cb in enumerate(checkboxes):
                cb_id = cb.get_attribute("id") or ""
                cb_name = cb.get_attribute("name") or ""
                cb_value = cb.get_attribute("value") or ""
                cb_disabled = cb.evaluate("el => el.disabled")
                cb_checked = cb.evaluate("el => el.checked")
                cb_visible = cb.is_visible()
                
                # Get parent label
                label = cb.locator("xpath=ancestor::label[1]")
                label_text = (label.inner_text() if label.count() else "") or ""
                
                onclick = cb.get_attribute("onclick") or ""
                
                print(f"\n  [{i}] id={cb_id}")
                print(f"      name={cb_name}")
                print(f"      value={cb_value}")
                print(f"      disabled={cb_disabled}")
                print(f"      checked={cb_checked}")
                print(f"      visible={cb_visible}")
                print(f"      label='{label_text}'")
                if onclick:
                    print(f"      onclick='{onclick}'")
            
            # Match logic
            print("\n" + "="*80)
            excel_pdi = record.get('pdi_indicator', '')
            print(f"Excel pdi_indicator: {repr(excel_pdi)}")
            
            from src.form_filler import _normalize_multiselect_text
            normalized_excel = _normalize_multiselect_text(excel_pdi)
            print(f"Normalized Excel value: {repr(normalized_excel)}")
            
            print("\nNormalized checkbox labels:")
            available_map = {}
            for cb in checkboxes:
                label = cb.locator("xpath=ancestor::label[1]")
                label_text = (label.inner_text() if label.count() else "") or ""
                normalized_label = _normalize_multiselect_text(label_text)
                available_map[normalized_label] = label_text
                print(f"  '{normalized_label}' <- '{label_text}'")
            
            if normalized_excel in available_map:
                print(f"\n✓ MATCHED CHECKBOX = {available_map[normalized_excel]}")
            else:
                print(f"\n✗ ADVANCEMENT INDICATOR MATCH FAILURE")
                print(f"   Requested: {repr(normalized_excel)}")
                print(f"   Available: {list(available_map.keys())}")
                return
            
            # Try to click it using current implementation
            print("\n" + "="*80)
            print("ATTEMPTING PDI SELECTION WITH CURRENT IMPLEMENTATION")
            print("="*80)
            
            try:
                from src.form_filler import select_panchayat_advancement_indicators
                select_panchayat_advancement_indicators(page, excel_pdi, row_index)
                print("✓ PDI handler completed")
            except Exception as e:
                print(f"✗ PDI handler failed: {e}")
                print("\nCurrent checkbox states after handler:")
                for i, cb in enumerate(page.locator("#maDivId #checkboxes input[type='checkbox']").all()):
                    print(f"  [{i}] checked={cb.is_checked()}")
                return
            
            # Check if selected
            checked_count_after = page.locator(
                "#maDivId #checkboxes input[type='checkbox']:checked"
            ).count()
            
            # Find the matched checkbox and check its state
            for cb in checkboxes:
                label = cb.locator("xpath=ancestor::label[1]")
                label_text = (label.inner_text() if label.count() else "") or ""
                normalized_label = _normalize_multiselect_text(label_text)
                
                if normalized_label == normalized_excel:
                    cb_id = cb.get_attribute("id") or ""
                    cb_value = cb.get_attribute("value") or ""
                    is_selected = cb.is_checked()
                    dom_checked = cb.evaluate("el => el.checked")
                    
                    print(f"\nTarget checkbox state AFTER click:")
                    print(f"  id: {cb_id}")
                    print(f"  value: {cb_value}")
                    print(f"  is_selected(): {is_selected}")
                    print(f"  DOM checked: {dom_checked}")
                    print(f"  Total checked: {checked_count_after}")
                    
                    # Check error element
                    error_el = page.query_selector("#activityMAError")
                    if error_el:
                        error_class = error_el.get_attribute("class") or ""
                        error_display = error_el.evaluate("el => window.getComputedStyle(el).display")
                        error_visible = error_el.is_visible()
                        
                        print(f"\n  #activityMAError state:")
                        print(f"    class: {error_class}")
                        print(f"    display: {error_display}")
                        print(f"    visible: {error_visible}")
                    
                    # Final query
                    final_checked = page.evaluate(
                        "() => document.querySelectorAll(\"#maDivId #checkboxes input[name^='missionAntodayaDet']:checked\").length"
                    )
                    print(f"\n  Query result: document.querySelectorAll(...):checked = {final_checked}")
                    
                    if not is_selected and not dom_checked:
                        print(f"\n✗ CHECKBOX NOT SELECTED AFTER PDI HANDLER")
                        return
                    else:
                        print(f"\n✓ Checkbox appears selected")
                    
                    break
            
            # Activity Type
            print("\n" + "="*80)
            print("CONTINUING WITH SUBSEQUENT FIELDS")
            print("="*80)
            print("\n4. Selecting Activity Type...")
            try:
                select_by_text(page, "#activityTypeListId", record['activity_type'])
                print(f"✓ Activity Type selected: {record['activity_type']}")
                
                # CHECK PDI STATE
                pdi_checked_after_activity_type = page.evaluate(
                    "() => document.querySelectorAll(\"#maDivId #checkboxes input[name^='missionAntodayaDet']:checked\").length"
                )
                print(f"ADVANCEMENT CHECKED COUNT AFTER activity_type = {pdi_checked_after_activity_type}")
            except Exception as e:
                print(f"✗ Activity Type failed: {e}")
                return
            
            # Continue with more fields, checking PDI each time
            print("\n5. Filling Activity Description...")
            try:
                page.locator("#activityDescId").fill(record.get('activity_description', ''))
                pdi_checked = page.evaluate(
                    "() => document.querySelectorAll(\"#maDivId #checkboxes input[name^='missionAntodayaDet']:checked\").length"
                )
                print(f"ADVANCEMENT CHECKED COUNT AFTER activity_description = {pdi_checked}")
            except Exception as e:
                print(f"✗ Activity Description failed: {e}")
            
            print("\n6. Activity For...")
            try:
                select_by_text(page, "#activityFor", record.get('activity_for', ''))
                pdi_checked = page.evaluate(
                    "() => document.querySelectorAll(\"#maDivId #checkboxes input[name^='missionAntodayaDet']:checked\").length"
                )
                print(f"ADVANCEMENT CHECKED COUNT AFTER activity_for = {pdi_checked}")
            except Exception as e:
                print(f"✗ Activity For failed: {e}")
            
            print("\n7. Activity Nature...")
            try:
                select_by_text(page, "#workTypId", record.get('activity_nature', ''))
                page.wait_for_load_state("networkidle", timeout=8000)
                pdi_checked = page.evaluate(
                    "() => document.querySelectorAll(\"#maDivId #checkboxes input[name^='missionAntodayaDet']:checked\").length"
                )
                print(f"ADVANCEMENT CHECKED COUNT AFTER activity_nature = {pdi_checked}")
            except Exception as e:
                print(f"✗ Activity Nature failed: {e}")
            
            # Flagship & Supported Dept diagnostics
            print("\n" + "="*80)
            print("FLAGSHIP SCHEME & SUPPORTED DEPARTMENT DIAGNOSTICS")
            print("="*80)
            
            print("\nBefore Flagship Scheme selection:")
            fs_val = page.locator("#flagshipSchemeId").input_value() if page.query_selector("#flagshipSchemeId") else "N/A"
            print(f"  flagshipSchemeId value: {fs_val}")
            
            sm_opts = page.evaluate(
                "() => Array.from(document.getElementById('supportedMinistriesId')?.options || []).map(o => ({value: o.value, text: o.text}))"
            )
            print(f"  supportedMinistriesId options: {json.dumps(sm_opts, indent=4)}")
            
            print(f"\nSelecting Flagship Scheme: {record.get('flagship_scheme', '')}")
            try:
                select_by_text(page, "#flagshipSchemeId", record.get('flagship_scheme', ''))
                page.wait_for_load_state("networkidle", timeout=8000)
                print("✓ Flagship Scheme selected")
            except Exception as e:
                print(f"✗ Flagship Scheme failed: {e}")
            
            print("\nAfter Flagship Scheme selection:")
            fs_val_after = page.locator("#flagshipSchemeId").input_value()
            fs_text = page.locator("#flagshipSchemeId option:checked").inner_text()
            print(f"  flagshipSchemeId value: {fs_val_after}")
            print(f"  flagshipSchemeId text: {fs_text}")
            
            sd_div = page.query_selector("#supportedMinistriesDivId")
            print(f"  supportedMinistriesDivId visible: {sd_div.is_visible() if sd_div else 'N/A'}")
            
            print("\n  supportedMinistriesId options after Flagship:")
            sm_opts_after = page.evaluate(
                "() => Array.from(document.getElementById('supportedMinistriesId')?.options || []).map(o => ({value: o.value, text: o.text, selected: o.selected}))"
            )
            for opt in sm_opts_after:
                print(f"    value={opt['value']} text='{opt['text']}' selected={opt['selected']}")
            
            # Training output diagnostics
            print("\n" + "="*80)
            print("TRAINING/ACTIVITY OUTPUT DIAGNOSTICS")
            print("="*80)
            
            excel_output = record.get('activity_output_type', '')
            print(f"\nExcel activity_output_type: {repr(excel_output)}")
            
            if pd.isna(excel_output) or excel_output == '':
                print("⚠ activity_output_type is empty/NaN - Training will not be tested")
                print("\nDiagnostic run COMPLETE - No Training test due to empty activity_output_type")
            else:
                print("\nTraining radio diagnostics:")
                radio = page.query_selector("#outputActvityAstId102")
                if radio:
                    print(f"  exists: True")
                    print(f"  displayed: {radio.is_visible()}")
                    print(f"  enabled: {radio.evaluate('el => !el.disabled')}")
                    print(f"  selected: {radio.is_checked()}")
                    print(f"  value: {radio.get_attribute('value')}")
                    print(f"  name: {radio.get_attribute('name')}")
                    print(f"  onclick: {radio.get_attribute('onclick') or 'none'}")
                else:
                    print(f"  exists: False")
            
            print("\n✓ DIAGNOSTIC RUN COMPLETE")
            
        except Exception as e:
            logger.exception(f"Diagnostic run failed: {e}")
            print(f"\n✗ DIAGNOSTIC FAILED: {e}")
        finally:
            context.close()
            browser.close()

if __name__ == "__main__":
    import pandas as pd
    diagnostic_run()
