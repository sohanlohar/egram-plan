from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from activity_output_handlers import ActivityOutputError, ActivityOutputHandlerRegistry, TrainingOutputHandler
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
            "total_trainees": "abcd",
            "total_duration": "5",
        }
        with self.assertRaisesRegex(ActivityOutputError, "Total Trainees must be numeric"):
            TrainingOutputHandler.validate_detail_record(detail, "ACT-0001")

    def test_training_validation_required(self) -> None:
        detail = {
            "activity_key": "ACT-0001",
            "training_category": "",
            "organized_by": "Department",
            "subject_of_training": "Watershed",
            "total_trainees": "10",
            "total_duration": "5",
        }
        with self.assertRaisesRegex(ActivityOutputError, "Training Category is required"):
            TrainingOutputHandler.validate_detail_record(detail, "ACT-0001")


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


if __name__ == "__main__":
    unittest.main()
