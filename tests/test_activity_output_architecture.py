from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import form_filler
from activity_output_handlers import (
    ActivityOutputError,
    ActivityOutputHandlerRegistry,
    AssetOutputHandler,
    TrainingOutputHandler,
)
from activity_output_registry import normalize_output_type
from excel_reader import ExcelReader, normalize_activity_key, normalize_final_action


class ActivityArchitectureTests(unittest.TestCase):
    def test_activity_key_normalization(self) -> None:
        self.assertEqual(normalize_activity_key(" act-0001 "), "ACT-0001")

    def test_output_type_normalization(self) -> None:
        self.assertEqual(normalize_output_type("Training/Capacity Building"), "training")
        self.assertEqual(normalize_output_type("community service"), "community_service")

    def test_final_action_normalization(self) -> None:
        self.assertEqual(normalize_final_action("save"), "Save")
        self.assertEqual(normalize_final_action(" Save and Forward "), "Save and Forward")
        self.assertEqual(normalize_final_action(""), "")

    def test_registry_lookup_training(self) -> None:
        registry = ActivityOutputHandlerRegistry(page=None)  # type: ignore[arg-type]
        handler = registry.resolve("Training/Capacity Building")
        self.assertEqual(handler.__class__.__name__, "TrainingOutputHandler")

    def test_registry_unsupported_output(self) -> None:
        registry = ActivityOutputHandlerRegistry(page=None)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ActivityOutputError, "Unsupported Activity Output"):
            registry.resolve("Unknown Output")

    def test_training_validation_numeric(self) -> None:
        detail = {
            "activity_key": "ACT-0001",
            "training_category": "Skill",
            "organized_by": "Department",
            "subject_of_training": "Watershed",
            "village": "Bapod",
            "amount": "2500",
            "total_trainees": "abcd",
            "total_duration": "5",
        }
        with self.assertRaisesRegex(ActivityOutputError, "Total Trainees must be numeric"):
            TrainingOutputHandler.validate_detail_record(detail, "ACT-0001")

    def test_training_validation_allows_blank_village_and_rejects_invalid_amount(self) -> None:
        detail = {
            "activity_key": "ACT-0001",
            "training_category": "Skill",
            "organized_by": "Department",
            "subject_of_training": "Watershed",
            "village": "",
            "amount": "abc",
            "total_trainees": "10",
            "total_duration": "5",
        }
        with self.assertRaisesRegex(ActivityOutputError, "Amount must be numeric"):
            TrainingOutputHandler.validate_detail_record(detail, "ACT-0001")

    def test_asset_validation_numeric(self) -> None:
        detail = {
            "activity_key": "ACT-0002",
            "asset_type": "Immovable",
            "asset_category": "Water Sources & Structures",
            "asset_sub_category": "Water Tank",
            "total_units": "0",
            "unit_cost": "1200",
            "coverage_area": "Area",
            "census_village": "Kundwasa",
            "units_per_village": "5",
        }
        with self.assertRaisesRegex(ActivityOutputError, "Total Units must be greater than zero"):
            AssetOutputHandler.validate_detail_record(detail, "ACT-0002")

    def test_excel_training_record_maps_village_and_amount_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "input.xlsx"
            activities_df = pd.DataFrame([
                {
                    "Activity_Key": "ACT-2001",
                    "Theme": "A",
                    "Activity Output": "Training/Capacity Building",
                    "Final Action": "Save",
                }
            ])
            training_df = pd.DataFrame([
                {
                    "Activity_Key": "ACT-2001",
                    "Training Category": "Skill",
                    "Organized By": "Department",
                    "Subject of Training": "Watershed",
                    "Village": "Bapod",
                    "Amount": "2500",
                    "Total Trainees": "10",
                    "Total Duration": "5",
                }
            ])
            with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
                activities_df.to_excel(writer, sheet_name="Activities", index=False)
                training_df.to_excel(writer, sheet_name="Training", index=False)

            reader = ExcelReader(str(file_path))
            reader.load()
            detail = reader.get_output_record("Training/Capacity Building", "ACT-2001")
            self.assertEqual(detail["village"], "Bapod")
            self.assertEqual(detail["amount"], "2500")

    def test_excel_asset_record_maps_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "input.xlsx"
            activities_df = pd.DataFrame([
                {
                    "Activity_Key": "ACT-2002",
                    "Theme": "A",
                    "Activity Output": "Asset",
                    "Final Action": "Save",
                }
            ])
            asset_df = pd.DataFrame([
                {
                    "Activity_Key": "ACT-2002",
                    "Asset Type": "Immovable",
                    "Asset Category": "Water Sources & Structures",
                    "Asset Sub Category": "Water Tank",
                    "Total Units": "1",
                    "Unit Cost": "1200",
                    "Coverage Area": "Area",
                    "Census Village": "Kundwasa",
                    "Units Per Village": "1",
                }
            ])
            with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
                activities_df.to_excel(writer, sheet_name="Activities", index=False)
                asset_df.to_excel(writer, sheet_name="Asset", index=False)

            reader = ExcelReader(str(file_path))
            reader.load()
            detail = reader.get_output_record("Asset", "ACT-2002")
            self.assertEqual(detail["asset_type"], "Immovable")
            self.assertEqual(detail["asset_category"], "Water Sources & Structures")
            self.assertEqual(detail["census_village"], "Kundwasa")
            self.assertEqual(detail["units_per_village"], "1")


