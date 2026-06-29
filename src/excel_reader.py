from __future__ import annotations

import pandas as pd
from pathlib import Path
from typing import Generator


# Exact columns that exist in the form — no extras
FORM_COLUMNS = [
    "theme",
    "activity_name",
    "focus_area",
    "activity_type",
    "activity_description",
    "pdi_indicator",
    "activity_for",
    "targeted_populace",
    "activity_nature",
    "is_directly_funded_by_panchayat",
    "estimated_completion_year",
    "estimated_completion_month",
    "estimated_completion_days",
    "start_year",
    "start_month",
    "expected_beneficiary_general",
    "expected_beneficiary_sc",
    "expected_beneficiary_st",
    "estimated_total_cost",
]

# Columns appended by the bot after processing
BOT_COLUMNS = ["status", "processed_time", "error_message"]

ALL_COLUMNS = FORM_COLUMNS + BOT_COLUMNS

SHEET_NAME = "eGram Activity Data"
STATUS_COL = "status"


class ExcelReader:
    """Reads pending rows from input Excel. Skips rows already marked SUCCESS."""

    def __init__(self, filepath: str) -> None:
        self.filepath = Path(filepath)
        self._df: pd.DataFrame | None = None

    def load(self) -> None:
        df = pd.read_excel(
            self.filepath,
            sheet_name=SHEET_NAME,
            dtype=str,
            keep_default_na=False,
        )
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        # Drop any columns that are not in ALL_COLUMNS (removes old junk cols)
        df = df[[c for c in df.columns if c in ALL_COLUMNS]]

        # Ensure every expected column exists (adds missing bot cols as empty)
        for col in ALL_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        # Enforce column order
        self._df = df[ALL_COLUMNS]

    @property
    def df(self) -> pd.DataFrame:
        if self._df is None:
            self.load()
        return self._df  # type: ignore[return-value]

    def pending_rows(self) -> Generator[tuple[int, dict], None, None]:
        for idx, row in self.df.iterrows():
            if str(row.get(STATUS_COL, "")).strip().upper() == "SUCCESS":
                continue
            yield int(idx), row.to_dict()  # type: ignore[arg-type]

    def total_rows(self) -> int:
        return len(self.df)

    def success_count(self) -> int:
        return (
            self.df.get(STATUS_COL, pd.Series(dtype=str))
            .str.strip().str.upper().eq("SUCCESS").sum()
        )
