from __future__ import annotations

import pandas as pd
from pathlib import Path
from typing import Generator

from activity_output_registry import get_output_definition


ACTIVITY_SHEET_NAME = "Activities"
LEGACY_ACTIVITY_SHEET_NAME = "eGram Activity Data"

# Main form columns expected from Activities sheet
FORM_COLUMNS = [
    "activity_key",
    "theme",
    "activity_name",
    "focus_area",
    "activity_type",
    "activity_description",
    "vprp_plan",
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
    "indicative_unit_cost",
    "total_indicative_cost",
    "expected_results",
    "flagship_scheme",
    "select_supported_department",
    "estimated_total_cost",
    "shareable",
    "major_head_choice",
    "minor_head_choice",
    "operation_type",
    "operation_remarks",
    "activity_output_type",
    "final_action",
]

# Columns appended by the bot after processing
BOT_COLUMNS = ["status", "processed_time", "error_message"]

ALL_COLUMNS = FORM_COLUMNS + BOT_COLUMNS
STATUS_COL = "status"

HEADER_ALIASES = {
    "activity_output": "activity_output_type",
    "activity_key": "activity_key",
    "activity_key_": "activity_key",
    "activity": "activity_name",
    "panchayat_advancement_index_indicator": "pdi_indicator",
    "supported_department": "select_supported_department",
    "final_action": "final_action",
    "finalaction": "final_action",
    "training_category": "training_category",
    "training_organized_by": "organized_by",
    "training_organised_by": "organized_by",
    "organized_by": "organized_by",
    "organised_by": "organized_by",
    "training_subject": "subject_of_training",
    "subject_of_training": "subject_of_training",
    "training_total_trainees": "total_trainees",
    "total_trainees": "total_trainees",
    "training_total_duration_days": "total_duration",
    "total_duration": "total_duration",
    "total_duration_days": "total_duration",
    "training_village": "village",
    "village": "village",
    "training_amount": "amount",
    "amount": "amount",
    "asset_type": "asset_type",
    "asset_category": "asset_category",
    "asset_sub_category": "asset_sub_category",
    "asset_total_units": "total_units",
    "total_units": "total_units",
    "asset_unit_cost": "unit_cost",
    "unit_cost": "unit_cost",
    "asset_coverage_area": "coverage_area",
    "coverage_area": "coverage_area",
    "census_village": "census_village",
    "units_per_village": "units_per_village",
    "major_head": "major_head_choice",
    "major_head_prompt": "major_head_choice",
    "minor_head": "minor_head_choice",
    "minor_head_prompt": "minor_head_choice",
    "operation_remarks": "operation_remarks",
    "operational_remarks": "operation_remarks",
}

TRAINING_COLUMNS = [
    "activity_key",
    "training_category",
    "organized_by",
    "subject_of_training",
    "village",
    "amount",
    "total_trainees",
    "total_duration",
]

ASSET_COLUMNS = [
    "activity_key",
    "asset_type",
    "asset_category",
    "asset_sub_category",
    "total_units",
    "unit_cost",
    "coverage_area",
    "census_village",
    "units_per_village",
]

FINAL_ACTION_ALIASES = {
    "save": "Save",
    "save and forward": "Save and Forward",
    "save_and_forward": "Save and Forward",
    "save&forward": "Save and Forward",
}


def normalize_header(header: object) -> str:
    normalized = str(header).strip().lower()
    normalized = normalized.replace("/", " ")
    normalized = normalized.replace("-", " ")
    normalized = "_".join(normalized.split())
    return HEADER_ALIASES.get(normalized, normalized)


def normalize_activity_key(activity_key: str) -> str:
    return str(activity_key or "").strip().upper()


def normalize_final_action(value: str) -> str:
    normalized = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    return FINAL_ACTION_ALIASES.get(normalized, "")


