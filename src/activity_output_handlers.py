from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, expect

from activity_output_registry import (
    ActivityOutputDefinition,
    get_output_definition,
)

logger = logging.getLogger("egram_bot")

SELECTOR_TIMEOUT = 15_000


class ActivityOutputError(RuntimeError):
    """Raised when Activity Output processing cannot complete."""


@dataclass(frozen=True)
class OutputFieldSpec:
    label: str
    key: str
    selector: str
    field_type: str  # "select" | "text"
    normalizer: Callable[[str], str] | None = None


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _dropdown_match_key(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return re.sub(r"\s*([()])\s*", r"\1", normalized)


def _dropdown_match_key_without_whitespace(value: str) -> str:
    return re.sub(r"\s+", "", _dropdown_match_key(value))


def _value(record: dict, key: str) -> str:
    return str(record.get(key, "") or "").strip()


def _select_by_visible_text(page: Page, selector: str, text: str) -> None:
    loc = page.locator(selector)
    loc.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)

    options = page.eval_on_selector(
        selector,
        "el => Array.from(el.options).filter(o => o.value !== '').map(o => ({value: o.value, text: o.textContent.trim()}))",
    )
    original = str(text or "")
    requested = original.strip()
    normalized_requested = _dropdown_match_key(requested)
    compact_requested = _dropdown_match_key_without_whitespace(requested)
    for option in options:
        option_text = str(option["text"] or "").strip()
        if option_text == requested:
            logger.debug(
                "dropdown match selector=%s original=%r normalized=%r matched=%r mode=exact",
                selector, original, normalized_requested, option_text,
            )
            loc.select_option(value=option["value"])
            return
        if _dropdown_match_key(option_text) == normalized_requested:
            logger.info(
                "dropdown match selector=%s original=%r normalized=%r matched=%r mode=normalized",
                selector, original, normalized_requested, option_text,
            )
            loc.select_option(value=option["value"])
            return

    for option in options:
        option_text = str(option["text"] or "").strip()
        if _dropdown_match_key_without_whitespace(option_text) == compact_requested:
            logger.info(
                "dropdown match selector=%s original=%r normalized=%r matched=%r mode=normalized_no_whitespace",
                selector, original, normalized_requested, option_text,
            )
            loc.select_option(value=option["value"])
            return

    available = [o["text"] for o in options]
    raise ActivityOutputError(
        f"'{text}' not found in {selector}. Available: {available}"
    )


def _select_by_visible_text_stable(page: Page, selector: str, text: str, retries: int = 1) -> None:
    requested = _dropdown_match_key(text)

    page.wait_for_function(
        """
        ({ selector, expected }) => {
            const el = document.querySelector(selector);
            if (!el) return false;
            const norm = (v) => String(v || '').trim().toLowerCase().replace(/\\s+/g, ' ').replace(/\\s*([()])\\s*/g, '$1');
            const compact = (v) => norm(v).replace(/\\s/g, '');
            const options = Array.from(el.options || []).filter(o => o.value !== '');
            return options.some(o => norm(o.textContent) === expected || compact(o.textContent) === compact(expected));
        }
        """,
        arg={"selector": selector, "expected": requested},
        timeout=SELECTOR_TIMEOUT,
    )

    for attempt in range(1, 2):
        _select_by_visible_text(page, selector, text)
        try:
            page.wait_for_function(
                """
                ({ selector, expected }) => {
                    const el = document.querySelector(selector);
                    if (!el) return false;
                    const norm = (v) => String(v || '').trim().toLowerCase().replace(/\\s+/g, ' ').replace(/\\s*([()])\\s*/g, '$1');
                    const compact = (v) => norm(v).replace(/\\s/g, '');
                    const selected = el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
                    return !!selected && (norm(selected.textContent) === expected || compact(selected.textContent) === compact(expected));
                }
                """,
                arg={"selector": selector, "expected": requested},
                timeout=3_000,
            )
            return
        except PlaywrightTimeout:
            logger.warning(
                "training_select retry %d/%d selector=%s text=%s",
                attempt,
                retries,
                selector,
                text,
            )

    options = page.eval_on_selector(
        selector,
        "el => Array.from(el.options).filter(o => o.value !== '').map(o => ({value: o.value, text: o.textContent.trim()}))",
    )
    current = page.eval_on_selector(
        selector,
        "el => (el.options && el.selectedIndex >= 0) ? el.options[el.selectedIndex].textContent.trim() : ''",
    )
    raise ActivityOutputError(
        f"'{text}' did not persist in {selector}. Current='{current}'. Available={options}"
    )


