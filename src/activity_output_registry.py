from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActivityOutputDefinition:
    canonical_name: str
    visible_name: str
    sheet_name: str
    radio_id: str
    modal_id: str
    handler_name: str
    implemented: bool


ACTIVITY_OUTPUT_DEFINITIONS: dict[str, ActivityOutputDefinition] = {
    "training": ActivityOutputDefinition(
        canonical_name="training",
        visible_name="Training/Capacity Building",
        sheet_name="Training",
        radio_id="outputActvityAstId102",
        modal_id="showTrainingDetailsPopup",
        handler_name="TrainingOutputHandler",
        implemented=True,
    ),
    "community_service": ActivityOutputDefinition(
        canonical_name="community_service",
        visible_name="Community Service",
        sheet_name="Community_Service",
        radio_id="outputActvityAstId103",
        modal_id="showCommunityServiceDetailsPopup",
        handler_name="CommunityServiceOutputHandler",
        implemented=False,
    ),
    "beneficiaries": ActivityOutputDefinition(
        canonical_name="beneficiaries",
        visible_name="Beneficiaries",
        sheet_name="Beneficiaries",
        radio_id="outputActvityAstId109",
        modal_id="showBeneficiariesDetailsPopup",
        handler_name="BeneficiariesOutputHandler",
        implemented=False,
    ),
    "asset": ActivityOutputDefinition(
        canonical_name="asset",
        visible_name="Asset",
        sheet_name="Asset",
        radio_id="outputActvityAstId101",
        modal_id="showAssetDetailsPopup",
        handler_name="AssetOutputHandler",
        implemented=True,
    ),
}


OUTPUT_TYPE_ALIASES = {
    "training": "training",
    "training/capacity building": "training",
    "capacity building": "training",
    "102": "training",
    "community service": "community_service",
    "community_service": "community_service",
    "service": "community_service",
    "103": "community_service",
    "beneficiary": "beneficiaries",
    "beneficiaries": "beneficiaries",
    "109": "beneficiaries",
    "asset": "asset",
    "assets": "asset",
    "101": "asset",
}


def normalize_output_type(value: str) -> str:
    normalized = " ".join(str(value or "").strip().lower().split())
    return OUTPUT_TYPE_ALIASES.get(normalized, "")


def get_output_definition(value: str) -> ActivityOutputDefinition | None:
    canonical = normalize_output_type(value)
    if not canonical:
        return None
    return ACTIVITY_OUTPUT_DEFINITIONS.get(canonical)
