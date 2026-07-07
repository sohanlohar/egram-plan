# PROJECT CODEBASE ANALYSIS

Last updated: 2026-07-07

This document is the working technical context for the eGramSwaraj automation project. The source code remains the final source of truth; future changes should re-check the relevant code paths before editing.

## Activity Output Sheet Architecture (Production)

This project now uses an in-workbook normalized architecture for Activity Output data.

Workbook relationships:

- Activities.Activity_Key -> Training.Activity_Key
- Activities.Activity_Key -> Community_Service.Activity_Key
- Activities.Activity_Key -> Beneficiaries.Activity_Key
- Activities.Activity_Key -> Asset.Activity_Key

### Activities sheet

- Primary sheet name: `Activities`
- Stable relationship key: `Activity_Key` (for example `ACT-0001`)
- Includes all main Register Activity fields and `Final Action`
- `Final Action` is normalized case-insensitively and supports:
  - `Save`
  - `Save and Forward`

### Activity Output detail sheets

- `Training` is fully implemented for popup filling.
- `Community_Service`, `Beneficiaries`, and `Asset` are provisioned with key-only schema (`Activity_Key`) and registry entries.
- Unsupported output types fail clearly with:
  - `Unsupported Activity Output: <value>`

### Excel loading and indexing

- Workbook is loaded once per run by `ExcelReader`.
- `ExcelReader` now exposes:
  - `get_activity_rows()`
  - `get_output_record(output_type, activity_key)`
- In-memory indexes are built by normalized `Activity_Key` for each output sheet.
- Validation guarantees:
  - Missing `Activity_Key` in `Activities` causes failure.
  - Duplicate `Activity_Key` in `Activities` causes failure.
  - Missing Training detail record fails before radio click:
    - `Training output data not found for Activity_Key ACT-0001`
  - Duplicate Training detail record fails:
    - `Duplicate Training output data for Activity_Key ACT-0001`

### Handler registry and Training handler

- Registry module: `src/activity_output_registry.py`
  - Maps visible output values to canonical definitions.
  - Stores sheet, radio, modal, and handler metadata.
- Handler module: `src/activity_output_handlers.py`
  - Registry/factory: `ActivityOutputHandlerRegistry`
  - Implemented handler: `TrainingOutputHandler`
  - Exact Training mapping:
    - Radio: `outputActvityAstId102`
    - Modal: `showTrainingDetailsPopup`
    - Fields: `trngCatCdId`, `trngOrgByCdId`, `trngSubjectId`, `totTraineesId`, `totDurationDaysId`
    - Submit action: button in Training modal using `validationTraining()`

### Main processing flow changes

- `FormFiller` resolves output handlers through registry, not a large if/elif block.
- Before clicking output radio, detail row is loaded by output type + `Activity_Key`.
- Training detail row validation is enforced before popup open.
- After popup submit, main state is re-verified (especially PDI retained selection).
- Final submit action now comes from row-level `Final Action`, not global config.

### Workbook migration

- Migration utility: `src/workbook_migration.py`
- Backup file created before migration:
  - `data/input_backup_before_output_sheet_migration.xlsx`
- Migration behavior:
  - Creates/updates `Activities` and `Training` sheets in the same workbook.
  - Moves inline Training columns to `Training` by `Activity_Key`.
  - Removes inline Training popup fields from `Activities` schema.
  - Creates placeholder sheets for additional output types.
  - Validates row count, key integrity, training 1:1 linkage, and orphan/duplicate records.

### How to add a new Activity Output handler later

1. Add a definition in `ACTIVITY_OUTPUT_DEFINITIONS` with visible name, sheet, radio, modal, and handler metadata.
2. Extend workbook schema for that output sheet in `input.xlsx`.
3. Add output-sheet indexing rules in `ExcelReader._build_output_indexes()`.
4. Implement a dedicated handler class in `src/activity_output_handlers.py`.
5. Register the handler in `ActivityOutputHandlerRegistry.resolve()`.
6. Add validation and lookup tests in `tests/test_activity_output_architecture.py`.

## 1. Project Overview

This project automates entry of Gram Panchayat Development Plan activity rows into the eGramSwaraj Activity Planning portal at `https://egramswaraj.gov.in/addactivity.htm`.

The expected user workflow is:

1. Prepare activity data in `data/input.xlsx`.
2. Run `python src/main.py` or `python src/main.py --rows 0,1,2`.
3. The bot opens Chromium visibly.
4. The user logs in manually and navigates to `https://egramswaraj.gov.in/addactivity.htm`.
5. The bot waits until `#themeId` is visible.
6. The user presses ENTER to start automation.
7. The bot processes pending Excel rows, fills the activity form, optionally submits it, and writes status to `data/output.xlsx`.

Current implementation status:

- Main activity form automation is implemented in `src/form_filler.py`.
- Manual login and same-browser session reuse are implemented in `src/main.py`.
- Resume-by-status is implemented: rows with `status == SUCCESS` are skipped by `ExcelReader`.
- Output status writing and summary generation are implemented in `src/result_writer.py`.
- Activity Output handling is enforced for `Training/Capacity Building` only. Requested Training output must complete its modal submit workflow before Save or Save and Forward can run.
- Separate Activity Output workbook sheets still exist only in templates; the live Training implementation uses inline columns on the main `eGram Activity Data` row.
- The repository contains runtime artifacts (`logs/`, `screenshots/`, `.pyc`, `data/output.xlsx`) even though `.gitignore` intends to ignore many of them.

## 2. Technology Stack

- Python 3, using modern type hints such as `list[int]`.
- Playwright sync API for browser automation: `playwright.sync_api`.
- Chromium launched through Playwright.
- pandas for reading Excel rows as DataFrames.
- openpyxl for workbook creation, styling, status writing, and workbook inspection.
- Python `logging` for file and console logs.
- JSON configuration in `config/config.json`.

Dependencies declared in `requirements.txt`:

- `playwright>=1.44.0`
- `pandas>=2.2.0`
- `openpyxl>=3.1.2`

## 3. Complete Folder and File Structure

`src/main.py`

- Entry point and orchestration layer.
- Imports `setup_logger`, `ExcelReader`, `FormFiller`, `RETRY_DELAY`, and `ResultWriter`.
- Loads config, launches Chromium, waits for manual login, applies row filtering, retries rows, handles session expiry, and writes final summary.
- Depends directly on Playwright, config values, and the Excel reader/writer contract.

`src/form_filler.py`

- Core browser automation module.
- Contains **primitive helpers**:
  - `safe_fill()` - Wait, clear, fill for text inputs/textareas
  - `select_by_text()` - Select options from standard `<select>` with option polling
  - `fill_select2()` - Drive Select2 widget through rendered UI
  - `open_checkbox_panel()` - Open custom multiselect panel with fallbacks
  - `wait_for_options()` - Poll select until real options appear
  - `_normalize_multiselect_text()` - Normalize text for robust matching
- Contains **enhanced multiselect handler**:
  - `select_multiselect_checkboxes()` - Robust checkbox selection with error handling, debug logging, pipe/comma delimiter support, validation, and JavaScript fallback
  - `tick_checkboxes()` - Legacy backward-compatible version (deprecated in favor of new handler)
- Contains `FormFiller` class with:
  - `fill_form(record, row_index)` - Main entry point; fills all 25+ form fields in dependency order with comprehensive error handling
  - Field-specific helper methods for optional value retrieval and activity output type normalization
- Contains **Activity Output automation**:
  - `TrainingActivityOutputHandler` - Full support for Training/Capacity Building modal including validation, selection, filling, submission, and verification
  - `ActivityOutputError` - Custom exception for Activity Output failures
  - `OUTPUT_TYPE_ALIASES` - Mapping of user-friendly and portal names to internal types
  - `OUTPUT_RADIO_IDS` - Mapping of output types to radio element IDs
  - `SUPPORTED_ACTIVITY_OUTPUTS = {"training"}` - Currently active types (Asset, Community Service, Beneficiaries, VPRP intentionally not active)
- Depends on Playwright `Page`, portal DOM IDs, config flags, row dictionaries from `ExcelReader`, and comprehensive logging.

`src/excel_reader.py`

- Reads `data/input.xlsx`, sheet `eGram Activity Data`.
- Defines the canonical in-code column list: `FORM_COLUMNS` (now 45 columns including new conditional fields), `BOT_COLUMNS`, `ALL_COLUMNS`.
- Normalizes workbook headers through `normalize_header()`, lowercases, converts separators to underscores, and applies known aliases including:
  - `Activity Output` → `activity_output_type`
  - Training column variants → standardized names
  - `major_head`, `major_head_prompt` → `major_head_choice`
  - `minor_head`, `minor_head_prompt` → `minor_head_choice`
  - Operational remarks variants → `operation_remarks`
- Drops columns not in `ALL_COLUMNS`.
- Adds missing expected columns as empty strings (supports appending new fields to existing workbooks).
- Supplies pending rows (status != SUCCESS) and success counts.
- Does not read separate Activity Output sheets found in template workbooks; Training data lives inline on main activity rows.

`src/result_writer.py`

- Writes `data/output.xlsx`.
- Imports `FORM_COLUMNS`, `BOT_COLUMNS`, `ALL_COLUMNS`, and `SHEET_NAME` from `excel_reader.py`.
- Rebuilds a styled workbook from the input sheet and preserves previous bot status columns from existing output.
- Writes `Automation_Summary` at the end.
- It only writes the main `eGram Activity Data` sheet plus summary; it does not preserve `Valid Options Reference` or Activity Output sheets.