class BaseOutputHandler:
    def __init__(self, page: Page, definition: ActivityOutputDefinition) -> None:
        self.page = page
        self.definition = definition

    def process(self, activity_key: str, detail_record: dict, row_index: int) -> None:
        raise NotImplementedError()


class StructuredModalOutputHandler(BaseOutputHandler):
    """Reusable template for modal-based Activity Output handlers."""

    TITLE_EXPECTED_TEXT = ""
    FIELD_SPECS: tuple[OutputFieldSpec, ...] = ()

    @classmethod
    def validate_detail_record(cls, detail_record: dict, activity_key: str) -> None:
        for spec in cls.FIELD_SPECS:
            if not _value(detail_record, spec.key):
                raise ActivityOutputError(
                    f"{cls._error_prefix()} output data invalid for Activity_Key {activity_key}: {spec.label} is required"
                )

    @classmethod
    def _error_prefix(cls) -> str:
        return cls.__name__.replace("OutputHandler", "")

    def process(self, activity_key: str, detail_record: dict, row_index: int) -> None:
        self.validate_detail_record(detail_record, activity_key)

        logger.info("[%s] Activity Output: %s", activity_key, self.definition.visible_name)
        logger.info("[%s] Handler: %s", activity_key, self.definition.handler_name)
        logger.info("[%s] Detail Sheet: %s", activity_key, self.definition.sheet_name)
        logger.info("[%s] Radio: %s", activity_key, self.definition.radio_id)

        self._open_modal(activity_key)
        self._verify_modal_title(activity_key)
        self._fill_fields(detail_record)
        self._verify_filled_values(detail_record, activity_key)

        pre_submit_errors = self._collect_validation_errors()
        if pre_submit_errors:
            raise ActivityOutputError(
                f"{self._error_prefix()} validation errors before submit for Activity_Key {activity_key}: {'; '.join(pre_submit_errors)}"
            )

        self._submit(activity_key)
        self._verify_output_retained(activity_key)

    def _open_modal(self, activity_key: str) -> None:
        section = self.page.locator("#outputActvityTypeDivId")
        section.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)

        radio = self.page.locator(f"#{self.definition.radio_id}")
        if not radio.count():
            raise ActivityOutputError(
                f"{self._error_prefix()} radio not found: {self.definition.radio_id}"
            )

        radio.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
        if not radio.is_enabled():
            raise ActivityOutputError(
                f"{self._error_prefix()} radio is disabled: {self.definition.radio_id}"
            )

        radio.scroll_into_view_if_needed()
        # The native click executes the portal's inline onclick exactly once.
        # Calling showAssetDetailsPopup again can re-run validation and hide the modal.
        radio.click(force=True)

        modal = self.page.locator(f"#{self.definition.modal_id}")
        try:
            self.page.wait_for_function(
                """
                ({ modalId }) => {
                    const modal = document.getElementById(modalId);
                    if (!modal) return false;

                    const style = window.getComputedStyle(modal);
                    const shownByClass = modal.classList.contains('show') || modal.classList.contains('in');
                    const shownByStyle = style.display !== 'none' && style.visibility !== 'hidden';
                    const ariaOpen = modal.getAttribute('aria-hidden') === 'false';
                    const trainingCategory = document.getElementById('trngCatCdId');
                    const categoryVisible = !!trainingCategory && trainingCategory.offsetParent !== null;
                    const backdrop = document.querySelector('.modal-backdrop.show, .modal-backdrop.in');

                    return (shownByClass && shownByStyle) || ariaOpen || (categoryVisible && !!backdrop);
                }
                """,
                arg={"modalId": self.definition.modal_id},
                timeout=SELECTOR_TIMEOUT,
            )
            modal.wait_for(state="attached", timeout=SELECTOR_TIMEOUT)
        except PlaywrightTimeout as exc:
            diagnostics = self._collect_modal_open_diagnostics()
            raise ActivityOutputError(
                f"{self._error_prefix()} modal did not open: {self.definition.modal_id}; diagnostics={diagnostics}"
            ) from exc

        logger.info("[%s] Modal opened: %s", activity_key, self.definition.modal_id)

    def _collect_modal_open_diagnostics(self) -> dict:
        return self.page.evaluate(
            """
            ({ modalId, radioId }) => {
                const modal = document.getElementById(modalId);
                const radio = document.getElementById(radioId);
                const outputHidden = document.getElementById('ouptputTypId');
                const trainingCategory = document.getElementById('trngCatCdId');
                const style = modal ? window.getComputedStyle(modal) : null;
                const errorIds = [
                    'activityoutputError',
                    'activitystartYearError',
                    'activitystartmonthError',
                    'activitydurationError',
                    'costlessTotalCostError',
                    'indicativeTotalCostWrongValueError',
                    'activityexpeBenefiError',
                    'activityexpeBenefiLimitError',
                    'activityActIsError',
                    'activityMAError',
                    'activityForError',
                    'activitycostLessError',
                    'activityoperationalError',
                    'trainingCatError',
                    'trainingOrgError',
                    'trainingSubTrainError',
                    'trainingTraineesError',
                    'trainingDurationError'
                ];
                const visibleErrors = errorIds.map(id => document.getElementById(id)).filter(el => el && el.offsetParent !== null).map(el => (el.innerText || '').trim()).filter(Boolean);

                return {
                    modalExists: !!modal,
                    modalClass: modal ? modal.className : null,
                    modalDisplay: style ? style.display : null,
                    modalVisibility: style ? style.visibility : null,
                    modalAriaHidden: modal ? modal.getAttribute('aria-hidden') : null,
                    radioExists: !!radio,
                    radioChecked: !!radio && radio.checked,
                    radioDisabled: !!radio && radio.disabled,
                    radioOnclick: radio ? radio.getAttribute('onclick') : null,
                    outputTypeHidden: outputHidden ? outputHidden.value : null,
                    startMonthValue: document.getElementById('startMonthId') ? document.getElementById('startMonthId').value : null,
                    totalIndicativeCostValue: document.getElementById('costlessTotalCostId') ? document.getElementById('costlessTotalCostId').value : null,
                    totalExpectedBeneficiariesValue: document.getElementById('totalId') ? document.getElementById('totalId').value : null,
                    trainingCategoryVisible: !!trainingCategory && trainingCategory.offsetParent !== null,
                    hasModalBackdrop: !!document.querySelector('.modal-backdrop.show, .modal-backdrop.in'),
                    visibleErrors: visibleErrors
                };
            }
            """,
            {"modalId": self.definition.modal_id, "radioId": self.definition.radio_id},
        )

    def _verify_modal_title(self, activity_key: str) -> None:
        modal = self.page.locator(f"#{self.definition.modal_id}")
        title = modal.locator(".modal-title:visible").first
        expected = _normalize_text(self.TITLE_EXPECTED_TEXT)

        if title.count():
            title_text = _normalize_text(title.inner_text())
        else:
            title_text = _normalize_text(modal.inner_text())

        if expected and expected not in title_text:
            raise ActivityOutputError(
                f"{self._error_prefix()} modal title mismatch for Activity_Key {activity_key}: '{title_text}'"
            )

    def _fill_fields(self, detail_record: dict) -> None:
        for spec in self.FIELD_SPECS:
            value = _value(detail_record, spec.key)
            if spec.field_type == "select":
                _select_by_visible_text_stable(self.page, spec.selector, value)
            elif spec.field_type == "text":
                field = self.page.locator(spec.selector)
                field.clear()
                field.fill(value)
            else:
                raise ActivityOutputError(f"Unsupported field type: {spec.field_type}")

    def _verify_filled_values(self, detail_record: dict, activity_key: str) -> None:
        def collect_failed() -> list[OutputFieldSpec]:
            failed_specs: list[OutputFieldSpec] = []
            for field_spec in self.FIELD_SPECS:
                expected = _value(detail_record, field_spec.key)
                if field_spec.field_type == "select":
                    actual = self.page.locator(f"{field_spec.selector} option:checked").inner_text().strip()
                else:
                    actual = self.page.locator(field_spec.selector).input_value().strip()

                normalize = field_spec.normalizer or (lambda v: v)
                if normalize(actual) != normalize(expected):
                    failed_specs.append(field_spec)
            return failed_specs

        failed_specs = collect_failed()

        # Some portal flows asynchronously re-render dropdown values after callbacks.
        # Re-apply failed select fields once before hard-failing verification.
        if any(spec.field_type == "select" for spec in failed_specs):
            for spec in failed_specs:
                if spec.field_type == "select":
                    _select_by_visible_text_stable(self.page, spec.selector, _value(detail_record, spec.key))
            failed_specs = collect_failed()

        failed = [spec.label for spec in failed_specs]

        if failed:
            raise ActivityOutputError(
                f"{self._error_prefix()} fields verification failed for Activity_Key {activity_key}: {', '.join(failed)}"
            )

        logger.info("[%s] %s fields verified: %d/%d", activity_key, self._error_prefix(), len(self.FIELD_SPECS), len(self.FIELD_SPECS))

    def _collect_validation_errors(self) -> list[str]:
        modal = self.page.locator(f"#{self.definition.modal_id}")
        errors: list[str] = []
        for element in modal.locator("[id$='Error']:visible, .text-danger:visible").all():
            text = (element.inner_text() or "").strip()
            if text:
                errors.append(text)
        return errors

    def _submit(self, activity_key: str) -> None:
        modal = self.page.locator(f"#{self.definition.modal_id}")
        submit = modal.locator("button[onclick*='validationTraining']").first
        if not submit.count():
            raise ActivityOutputError(
                f"{self._error_prefix()} submit button with validationTraining() not found"
            )

        expect(submit).to_be_enabled(timeout=SELECTOR_TIMEOUT)
        submit.click()

        try:
            modal.wait_for(state="hidden", timeout=SELECTOR_TIMEOUT)
        except PlaywrightTimeout:
            errors = self._collect_validation_errors()
            if errors:
                raise ActivityOutputError(
                    f"{self._error_prefix()} modal remained open for Activity_Key {activity_key}: {'; '.join(errors)}"
                )
            raise ActivityOutputError(
                f"{self._error_prefix()} modal remained open for Activity_Key {activity_key}"
            )

        logger.info("[%s] %s popup submitted successfully", activity_key, self._error_prefix())

    def _verify_output_retained(self, activity_key: str) -> None:
        radio = self.page.locator(f"#{self.definition.radio_id}")
        if not radio.is_checked():
            raise ActivityOutputError(
                f"{self._error_prefix()} output radio is not retained for Activity_Key {activity_key}"
            )