class WorkbookIndexTests(unittest.TestCase):
    def _create_workbook(self, path: Path, activities: list[dict], training: list[dict]) -> None:
        activities_df = pd.DataFrame(activities)
        training_df = pd.DataFrame(training)
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            activities_df.to_excel(writer, sheet_name="Activities", index=False)
            training_df.to_excel(writer, sheet_name="Training", index=False)

    def test_duplicate_activity_key_detection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "input.xlsx"
            self._create_workbook(
                file_path,
                activities=[
                    {"Activity_Key": "ACT-0001", "Theme": "A", "Final Action": "Save"},
                    {"Activity_Key": "ACT-0001", "Theme": "B", "Final Action": "Save"},
                ],
                training=[],
            )

            reader = ExcelReader(str(file_path))
            with self.assertRaisesRegex(ValueError, "Duplicate Activity_Key"):
                reader.load()

    def test_missing_activity_key_detection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "input.xlsx"
            self._create_workbook(
                file_path,
                activities=[
                    {"Activity_Key": "", "Theme": "A", "Final Action": "Save"},
                ],
                training=[],
            )

            reader = ExcelReader(str(file_path))
            with self.assertRaisesRegex(ValueError, "Missing Activity_Key"):
                reader.load()

    def test_missing_training_detail_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "input.xlsx"
            self._create_workbook(
                file_path,
                activities=[
                    {
                        "Activity_Key": "ACT-0001",
                        "Theme": "A",
                        "Activity Output": "Training/Capacity Building",
                        "Final Action": "Save",
                    },
                ],
                training=[],
            )

            reader = ExcelReader(str(file_path))
            reader.load()
            with self.assertRaisesRegex(ValueError, "Training output data not found"):
                reader.get_output_record("Training/Capacity Building", "ACT-0001")

    def test_duplicate_training_detail_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "input.xlsx"
            self._create_workbook(
                file_path,
                activities=[
                    {
                        "Activity_Key": "ACT-0001",
                        "Theme": "A",
                        "Activity Output": "Training/Capacity Building",
                        "Final Action": "Save",
                    },
                ],
                training=[
                    {
                        "Activity_Key": "ACT-0001",
                        "Training Category": "Skill",
                        "Organized By": "Dept",
                        "Subject of Training": "Topic",
                        "Total Trainees": "12",
                        "Total Duration": "2",
                    },
                    {
                        "Activity_Key": "ACT-0001",
                        "Training Category": "Skill",
                        "Organized By": "Dept",
                        "Subject of Training": "Topic",
                        "Total Trainees": "14",
                        "Total Duration": "3",
                    },
                ],
            )

            reader = ExcelReader(str(file_path))
            reader.load()
            with self.assertRaisesRegex(ValueError, "Duplicate Training output data"):
                reader.get_output_record("Training/Capacity Building", "ACT-0001")

    def test_shared_training_single_row_reused_for_all_activity_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "input.xlsx"
            self._create_workbook(
                file_path,
                activities=[
                    {
                        "Activity_Key": "ACT-0089",
                        "Theme": "A",
                        "Activity Output": "Training/Capacity Building",
                        "Final Action": "Save",
                    },
                ],
                training=[
                    {
                        "Activity_Key": "ACT-0001",
                        "Training Category": "Skill",
                        "Organized By": "Dept",
                        "Subject of Training": "Topic",
                        "Total Trainees": "12",
                        "Total Duration": "2",
                    },
                ],
            )

            reader = ExcelReader(str(file_path))
            reader.load()
            detail = reader.get_output_record("Training/Capacity Building", "ACT-0089")
            self.assertEqual(detail["training_category"], "Skill")
            self.assertEqual(detail["organized_by"], "Dept")

    def test_shared_training_single_row_can_have_blank_activity_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "input.xlsx"
            self._create_workbook(
                file_path,
                activities=[
                    {
                        "Activity_Key": "ACT-0090",
                        "Theme": "A",
                        "Activity Output": "Training/Capacity Building",
                        "Final Action": "Save",
                    },
                ],
                training=[
                    {
                        "Activity_Key": "",
                        "Training Category": "Awareness",
                        "Organized By": "Govt",
                        "Subject of Training": "ICDS",
                        "Total Trainees": "50",
                        "Total Duration": "5",
                    },
                ],
            )

            reader = ExcelReader(str(file_path))
            reader.load()
            detail = reader.get_output_record("Training/Capacity Building", "ACT-0090")
            self.assertEqual(detail["training_category"], "Awareness")
            self.assertEqual(detail["subject_of_training"], "ICDS")