class ExcelReader:
    """Workbook repository with Activities + output detail sheet indexing."""

    def __init__(self, filepath: str) -> None:
        self.filepath = Path(filepath)
        self._df: pd.DataFrame | None = None
        self.activity_sheet_name: str = ACTIVITY_SHEET_NAME
        self._output_indexes: dict[str, dict[str, dict]] = {}
        self._output_duplicates: dict[str, set[str]] = {}
        self._shared_output_records: dict[str, dict | None] = {}

    def load(self) -> None:
        with pd.ExcelFile(self.filepath) as workbook:
            if ACTIVITY_SHEET_NAME in workbook.sheet_names:
                self.activity_sheet_name = ACTIVITY_SHEET_NAME
            elif LEGACY_ACTIVITY_SHEET_NAME in workbook.sheet_names:
                self.activity_sheet_name = LEGACY_ACTIVITY_SHEET_NAME
            else:
                raise ValueError(
                    f"Activities sheet not found. Expected '{ACTIVITY_SHEET_NAME}'"
                )

            df = pd.read_excel(
                self.filepath,
                sheet_name=self.activity_sheet_name,
                dtype=str,
                keep_default_na=False,
            )
            df.columns = [normalize_header(c) for c in df.columns]
            df = df[[c for c in df.columns if c in ALL_COLUMNS]]
            for col in ALL_COLUMNS:
                if col not in df.columns:
                    df[col] = ""
            self._df = df[ALL_COLUMNS]

            self._validate_activity_keys_unique()
            self._build_output_indexes(workbook)

    @property
    def df(self) -> pd.DataFrame:
        if self._df is None:
            self.load()
        return self._df  # type: ignore[return-value]

    def get_activity_rows(self) -> list[dict]:
        rows: list[dict] = []
        for idx, row in self.df.iterrows():
            record = row.to_dict()  # type: ignore[arg-type]
            record["activity_key"] = normalize_activity_key(record.get("activity_key", ""))
            record["_row_index"] = int(idx)
            rows.append(record)
        return rows

    def pending_rows(self) -> Generator[tuple[int, dict], None, None]:
        for row in self.get_activity_rows():
            idx = int(row["_row_index"])
            if str(row.get(STATUS_COL, "")).strip().upper() == "SUCCESS":
                continue
            row.pop("_row_index", None)
            yield idx, row

    def total_rows(self) -> int:
        return len(self.df)

    def success_count(self) -> int:
        return (
            self.df.get(STATUS_COL, pd.Series(dtype=str))
            .str.strip().str.upper().eq("SUCCESS").sum()
        )

    def get_output_record(self, output_type: str, activity_key: str) -> dict:
        definition = get_output_definition(output_type)
        if not definition:
            raise ValueError(f"Unsupported Activity Output: {output_type}")

        key = normalize_activity_key(activity_key)
        duplicates = self._output_duplicates.get(definition.canonical_name, set())
        if key in duplicates:
            raise ValueError(
                f"Duplicate {definition.sheet_name} output data for Activity_Key {key}"
            )

        output_map = self._output_indexes.get(definition.canonical_name, {})
        if key not in output_map:
            shared_record = self._shared_output_records.get(definition.canonical_name)
            if shared_record is not None:
                return shared_record
            raise ValueError(
                f"{definition.sheet_name} output data not found for Activity_Key {key}"
            )
        return output_map[key]

    def _validate_activity_keys_unique(self) -> None:
        keys = self.df["activity_key"].map(normalize_activity_key)
        if (keys == "").any():
            missing_indices = [str(i) for i, key in enumerate(keys) if key == ""]
            raise ValueError(
                f"Missing Activity_Key in Activities sheet rows: {', '.join(missing_indices)}"
            )

        duplicates = keys[keys.duplicated(keep=False)].unique()
        if len(duplicates):
            raise ValueError(
                f"Duplicate Activity_Key in Activities sheet: {', '.join(sorted(duplicates))}"
            )

        self._df["activity_key"] = keys

    def _build_output_indexes(self, workbook: pd.ExcelFile) -> None:
        self._output_indexes = {}
        self._output_duplicates = {}
        self._shared_output_records = {}

        for canonical in ["training", "community_service", "beneficiaries", "asset"]:
            definition = get_output_definition(canonical)
            if not definition:
                continue

            if definition.sheet_name not in workbook.sheet_names:
                self._output_indexes[canonical] = {}
                self._output_duplicates[canonical] = set()
                self._shared_output_records[canonical] = None
                continue

            raw = pd.read_excel(
                self.filepath,
                sheet_name=definition.sheet_name,
                dtype=str,
                keep_default_na=False,
            )
            raw.columns = [normalize_header(c) for c in raw.columns]

            if canonical == "training":
                for col in TRAINING_COLUMNS:
                    if col not in raw.columns:
                        raw[col] = ""
                use_df = raw[TRAINING_COLUMNS]
            elif canonical == "asset":
                for col in ASSET_COLUMNS:
                    if col not in raw.columns:
                        raw[col] = ""
                use_df = raw[ASSET_COLUMNS]
            else:
                if "activity_key" not in raw.columns:
                    raw["activity_key"] = ""
                use_df = raw[["activity_key"]]

            detail_map: dict[str, dict] = {}
            duplicate_keys: set[str] = set()
            shared_candidates: list[dict] = []

            for _, row in use_df.iterrows():
                record = row.to_dict()  # type: ignore[arg-type]
                if canonical == "training" and self._has_training_payload(record):
                    shared_candidates.append(record.copy())
                if canonical == "asset" and self._has_asset_payload(record):
                    shared_candidates.append(record.copy())

                key = normalize_activity_key(record.get("activity_key", ""))
                if not key:
                    continue
                record["activity_key"] = key
                if key in detail_map:
                    duplicate_keys.add(key)
                else:
                    detail_map[key] = record

            self._output_indexes[canonical] = detail_map
            self._output_duplicates[canonical] = duplicate_keys
            if canonical == "training" and len(shared_candidates) == 1:
                self._shared_output_records[canonical] = shared_candidates[0]
            else:
                self._shared_output_records[canonical] = None

    @staticmethod
    def _has_training_payload(record: dict) -> bool:
        return any(
            str(record.get(col, "") or "").strip()
            for col in TRAINING_COLUMNS
            if col != "activity_key"
        )

    @staticmethod
    def _has_asset_payload(record: dict) -> bool:
        return any(
            str(record.get(col, "") or "").strip()
            for col in ASSET_COLUMNS
            if col != "activity_key"
        )
