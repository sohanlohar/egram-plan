# eGramSwaraj Activity Planning Bot (v5)

Automation for the eGramSwaraj Add Activity workflow with:
- Manual login in visible Chromium
- Resume-by-status row processing
- Activity Output handler architecture (Training implemented)
- Row-level Save / Save and Forward via Excel Final Action

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## Run

```bash
python src/main.py
```

Process specific rows (0-indexed):

```bash
python src/main.py --rows 0,1,2
```

Custom config file:

```bash
python src/main.py --config config/config.json
```

## Runtime Flow

1. Browser opens at https://egramswaraj.gov.in.
2. You log in manually and navigate to https://egramswaraj.gov.in/addactivity.htm.
3. Bot waits for form readiness (#themeId), then ENTER gate starts processing.
4. Bot iterates pending rows from Excel (rows not marked SUCCESS).
5. For each row:
   - Fills main form in dependency order
   - Handles Activity Output for that row (if configured)
   - Runs Save or Save and Forward based on row Final Action
   - Writes SUCCESS/FAILED with timestamp and error to output workbook
6. If session expires, bot pauses for re-login and resumes.

## Configuration (config/config.json)

| Key | Description |
|---|---|
| retry_count | Retries per row on failure |
| page_timeout_ms | Page navigation timeout |
| selector_timeout_ms | Default Playwright selector timeout |
| input_file | Input workbook path |
| output_file | Output workbook path |
| log_file | Log file path |
| screenshots_dir | Screenshot/HTML snapshot directory |
| submit_form | true to submit, false for fill-only dry run |
| take_success_screenshots | Capture success screenshot per row |

Notes:
- Browser is always forced to visible mode in code for manual login.
- Save vs Save and Forward is controlled by each row Final Action value.

## Excel and Sheets

Supported activity sheet names (input):
- Activities (preferred)
- eGram Activity Data (legacy fallback)

Activity Output detail sheet mapping:
- Training -> implemented
- Community_Service -> mapped, not implemented
- Beneficiaries -> mapped, not implemented
- Asset -> mapped, not implemented

Training lookup is by Activity_Key through the Training sheet.

## Output

Generated workbook: data/output.xlsx

Status columns written per row:
- status
- processed_time
- error_message

Application/transaction IDs may be read from UI during submit but are not persisted to output.

## Testing

```bash
python -m unittest discover -s tests -v
```

Current architecture tests cover:
- Activity_Key validation and normalization
- Output type normalization
- Registry behavior
- Training record validation
- Output sheet lookup constraints