`src/logger.py`

- Central logger setup.
- Creates `logs/automation.log`.
- Adds DEBUG file logging and INFO console logging.
- Reuses existing handlers if called multiple times.

`config/config.json`

- Runtime configuration.
- Current values set `submit_form: true` and `submit_action: save_and_forward`.
- `headless` is present but overridden to `False` by `load_config()`.

`data/input.xlsx`

- Current live input workbook.
- Contains `eGram Activity Data` and `Valid Options Reference`.
- Current main sheet has 28 columns, including legacy `major_head` and `minor_head`; these are dropped by the current reader because they are not in `ALL_COLUMNS`.
- It does not currently contain the full Activity Output inline columns listed in `FORM_COLUMNS`.

`data/output.xlsx`

- Current generated output workbook.
- Contains `eGram Activity Data` and `Automation_Summary`.
- Current output sheet has 39 columns matching writer output, including Activity Output inline columns.

`data/input_ACTIVITY_OUTPUT_TEMPLATE.xlsx`

- Template workbook with separate sheets: `AO_Asset`, `AO_Training`, `AO_CommunityService`, `AO_Beneficiaries`, `AO_VRPPBeneficiaries`.
- The current code does not read or write these sheets.

`data/input_template_with_activity_output.xlsx`

- Template workbook with separate sheets using names such as `ActivityOutput_Asset`.
- The current code does not read or write these sheets.

`README.md`

- User-facing setup and workflow documentation.
- Partly outdated relative to current code and config.

`ACTIVITY_OUTPUT_ARCHITECTURE_ANALYSIS.md`

- Pre-implementation analysis recommending separate Activity Output sheets.
- It does not match the current implementation fully because the working tree now has inline Activity Output handling inside `FormFiller`.

`logs/automation.log`

- Historical runtime log.
- Shows main-form successes, earlier failures, and Activity Output warning patterns.

`screenshots/`

- Runtime screenshots and HTML snapshots from field failures and row attempts.
- Useful for debugging selectors and portal state.

`{data,logs,screenshots,config,src}`

- Empty odd-named directory found in the root. It appears to be an accidental literal directory creation and is not used by the code.

## 4. Application Execution Flow

1. `src/main.py::main()` parses `--config` and `--rows`.
2. `load_config()` reads JSON, forces `headless = False`, and injects `login_url` and `target_url`.
3. `run()` creates the logger through `setup_logger()`.
4. `ExcelReader(config["input_file"])` is constructed and `load()` reads sheet `eGram Activity Data`.
5. `ResultWriter(config["input_file"], config["output_file"])` is constructed.
6. Total, success, and pending counts are computed from the reader DataFrame.
7. Playwright starts Chromium with `headless=False` and `--start-maximized`.
8. A browser context is opened with no viewport limit.
9. The login page is loaded.
10. `_wait_for_login()` polls until the user reaches `addactivity.htm` and `#themeId` is visible.
11. The user presses ENTER.
12. A `FormFiller` is created with the current page and screenshot directory.
13. `reader.pending_rows()` yields every row whose status is not `SUCCESS`.
14. If `--rows` was provided, rows outside that set are skipped.
15. For each row, `run()` tries up to `retry_count` attempts.
16. Each attempt calls `_goto_form()`, checks session expiry, then calls `filler.fill_form(record, row_index)`.
17. On success, `writer.write_result(..., status="SUCCESS")` flushes `data/output.xlsx`.
18. On failure, a row-attempt screenshot is captured and the row is retried after a Playwright timeout wait.
19. After all retries fail, `writer.write_result(..., status="FAILED", error_message=...)` is called.
20. At the end, the browser is closed and `writer.write_summary()` adds `Automation_Summary`.

## 5. Browser and Session Workflow

- Browser starts in `main.run()` using `pw.chromium.launch(headless=False, args=["--start-maximized"])`.
- The code always uses a visible browser because login is manual.
- Login is not automated.
- The user must manually navigate to `https://egramswaraj.gov.in/addactivity.htm`.
- The authenticated session is preserved by reusing the same Playwright browser context and page.
- `_wait_for_login()` waits up to `MAX_SESSION_WAIT_MIN` minutes for the form page and `#themeId`.
- `_goto_form()` navigates to the form URL before each row.
- `_session_expired()` treats login/index/root URLs as expired sessions.
- `_handle_expiry()` prints instructions and waits for the user to log in again.
- Required page readiness is defined only as `#themeId` visible.

## 6. Excel Data Workflow

Live reader behavior:

- `ExcelReader.load()` reads `data/input.xlsx`, sheet `eGram Activity Data`.
- Headers are normalized by `normalize_header()`, which lowercases, converts separators to underscores, and applies known aliases.
- Unknown columns are dropped.
- Missing expected columns are added with empty string values.
- Rows with `status == SUCCESS` are skipped.
- `pending_rows()` yields `(row_index, row.to_dict())`.

Live writer behavior:

- `ResultWriter` reads the same input sheet.
- If `data/output.xlsx` exists, it preserves only bot columns (`status`, `processed_time`, `error_message`) from the previous output.
- It writes all `ALL_COLUMNS` to `eGram Activity Data`.
- It writes status after every row.
- It writes `Automation_Summary` only at the end.

Expected code columns in `FORM_COLUMNS`:

- Core main form fields: `theme`, `activity_name`, `focus_area`, `activity_type`, `activity_description`, `pdi_indicator`, `activity_for`, `targeted_populace`, `activity_nature`, `is_directly_funded_by_panchayat`, `estimated_completion_year`, `estimated_completion_month`, `estimated_completion_days`, `start_year`, `start_month`, `expected_beneficiary_general`, `expected_beneficiary_sc`, `expected_beneficiary_st`.
- Conditional cost and result fields: `indicative_unit_cost`, `total_indicative_cost`, `expected_results`.
- Flagship scheme and department fields: `flagship_scheme`, `select_supported_department`.
- Conditional shareable and dynamic head fields: `shareable`, `major_head_choice`, `minor_head_choice`.
- Operational fields: `operation_type`, `operation_remarks`.
- Main cost field: `estimated_total_cost`.
- Inline Activity Output fields: `activity_output_type`, `asset_type`, `asset_category`, `asset_sub_category`, `asset_total_units`, `asset_unit_cost`, `asset_coverage_area`, `training_category`, `training_organized_by`, `training_subject`, `training_total_trainees`, `training_total_duration_days`, `service_type`, `service_duration_days`, `service_total_expected_beneficiaries`, `beneficiary_action`, `vprp_select_all`.

Current workbook mismatch:

- `data/input.xlsx` still includes `major_head` and `minor_head`, but code drops them.
- `data/input.xlsx` lacks many inline Activity Output columns; the reader will add them in memory as blank.
- Activity Output template sheets exist, but the current workflow ignores them.
- For Training/Capacity Building, users should provide `activity_output_type` with the human-readable value `Training/Capacity Building` and fill `training_category`, `training_organized_by`, `training_subject`, `training_total_trainees`, and `training_total_duration_days` on the same row.

## 7. Form Automation Architecture

Standard dropdowns:

- Implemented by `select_by_text(page, selector, text)`.
- It waits for non-empty options using `wait_for_options()`.
- It exact-matches visible text first, then substring-matches.
- Used for theme, focus area, activity type, activity_for, activity_nature, start_year, start_month, and Activity Output modal selects.

Select2 dropdowns:

- Implemented by `fill_select2(page, hidden_select_id, search_text)`.
- It clicks the adjacent `.select2-container`, fills `.select2-search__field`, waits for `.select2-results__option`, and clicks the highlighted or first option.
- Used for `themeActivityNameID`.

Dependent dropdowns:

- Theme → Activity name → Focus Area → Activity Type / PDI are handled by sequential ordering and `_wait_idle()`.
- After Focus Area selection, explicit wait for PDI checkbox container to appear.
- After Start Year selection, explicit `wait_for_options()` call to refresh Start Month options.
- After Activity Nature selection, wait for Major/Minor Head fields to appear (if applicable).
- After Flagship Scheme selection, wait for Supported Department options to refresh.
- After Direct Funding radio selection, wait for cost section visibility to update.
- `wait_for_options()` polls child select options until usable values exist.

Text inputs and textareas:

- Implemented by `safe_fill()`, which waits for visibility, clears, then fills.
- Used for description, duration, beneficiaries, costs, result statements, and operational remarks.
- Also used for Activity Output modal fields.

Radio buttons:

- Direct funding radio: `yes/1/true` clicks `#activityForCostlessFlagNoId`; otherwise clicks `#activityForCostlessFlagYesId`.
- Shareable activity (if visible): `yes/true/1` clicks `input[name='shareable'][value='true']`; otherwise clicks `value='false'`.
- Activity Output Training radio selection is handled by `TrainingActivityOutputHandler._select_radio()`.

Checkboxes and multi-selects:

- Custom checkbox panels are opened by `open_checkbox_panel()`.
- It clicks a scoped `.selectBox`, waits briefly, may call JavaScript show functions, and may force visibility via CSS.
- **Enhanced multiselect handler `select_multiselect_checkboxes()`**: Replaces legacy `tick_checkboxes()` for improved reliability:
  - Supports `|` or `,` as delimiter for multiple indicator labels.
  - Normalizes whitespace in visible label text for robust matching.
  - Validates that all requested indicators exist before attempting selection.
  - Checks that checkboxes are enabled before clicking.
  - Provides detailed debug logging for troubleshooting checkbox issues.
  - Implements fallback to JavaScript click if normal click fails.
  - Verifies each checkbox's final selected state.
  - Raises meaningful error if requested indicator not found or selection fails.