class UnsupportedOutputHandler(BaseOutputHandler):
    def process(self, activity_key: str, detail_record: dict, row_index: int) -> None:
        raise ActivityOutputError(
            f"Unsupported Activity Output: {self.definition.visible_name}"
        )


class TrainingOutputHandler(StructuredModalOutputHandler):
    TITLE_EXPECTED_TEXT = "training/capacity building"
    FIELD_SPECS = (
        OutputFieldSpec("Training Category", "training_category", "#trngCatCdId", "select", _normalize_text),
        OutputFieldSpec("Organized By", "organized_by", "#trngOrgByCdId", "select", _normalize_text),
        OutputFieldSpec("Subject of Training", "subject_of_training", "#trngSubjectId", "text"),
        OutputFieldSpec("Village", "village", "#trngLocCd", "select", _normalize_text),
        OutputFieldSpec("Amount", "amount", "#trngAmount", "text"),
        OutputFieldSpec("Total Trainees", "total_trainees", "#totTraineesId", "text"),
        OutputFieldSpec("Total Duration", "total_duration", "#totDurationDaysId", "text"),
    )

    @classmethod
    def validate_detail_record(cls, detail_record: dict, activity_key: str) -> None:
        for spec in cls.FIELD_SPECS:
            if spec.key != "village" and not _value(detail_record, spec.key):
                raise ActivityOutputError(
                    f"Training output data invalid for Activity_Key {activity_key}: {spec.label} is required"
                )

        amount = _value(detail_record, "amount")
        if not re.fullmatch(r"\d+(?:\.\d+)?", amount):
            raise ActivityOutputError(
                f"Training output data invalid for Activity_Key {activity_key}: Amount must be numeric"
            )

        trainees = _value(detail_record, "total_trainees")
        duration = _value(detail_record, "total_duration")

        if not trainees.isdigit():
            raise ActivityOutputError(
                f"Training output data invalid for Activity_Key {activity_key}: Total Trainees must be numeric"
            )
        if int(trainees) <= 0:
            raise ActivityOutputError(
                f"Training output data invalid for Activity_Key {activity_key}: Total Trainees must be greater than zero"
            )
        if len(trainees) > 4:
            raise ActivityOutputError(
                f"Training output data invalid for Activity_Key {activity_key}: Total Trainees exceeds maxlength 4"
            )

        if not duration.isdigit():
            raise ActivityOutputError(
                f"Training output data invalid for Activity_Key {activity_key}: Total Duration must be numeric"
            )
        if int(duration) <= 0:
            raise ActivityOutputError(
                f"Training output data invalid for Activity_Key {activity_key}: Total Duration must be greater than zero"
            )
        if len(duration) > 3:
            raise ActivityOutputError(
                f"Training output data invalid for Activity_Key {activity_key}: Total Duration exceeds maxlength 3"
            )

    def _fill_fields(self, detail_record: dict) -> None:
        super()._fill_fields(detail_record)

        # Some portal runs re-render Organized By after category callbacks settle.
        try:
            self.page.wait_for_function(
                """
                ({ expected }) => {
                    const el = document.querySelector('#trngOrgByCdId');
                    if (!el) return false;
                    const norm = (v) => String(v || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                    const selected = el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
                    return !!selected && norm(selected.textContent) === expected;
                }
                """,
                arg={"expected": _normalize_text(_value(detail_record, "organized_by"))},
                timeout=2_500,
            )
        except PlaywrightTimeout:
            _select_by_visible_text_stable(self.page, "#trngOrgByCdId", _value(detail_record, "organized_by"))