class PdiSelectionRuleTests(unittest.TestCase):
    @patch("form_filler.random.sample")
    @patch("form_filler.random.choice", return_value=5)
    def test_five_plus_options_selects_four_or_five_unique(self, mock_choice, mock_sample) -> None:
        mock_sample.return_value = ["A", "B", "C", "D", "E"]

        result = form_filler._choose_random_pdi_labels(["A", "B", "C", "D", "E", "F"])

        self.assertEqual(result, ["A", "B", "C", "D", "E"])
        mock_choice.assert_called_once_with([4, 5])
        mock_sample.assert_called_once_with(["A", "B", "C", "D", "E", "F"], k=5)

    @patch("form_filler.random.sample")
    @patch("form_filler.random.randint", return_value=2)
    def test_three_or_four_options_selects_between_one_and_all(self, mock_randint, mock_sample) -> None:
        mock_sample.return_value = ["X", "Y"]

        result = form_filler._choose_random_pdi_labels(["X", "Y", "Z"])

        self.assertEqual(result, ["X", "Y"])
        mock_randint.assert_called_once_with(1, 3)
        mock_sample.assert_called_once_with(["X", "Y", "Z"], k=2)

    @patch("form_filler.random.sample")
    @patch("form_filler.random.randint", return_value=1)
    def test_one_or_two_options_selects_at_least_one(self, mock_randint, mock_sample) -> None:
        mock_sample.return_value = ["Only"]

        result = form_filler._choose_random_pdi_labels(["Only", "Second"])

        self.assertEqual(result, ["Only"])
        mock_randint.assert_called_once_with(1, 2)
        mock_sample.assert_called_once_with(["Only", "Second"], k=1)


if __name__ == "__main__":
    unittest.main()