- Legacy `tick_checkboxes()` retained for backward compatibility (comma-separated values, case-insensitive exact-or-substring matching).
- Used for PDI indicators (`#maDivId`, `#checkboxes`) with new enhanced handler and targeted populace (`#checkboxess`) with new enhanced handler.

Dynamic fields:

- Shareable (radio): Only processed if `#shareablediv` is visible.
- Major Head (dropdown `#submjrPrmptId`): Only processed if `#subMajorHeadDivId` is visible.
- Minor Head (dropdown `#minorPrmptId`): Only processed if `#subMinorHeadDivId` is visible.
- Indicative Unit Cost: Only processed if `#indicativeUnitCostDivId` is visible; triggers `calculateTotalIndicativeCost()`.
- Expected Result (textarea): Only processed if `#expctdResultDivId` is visible.
- Flagship Scheme (dropdown): Only processed if `#flagshipSchemeDivId` is visible; triggers Supported Department refresh.
- Supported Department (dropdown): Only processed if `#supportedMinistriesDivId` is visible.
- Estimated Total Cost: Only processed if `#totalCostDivId` is visible and field is enabled.
- Operational Type (dropdown): Only processed if `#oprationalActivityTypeDivId` is visible.
- Operational Remarks (textarea): Only processed if visible.
- Activity Output is handled if `#outputActvityTypeDivId` is visible.

Activity Output:

- Detection is implemented in `FormFiller._fill_activity_output()`.
- Supported aliases map human-readable `Training/Capacity Building` and internal value `102` to `training`.
- Current active support is restricted by `SUPPORTED_ACTIVITY_OUTPUTS = {"training"}`.
- `TrainingActivityOutputHandler` validates Training Excel data, clicks `#outputActvityAstId102`, waits for the Training modal fields, fills the modal, submits via `validationTraining()`, waits for closure, and verifies the radio remains checked.
- Asset, Community Service, Beneficiaries, and VPRP Beneficiaries are intentionally not implemented in the active workflow yet.

Buttons and Save/Forward:

- If `submit_form` is false, no submit button is clicked.
- If `submit_form` is true, `_check_page_errors()` runs first.
- `submit_action == "save_and_forward"` clicks `#saveAndForwardId`.
- Otherwise it clicks `#saveAsDraftId`.
- Application number and transaction ID are scraped loosely but not written to the output workbook.

## 8. Activity Processing Workflow

For one row, `FormFiller.fill_form(record, row_index)` processes fields in strict dependency order:

1. **Theme** → Select theme in `#themeId`, wait for Activity name Select2 to populate.
2. **Activity Name** → Select through Select2 on `#themeActivityNameID`, wait for Focus Area to populate.
3. **Focus Area** → Select in `#focusAreaId`, wait for PDI checkboxes to populate.
4. **Activity Type** → Select in `#activityTypeListId`.
5. **Activity Description** → Fill textarea `#activityDescId` (required).
6. **PDI / Advancement Indicator** → Open `#maDivId` panel, select multiple checkboxes from `#checkboxes` using pipe-delimited values. Uses enhanced `select_multiselect_checkboxes()` with error validation.
7. **Shareable Activity** (conditional) → If visible (`#shareablediv`), select radio button for Yes/No.
8. **Activity For** → Select in `#activityFor` (All/GEN/SC/ST).
9. **Targeted Populace** (conditional) → Open `.multiselect:has(#checkboxess)` panel, select multiple checkboxes from `#checkboxess` using pipe-delimited values.
10. **Activity Nature** → Select in `#workTypId`, wait for dynamic Major/Minor Head fields.
11. **Major Head Choice** (conditional) → If visible (`#subMajorHeadDivId`), select in `#submjrPrmptId`, wait for Minor Head to populate.
12. **Minor Head Choice** (conditional) → If visible (`#subMinorHeadDivId`), select in `#minorPrmptId`.
13. **Is Directly Funded by Panchayat** (radio) → Select radio (note: HTML labels are inverted), wait for cost section updates.
14. **Estimated Completion Time** → Fill three numeric inputs: Year (`#totDurYearId`), Month (`#totDurMonId`), Days (`#totDurDayId`).
15. **Start Year** → Select in `#startYearId`, explicitly wait for month options to refresh.
16. **Start Month** → Select in `#startMonthId`.
17. **Expected Beneficiaries** → Fill three numeric inputs: General (`#expctdMenGenId`), SC (`#expctdMenScId`), ST (`#expctdMenStId`). Trigger calculation.
18. **Indicative Unit Cost** (conditional) → If visible (`#indicativeUnitCostDivId`), fill `#indicativeUnitCostId`, trigger `calculateTotalIndicativeCost()`.
19. **Total Indicative Cost** (verify) → Check portal-calculated value in `#costlessTotalCostId`.
20. **Expected Result** (conditional) → If visible (`#expctdResultDivId`), fill textarea `#expctdResultId`.
21. **Flagship Scheme** (conditional) → If visible (`#flagshipSchemeDivId`), select in `#flagshipSchemeId`, wait for Supported Department to refresh.
22. **Supported Department** (conditional) → If visible (`#supportedMinistriesDivId`), select in `#supportedMinistriesId`.
23. **Estimated Total Cost** (conditional) → If visible and enabled (`#totalCostDivId`), fill `#totalCostId`.
24. **Operational Type** (conditional) → If visible (`#oprationalActivityTypeDivId`), select in `#operationTypeId`.
25. **Operational Remarks** (conditional) → If visible, fill textarea `#operationRemarksId`.
26. **Activity Output** → Process if `#outputActvityTypeDivId` is visible and `activity_output_type` has a value. For `Training/Capacity Building`, select the radio, fill the modal, submit, verify registration.
27. **Form Validation** → If `submit_form` is true and no errors exist, check for portal validation errors via `_check_page_errors()`.
28. **Form Submission** → If no errors, click Save (`#saveAsDraftId`) or Save and Forward (`#saveAndForwardId`) based on config.