class AssetOutputHandler(StructuredModalOutputHandler):
    TITLE_EXPECTED_TEXT = "asset details"
    FIELD_SPECS = (
        OutputFieldSpec("Asset Type", "asset_type", "#astTypId", "select", _normalize_text),
        OutputFieldSpec("Asset Category", "asset_category", "#assetCategoryId", "select", _normalize_text),
        OutputFieldSpec("Asset Sub Category", "asset_sub_category", "#astSubCtgryId", "select", _normalize_text),
        OutputFieldSpec("Total Units", "total_units", "#totalUntId", "text"),
        OutputFieldSpec("Unit Cost", "unit_cost", "#untCostId", "text"),
    )

    @classmethod
    def validate_detail_record(cls, detail_record: dict, activity_key: str) -> None:
        super().validate_detail_record(detail_record, activity_key)
        for key, label in (("coverage_area", "Coverage Area"), ("census_village", "Census Village"), ("units_per_village", "Units Per Village")):
            if not _value(detail_record, key):
                raise ActivityOutputError(f"Asset output data invalid for Activity_Key {activity_key}: {label} is required")
        if _value(detail_record, "coverage_area").lower() not in {"area", "a"}:
            raise ActivityOutputError(f"Asset output data invalid for Activity_Key {activity_key}: Coverage Area must be 'Area'")
        for key, label in (("total_units", "Total Units"), ("units_per_village", "Units Per Village")):
            value = _value(detail_record, key)
            if not value.isdigit():
                raise ActivityOutputError(f"Asset output data invalid for Activity_Key {activity_key}: {label} must be numeric")
            if int(value) <= 0:
                raise ActivityOutputError(f"Asset output data invalid for Activity_Key {activity_key}: {label} must be greater than zero")
        unit_cost = _value(detail_record, "unit_cost")
        if not re.fullmatch(r"\d+(?:\.\d+)?", unit_cost) or float(unit_cost) <= 0:
            raise ActivityOutputError(f"Asset output data invalid for Activity_Key {activity_key}: Unit Cost must be numeric and greater than zero")

    def _open_modal(self, activity_key: str) -> None:
        section = self.page.locator("#outputActvityTypeDivId")
        section.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
        radio = self.page.locator("#outputActvityAstId101")
        logger.info("[%s] Asset radio found=%s visible=%s enabled=%s checked_before=%s", activity_key, bool(radio.count()), radio.is_visible() if radio.count() else False, radio.is_enabled() if radio.count() else False, radio.is_checked() if radio.count() else False)
        if not radio.count():
            raise ActivityOutputError("Asset radio not found: #outputActvityAstId101")
        radio.scroll_into_view_if_needed()
        radio.click()
        logger.info("[%s] Asset radio checked_after=%s", activity_key, radio.is_checked())
        if not radio.is_checked():
            radio.click(force=True)
        if not radio.is_checked():
            self.page.evaluate("document.querySelector('#outputActvityAstId101').click()")
        if not radio.is_checked():
            raise ActivityOutputError("Asset radio did not become checked")

        modal = self.page.locator("#showAssetDetailsPopup")
        logger.info("[%s] Asset modal selector=#showAssetDetailsPopup", activity_key)
        try:
            self.page.wait_for_function(
                """
                () => {
                    const modal = document.querySelector('#showAssetDetailsPopup');
                    if (!modal) return false;
                    const style = window.getComputedStyle(modal);
                    const field = modal.querySelector('#astTypId');
                    const shown = modal.classList.contains('show') || modal.classList.contains('in');
                    const displayed = style.display !== 'none' && style.visibility !== 'hidden';
                    const ariaOpen = modal.getAttribute('aria-hidden') === 'false';
                    return (shown && displayed) || ariaOpen || (displayed && !!field);
                }
                """,
                timeout=SELECTOR_TIMEOUT,
            )
            modal.wait_for(state="attached", timeout=SELECTOR_TIMEOUT)
            logger.info("[%s] Asset popup opened", activity_key)
        except PlaywrightTimeout as exc:
            diagnostics = self.page.evaluate(
                """
                () => {
                    const modal = document.querySelector('#showAssetDetailsPopup');
                    const style = modal ? getComputedStyle(modal) : null;
                    const field = modal ? modal.querySelector('#astTypId') : null;
                    return {
                        found: !!modal,
                        className: modal ? modal.className : null,
                        display: style ? style.display : null,
                        visibility: style ? style.visibility : null,
                        ariaHidden: modal ? modal.getAttribute('aria-hidden') : null,
                        assetTypeFound: !!field,
                        assetTypeVisible: !!field && getComputedStyle(field).display !== 'none'
                    };
                }
                """
            )
            logger.error("[%s] Asset popup did not open; diagnostics=%s", activity_key, diagnostics)
            raise ActivityOutputError(
                f"Asset popup did not open: #showAssetDetailsPopup; diagnostics={diagnostics}"
            ) from exc

    def _fill_fields(self, detail_record: dict) -> None:
        for spec in self.FIELD_SPECS:
            value = _value(detail_record, spec.key)
            field = self.page.locator(spec.selector)
            logger.info("Asset field=%s found=%s visible=%s enabled=%s excel=%r before=%r", spec.label, bool(field.count()), field.is_visible() if field.count() else False, field.is_enabled() if field.count() else False, value, field.input_value() if field.count() else None)
            if not field.count():
                raise ActivityOutputError(f"Asset field not found: {spec.label} ({spec.selector})")
            field.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
            if spec.field_type == "select":
                _select_by_visible_text_stable(self.page, spec.selector, value)
            else:
                field.fill(value)
            after = field.input_value()
            logger.info("Asset field=%s after=%r success=%s", spec.label, after, bool(after))
            if spec.field_type == "text" and after != value:
                raise ActivityOutputError(f"Asset field did not persist: {spec.label}")

        coverage = self.page.locator("input#assetCovgeAreaId, input[name='assetDetails.astCvrgCd'][value='A']").first
        coverage.check(force=True)
        if not coverage.is_checked():
            raise ActivityOutputError("Coverage Area radio did not become checked")

        available = self.page.locator("#avlPlanUnitsVillId")
        available.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
        options = self.page.locator("#avlPlanUnitsVillId option").all()
        village = _value(detail_record, "census_village")
        match = next((option for option in options if _dropdown_match_key(option.inner_text()) == _dropdown_match_key(village)), None)
        if match is None:
            raise ActivityOutputError(f"Census Village not found: {village}")
        available.select_option(value=match.get_attribute("value"))
        self.page.locator("#selectedPlanUnitsForVillId input[value='>>']").click(force=True)

        selected = self.page.locator("#selPlanUnitsVillId")
        self.page.wait_for_function("""({ village }) => Array.from(document.querySelectorAll('#selPlanUnitsVillId option')).some(o => o.textContent.trim().toLowerCase() === village)""", {"village": _dropdown_match_key(village)}, timeout=SELECTOR_TIMEOUT)
        unit_input = self.page.locator("#astNoOfUntId0, input[name^='assetDetails.assetLocationList'][name$='.astNoOfUnt']").first
        unit_input.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
        units = _value(detail_record, "units_per_village")
        logger.info("Units Per Village input found=%s id=%s before=%r expected=%r", bool(unit_input.count()), unit_input.get_attribute("id"), unit_input.input_value(), units)
        unit_input.fill(units)
        unit_input.press("Tab")
        after = unit_input.input_value()
        logger.info("Units Per Village after=%r success=%s", after, after == units)
        if after != units:
            raise ActivityOutputError(f"Units Per Village value did not persist: expected {units!r}, got {after!r}")

    def _submit(self, activity_key: str) -> None:
        modal = self.page.locator("#showAssetDetailsPopup")
        submit = modal.locator("button[onclick*='validationAsset'], input[onclick*='validationAsset']").first
        if not submit.count():
            raise ActivityOutputError("Asset Save button not found")
        submit.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
        if not submit.is_enabled():
            raise ActivityOutputError("Asset Save button is disabled")
        logger.info("[%s] Clicking Asset Save", activity_key)
        submit.click()
        modal.wait_for(state="hidden", timeout=SELECTOR_TIMEOUT)
        logger.info("[%s] Asset popup saved and closed", activity_key)


class ActivityOutputHandlerRegistry:
    def __init__(self, page: Page) -> None:
        self.page = page
        self._handlers: dict[str, type[BaseOutputHandler]] = {
            "training": TrainingOutputHandler,
            "asset": AssetOutputHandler,
        }

    def resolve(self, output_type: str) -> BaseOutputHandler:
        definition = get_output_definition(output_type)
        if not definition:
            raise ActivityOutputError(f"Unsupported Activity Output: {output_type}")

        handler_cls = self._handlers.get(definition.canonical_name)
        if handler_cls:
            return handler_cls(self.page, definition)

        if definition.implemented:
            return BaseOutputHandler(self.page, definition)

        return UnsupportedOutputHandler(self.page, definition)

