# eGramSwaraj Activity Planning Bot (v3)

Automated form filler for eGramSwaraj GPDP Activity Planning portal.
Supports manual login, ENTER-to-start gate, session expiry recovery,
and resume from last completed row.

---

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Running

```bash
python src/main.py
```

### Process specific rows only (0-indexed):
```bash
python src/main.py --rows 0,1,2
```

### Custom config path:
```bash
python src/main.py --config config/config.json
```

---

## Workflow

```
1. Bot launches Chromium and opens https://egramswaraj.gov.in
2. YOU log in manually in the browser window
3. YOU navigate to: https://egramswaraj.gov.in/addactivity.htm
4. Bot detects the form and prints: "Press ENTER to start automation"
5. YOU press ENTER
6. Bot processes each Excel row:
   - Navigates to addactivity.htm (reuses your session)
   - Fills all fields respecting AJAX dependency chain
   - Saves result to output.xlsx
7. If session expires mid-run:
   - Bot pauses and prints: "SESSION EXPIRED — please log in again"
   - YOU log in and navigate back to addactivity.htm
   - Bot resumes automatically from where it stopped
```

---

## Configuration (`config/config.json`)

| Key | Description |
|-----|-------------|
| `retry_count` | Retries per row on failure (default 3) |
| `submit_form` | `true` to click Save, `false` for dry-run (fill only) |
| `selector_timeout_ms` | Max wait for any element (default 20000ms) |
| `options_timeout_ms` | Max wait for AJAX dropdown to populate (default 20000ms) |
| `take_success_screenshots` | Save screenshot after each successful fill |
| `debug_mode` | Extra logging of all select states on failure |

> **Note:** `headless` is always forced to `false` — the browser must be visible
> so you can log in. Do not set headless in config.

---

## AJAX Dependency Chain

The form uses cascading AJAX dropdowns. The bot handles them in this exact order:

```
#themeId  (select theme)
    ↓ onchange → AJAX loads activity names
#themeActivityNameID  (Select2 widget — search by typing)
    ↓ onchange → AJAX loads focus areas
#focusAreaId  (select focus area)
    ↓ onchange → AJAX loads activity types + PDI indicators
#activityTypeListId  (select activity type)
#maDivId checkboxes  (PDI indicators — click panel to open)
#activityFor  (select "All / GEN / SC / ST")
#checkboxess  (targeted populace checkboxes)
#workTypId  (activity nature: Fresh / Maintenance / Upgradation)
    ↓ onchange → conditionally shows major/minor head divs
#submjrPrmptId  (major head — only if #subMajorHeadDivId visible)
#minorPrmptId   (minor head — only if #subMinorHeadDivId visible)
```

Each step uses `wait_for_options()` which polls the dropdown until real options
appear — never a blind timer.

---

## Field Mapping

| Excel Column | HTML Element | Type |
|---|---|---|
| `theme` | `#themeId` | select |
| `activity_name` | `#themeActivityNameID` | Select2 |
| `focus_area` | `#focusAreaId` | select (AJAX) |
| `activity_type` | `#activityTypeListId` | select (AJAX) |
| `activity_description` | `#activityDescId` | textarea |
| `pdi_indicator` | `#maDivId` checkboxes | multi-checkbox |
| `activity_for` | `#activityFor` | select |
| `targeted_populace` | `#checkboxess` | multi-checkbox |
| `activity_nature` | `#workTypId` | select (AJAX) |
| `is_directly_funded_by_panchayat` | radio buttons | Yes/No |
| `estimated_completion_year` | `#totDurYearId` | text |
| `estimated_completion_month` | `#totDurMonId` | text |
| `estimated_completion_days` | `#totDurDayId` | text |
| `start_year` | `#startYearId` | select |
| `start_month` | `#startMonthId` | select |
| `expected_beneficiary_general` | `#expctdMenGenId` | text |
| `expected_beneficiary_sc` | `#expctdMenScId` | text |
| `expected_beneficiary_st` | `#expctdMenStId` | text |
| `estimated_total_cost` | `#totalCostId` | text |

---

## Output

- `data/output.xlsx` — original data + status per row
- `logs/automation.log` — full debug log
- `screenshots/row_N_success.png` — after each successful fill
- `screenshots/row_N_attemptK_error.png` — on failure
- `screenshots/rowN_FIELD_error.html` — full page HTML snapshot on field failure

### Status columns added to output.xlsx:

| Column | Values |
|---|---|
| `status` | `SUCCESS` or `FAILED` |
| `processed_time` | Timestamp |
| `application_number` | From confirmation (if submit_form=true) |
| `transaction_id` | From confirmation (if submit_form=true) |
| `error_message` | Detailed error for FAILED rows |

---

## Resume Feature

Rows already marked `SUCCESS` in output.xlsx are skipped automatically.
If the run stops at row 12, re-run — it picks up from row 13.

---

## Dry Run (default)

By default `submit_form` is `false` — the bot fills all fields but does NOT
click Save. Verify the filled form visually before setting `submit_form: true`.