Field failure behavior:

- All fields except mandatory dependencies (Theme, Activity Name, Focus Area, Activity Type) are wrapped in `attempt()`.
- A failed field appends error text, logs warning, and captures row-level screenshot/HTML.
- Processing continues through remaining fields, then raises `RuntimeError` listing all failures.

Conditional field behavior:

- Fields with container visibility checks (e.g., `#indicativeUnitCostDivId`) are skipped gracefully if hidden.
- Fields are only filled if their container is visible and (if applicable) enabled.
- No error is raised if a conditional field is empty in Excel and not visible in the form.

Field failure behavior:

- Most main-form fields are wrapped by `attempt()`.
- A failed field appends an error, logs a warning, and captures `row{row}_{field}_error.png/html`.
- Processing continues to later fields in the same row, then raises once all attempts finish.

Activity Output behavior:

- If `#outputActvityTypeDivId` is not present/visible, Activity Output is skipped.
- If `activity_output_type` is blank, Activity Output is skipped.
- If the type is unknown, unavailable, disabled, or not implemented, the row fails before submission.
- For `Training/Capacity Building`, required inline Excel columns are `activity_output_type`, `training_category`, `training_organized_by`, `training_subject`, `training_total_trainees`, and `training_total_duration_days`.
- Training Category and Organized By are selected by visible option text through `select_by_text()`.
- Subject of Training is required and must be 500 characters or fewer.
- Total Trainees is required, numeric only, no decimals, and maximum 4 digits.
- Total Duration is required, numeric only, no decimals, and maximum 3 digits.
- The Training Submit button is scoped to `#showTrainingDetailsPopup` when present, otherwise to the modal ancestor of `#trngCatCdId`, with a single global `validationTraining` button allowed only as a fallback.
- Save and Forward runs only after Training modal submission and Activity Output registration succeed.

## 9. Error Handling and Recovery

Exceptions:

- Field errors in `fill_form()` are accumulated and raised as a single `RuntimeError`.
- Portal option mismatches usually raise `ValueError` from `select_by_text()`.
- AJAX option timeouts raise `TimeoutError` from `wait_for_options()`.
- Playwright timeouts are caught selectively in `_wait_idle()` and elsewhere.

Retry logic:

- `main.run()` retries each row up to `retry_count`.
- `RETRY_DELAY` is 2 seconds.
- `main.run()` uses `page.wait_for_timeout(RETRY_DELAY * 1000)` between row attempts.
- If session expiry is detected after a failure, `_handle_expiry()` runs and the loop continues.

Timeout handling:

- Page navigation uses `page_timeout_ms`.
- Default selector timeout is set on the Playwright page from `selector_timeout_ms`.
- `form_filler.py` also has module constants such as `SELECTOR_TIMEOUT`, `OPTIONS_TIMEOUT`, and `NETWORK_IDLE_MS`.
- `options_timeout_ms` from README is not currently read by `form_filler.py`; the module constant is used.

Failure handling:

- Failed fields produce screenshots and HTML snapshots through `_snapshot()`.
- Failed row attempts produce screenshots named `row_{row_index}_attempt{attempt}_error.png`.
- Processing continues to the next Excel row after all retries fail.
- Failed rows are written to output with `status=FAILED`, `processed_time`, and truncated `error_message`.

Logging:

- File log receives DEBUG+ messages.
- Console receives INFO+ messages.
- Activity Output exceptions are warnings but do not fail the row.

Status/result tracking:

- Only `status`, `processed_time`, and `error_message` are written.
- `application_number` and `transaction_id` parameters exist in `ResultWriter.write_result()` but are reserved and not written.

## 10. Existing Documentation

`README.md`

- Purpose: setup, run instructions, workflow, config, field mapping, output, resume.
- Matches current code for manual login, visible browser, row filtering, status output, and basic main-form chain.
- Outdated/conflicting points:
  - Says bot v3; code docstrings say v5.
  - Says default `submit_form` is false; current `config/config.json` sets it to true.
  - Lists `application_number` and `transaction_id` as output columns; current writer does not include them.
  - Lists only the older main-form fields and omits current Activity Output inline columns.
  - Mentions `options_timeout_ms`; current `form_filler.py` uses `OPTIONS_TIMEOUT` constant.

`ACTIVITY_OUTPUT_ARCHITECTURE_ANALYSIS.md`

- Purpose: design analysis for Activity Output automation.
- It accurately identifies that Activity Output forms are dynamic and recommends separate sheets.
- It is now partly outdated because current working code has inline Activity Output implementation in `FormFiller`.
- The recommended separate-sheet architecture is not implemented in the live reader/writer.
- It references pseudo-code classes that do not exist.

`PROJECT_CODEBASE_ANALYSIS.md`

- This document. It should be updated whenever architecture, execution flow, file responsibilities, or important behavior change.

## 11. Current Implemented Features

- Manual browser launch and manual login wait.
- Manual navigation detection for `addactivity.htm`.
- ENTER-to-start gate.
- Row filtering through `--rows`.
- Main sheet Excel loading and header normalization with aliases for variant column names.
- Pending row iteration and resume based on `SUCCESS` status.
- **Complete main activity form filling** for all 25 user-input fields:
  - Theme, Activity Name (Select2), Focus Area, Activity Type
  - Activity Description, Panchayat Advancement Index Indicators (multiselect with pipe-delimited support)
  - Shareable Activity (conditional radio), Activity For, Targeted Populace (multiselect with pipe-delimited support)
  - Activity Nature, Major Head Choice (conditional), Minor Head Choice (conditional)
  - Direct Funding Radio, Estimated Completion Time (year/month/days), Start Year/Month
  - Expected Beneficiaries (general/SC/ST), Indicative Unit Cost (conditional), Expected Result (conditional)
  - Flagship Scheme (conditional), Supported Department (conditional), Estimated Total Cost (conditional)
  - Operational Type (conditional), Operational Remarks (conditional)
- Select2 activity name handling with search and selection.
- **Enhanced multiselect checkbox handling**: `select_multiselect_checkboxes()` with pipe-or-comma delimiters, text normalization, validation, error reporting, and debug logging.
- Legacy `tick_checkboxes()` retained for backward compatibility.
- **Explicit dependency waits** between cascading dropdowns (Theme→Activity→Focus→Type, Start Year→Month, Activity Nature→Major/Minor, Flagship→Department).
- Row-level retries with configurable retry count.
- Session-expiry detection, pause, and resume.
- Per-field screenshots and HTML snapshots on failure.
- Per-row attempt screenshots.
- Styled output workbook with all form and bot columns, formatted header, color-coded status rows.
- Automation summary sheet with row counts and statistics.
- Save as Draft and Save and Forward button selection via config.
- Full inline Activity Output handling for Training/Capacity Building with modal validation and registration verification.
- Comprehensive logging (DEBUG to file, INFO to console) with field-specific diagnostic messages.

## 12. Partially Implemented Features

- Activity Output automation: Training/Capacity Building is fully supported; other Activity Output forms (Asset, Community Service, Beneficiaries, VPRP) are intentionally not active yet.
- Separate Activity Output sheets: templates exist, but code does not read/write them. Training data currently lives inline on each main activity row.
- Asset/service/beneficiary/VPRP Activity Output handling: not supported in the active Activity Output workflow yet.
- Application number / transaction ID capture: values are attempted but not persisted to output.
- Configurable option timeout/debug mode: config keys/documentation exist, but not all are fully wired into `form_filler.py`.

## 13. Fixed Issues (July 7, 2026 Implementation)

**Main Form Completion - All Fields Now Supported:**

- ✅ Panchayat Advancement Index Indicator checkboxes: Enhanced handler `select_multiselect_checkboxes()` provides robust selection with full error handling, debug logging, and support for pipe-delimited multiple indicators.
- ✅ Shareable Activity (radio buttons): Now handled with visibility check.
- ✅ Major Head / Minor Head (dynamic dropdowns): Now filled when visible after Activity Nature selection.
- ✅ Indicative Unit Cost: Now filled when visible; Total Indicative Cost verified after calculation.
- ✅ Expected Result (textarea): Now filled when visible.
- ✅ Flagship Scheme + Supported Department: Now supported with dependency wait between selections.
- ✅ Operation Type + Operational Remarks: Now handled as conditional fields.
- ✅ Start Year → Start Month: Explicit `wait_for_options()` call added after year selection.
- ✅ Focus Area → PDI Data: Explicit wait for checkbox container after focus area selection.
- ✅ Activity Nature → Major Head: Explicit wait for major head section after nature selection.
- ✅ Direct Funding Radio → Cost Section: Explicit wait for visibility updates.
- ✅ Beneficiary fields trigger calculation after input.
- ✅ Excel schema expanded: Added 8 new columns for conditional fields.

**Root Cause of Checkbox Issue (PDI Indicators):**

The original checkbox failure was caused by:
1. Case-sensitive substring matching against label text
2. No validation that checkbox was enabled before clicking
3. No error reporting if requested indicator wasn't found
4. Lack of debug logging to diagnose which indicators were available vs. requested
5. No retry with JavaScript fallback if normal click failed

The new `select_multiselect_checkboxes()` function addresses all root causes.

## 14. Known Remaining Risks and Limitations

Automation fragility:

- Many selectors remain hardcoded portal IDs; if the portal changes IDs, automation breaks.
- `_session_expired()` is based on URL patterns; more sophisticated detection could prevent false positives.

Data/workflow risks:

- `ExcelReader` drops all unknown columns; users cannot add custom columns beyond `ALL_COLUMNS`.
- `ResultWriter` overlays previous output status by row position, not a stable ID.
- Activity Output template sheet names are inconsistent between template workbooks (`AO_Asset` vs `ActivityOutput_Asset`).
- Application and transaction IDs are not written to output despite being captured.

Operational risks:

- Runtime artifacts are tracked in git despite `.gitignore`.
- The root contains an unused literal directory named `{data,logs,screenshots,config,src}`.

## 15. Dead, Legacy, and Unused Code

- `tick_checkboxes()`: Retained for backward compatibility but deprecated in favor of `select_multiselect_checkboxes()`.
- `asset_coverage_area`: Listed and width-configured but Asset is not fully implemented in Activity Output workflow.
- `vprp_select_all`: Listed in `FORM_COLUMNS` but VPRP Beneficiaries not implemented yet.
- `application_number` and `transaction_id` arguments in `ResultWriter.write_result()`: reserved but not written.
- `debug_mode` config: not used by current code.
- `data/input_ACTIVITY_OUTPUT_TEMPLATE.xlsx` and `data/input_template_with_activity_output.xlsx`: useful as templates but unused by current runtime.

## 16. Recommended Development Priorities

P0 - Critical blockers

- Decide whether the Activity Output architecture will remain inline or move to separate sheets; maintain consistency across reader/writer/filler.
- Align `submit_form` default/documentation with safe operational expectations (currently defaults to true/save_and_forward).

P1 - Important reliability issues

- Extend Activity Output support to Asset, Community Service, Beneficiaries, VPRP if needed.
- Preserve existing workbook sheets in output or document that output intentionally drops them.
- Add stable identifiers for row/result tracking instead of row-position-only status preservation.
- Persist application number and transaction ID if submission workflow is completed.

P2 - Architecture and maintainability improvements

- Split Activity Output handlers into separate classes/modules if workflow expands.
- Add a schema validation layer for Excel columns before browser launch.
- Centralize timeout configuration so config values drive all waits.
- Add structured result objects from `FormFiller.fill_form()` instead of bare dicts.
- Clean tracked runtime artifacts from git in a controlled commit.

P3 - Optional enhancements

- Add a dry-run validation mode for workbook schema.
- Add per-field failure report generation by row.
- Add more precise session-expiry detection based on page content.
- Add unit tests for Excel normalization and writer behavior.

## 17. Important Rules for Future Development

- Always read this document first, then verify the relevant source files before editing.
- Treat source code as the final source of truth.
- Do not assume README or architecture documents remain current.
- Preserve manual login unless the user explicitly requests login automation.
- Preserve visible browser mode unless a safe authenticated-session strategy is designed.
- Keep row processing resumable through output status.
- Do not silently swallow critical automation failures, especially submission and requested Activity Output failures.
- Avoid adding duplicate Excel columns or duplicate form handlers.
- Support pipe-delimited (`|`) format for multiselect fields; maintain backward compatibility with comma if existing deployments use it.
- Prefer portal IDs and stable selectors; use XPath only when needed.
- Replace fixed waits with explicit, condition-based waits when changing related code.
- Keep changes scoped to the requested workflow.
- Update this document when architecture, execution flow, file responsibilities, Excel schema, or important behavior changes.
