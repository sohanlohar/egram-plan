from __future__ import annotations

import logging

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, expect

from activity_output_registry import (
    ACTIVITY_OUTPUT_DEFINITIONS,
    ActivityOutputDefinition,
    get_output_definition,
)

logger = logging.getLogger("egram_bot")

SELECTOR_TIMEOUT = 15_000


class ActivityOutputError(RuntimeError):
    """Raised when Activity Output processing cannot complete."""


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _value(record: dict, key: str) -> str:
    return str(record.get(key, "") or "").strip()


def _select_by_visible_text(page: Page, selector: str, text: str) -> None:
    loc = page.locator(selector)
    loc.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)

    options = page.eval_on_selector(
        selector,
        "el => Array.from(el.options).filter(o => o.value !== '').map(o => ({value: o.value, text: o.textContent.trim()}))",
    )
    requested = _normalize_text(text)
    for option in options:
        if _normalize_text(option["text"]) == requested:
            loc.select_option(value=option["value"])
            return

    available = [o["text"] for o in options]
    raise ActivityOutputError(
        f"'{text}' not found in {selector}. Available: {available}"
    )


def _select_by_visible_text_stable(page: Page, selector: str, text: str, retries: int = 3) -> None:
    requested = _normalize_text(text)

    page.wait_for_function(
        """
        ({ selector, expected }) => {
            const el = document.querySelector(selector);
            if (!el) return false;
            const norm = (v) => String(v || '').trim().toLowerCase().replace(/\\s+/g, ' ');
            const options = Array.from(el.options || []).filter(o => o.value !== '');
            return options.some(o => norm(o.textContent) === expected);
        }
        """,
        arg={"selector": selector, "expected": requested},
        timeout=SELECTOR_TIMEOUT,
    )

    for attempt in range(1, retries + 1):
        _select_by_visible_text(page, selector, text)
        try:
            page.wait_for_function(
                """
                ({ selector, expected }) => {
                    const el = document.querySelector(selector);
                    if (!el) return false;
                    const norm = (v) => String(v || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                    const selected = el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
                    return !!selected && norm(selected.textContent) === expected;
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


class UnsupportedOutputHandler(BaseOutputHandler):
    def process(self, activity_key: str, detail_record: dict, row_index: int) -> None:
        raise ActivityOutputError(
            f"Unsupported Activity Output: {self.definition.visible_name}"
        )


class TrainingOutputHandler(BaseOutputHandler):
    REQUIRED_FIELDS = {
        "Activity_Key": "activity_key",
        "Training Category": "training_category",
        "Organized By": "organized_by",
        "Subject of Training": "subject_of_training",
        "Total Trainees": "total_trainees",
        "Total Duration": "total_duration",
    }

    @classmethod
    def validate_detail_record(cls, detail_record: dict, activity_key: str) -> None:
        for label, key in cls.REQUIRED_FIELDS.items():
            value = _value(detail_record, key)
            if not value:
                raise ActivityOutputError(
                    f"Training output data invalid for Activity_Key {activity_key}: {label} is required"
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

    def process(self, activity_key: str, detail_record: dict, row_index: int) -> None:
        self.validate_detail_record(detail_record, activity_key)

        logger.info("[%s] Activity Output: %s", activity_key, self.definition.visible_name)
        logger.info("[%s] Handler: %s", activity_key, self.definition.handler_name)
        logger.info("[%s] Detail Sheet: %s", activity_key, self.definition.sheet_name)
        logger.info("[%s] Radio: %s", activity_key, self.definition.radio_id)

        self._open_modal(activity_key, row_index)
        self._verify_modal_title(activity_key)
        self._fill_fields(detail_record)
        self._verify_filled_values(detail_record, activity_key)

        pre_submit_errors = self._collect_validation_errors()
        if pre_submit_errors:
            raise ActivityOutputError(
                f"Training validation errors before submit for Activity_Key {activity_key}: {'; '.join(pre_submit_errors)}"
            )

        self._submit(activity_key)
        self._verify_output_retained(activity_key)

    def _open_modal(self, activity_key: str, row_index: int) -> None:
        section = self.page.locator("#outputActvityTypeDivId")
        section.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)

        radio = self.page.locator(f"#{self.definition.radio_id}")
        if not radio.count():
            raise ActivityOutputError(
                f"Training radio not found: {self.definition.radio_id}"
            )

        radio.wait_for(state="visible", timeout=SELECTOR_TIMEOUT)
        if not radio.is_enabled():
            raise ActivityOutputError(
                f"Training radio is disabled: {self.definition.radio_id}"
            )

        radio.scroll_into_view_if_needed()
        try:
            radio.click(force=True)
        except Exception:
            pass
        self.page.evaluate(
            """
            ({ modalId, radioId }) => {
                const radio = document.getElementById(radioId);
                if (radio) {
                    radio.checked = true;
                    radio.dispatchEvent(new Event('change', { bubbles: true }));
                }
                if (typeof window.showAssetDetailsPopup === 'function') {
                    window.showAssetDetailsPopup(modalId, radioId);
                }
            }
            """,
            {"modalId": self.definition.modal_id, "radioId": self.definition.radio_id},
        )

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
                f"Training modal did not open: {self.definition.modal_id}; diagnostics={diagnostics}"
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
        expected = "training/capacity building"

        if title.count():
            title_text = _normalize_text(title.inner_text())
        else:
            title_text = _normalize_text(modal.inner_text())

        if expected not in title_text:
            raise ActivityOutputError(
                f"Training modal title mismatch for Activity_Key {activity_key}: '{title_text}'"
            )

    def _fill_fields(self, detail_record: dict) -> None:
        _select_by_visible_text_stable(self.page, "#trngCatCdId", _value(detail_record, "training_category"))
        _select_by_visible_text_stable(self.page, "#trngOrgByCdId", _value(detail_record, "organized_by"))

        subject = self.page.locator("#trngSubjectId")
        subject.clear()
        subject.fill(_value(detail_record, "subject_of_training"))

        trainees = self.page.locator("#totTraineesId")
        trainees.clear()
        trainees.fill(_value(detail_record, "total_trainees"))

        duration = self.page.locator("#totDurationDaysId")
        duration.clear()
        duration.fill(_value(detail_record, "total_duration"))

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

    def _verify_filled_values(self, detail_record: dict, activity_key: str) -> None:
        try:
            self.page.wait_for_function(
                """
                ({ category, organizedBy, subject, trainees, duration }) => {
                    const norm = (v) => String(v || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                    const selectedText = (selector) => {
                        const el = document.querySelector(selector);
                        if (!el || el.selectedIndex < 0) return '';
                        return norm(el.options[el.selectedIndex].textContent);
                    };
                    const valueOf = (selector) => {
                        const el = document.querySelector(selector);
                        return el ? String(el.value || '').trim() : '';
                    };

                    return (
                        selectedText('#trngCatCdId') === category
                        && selectedText('#trngOrgByCdId') === organizedBy
                        && valueOf('#trngSubjectId') === subject
                        && valueOf('#totTraineesId') === trainees
                        && valueOf('#totDurationDaysId') === duration
                    );
                }
                """,
                arg={
                    "category": _normalize_text(_value(detail_record, "training_category")),
                    "organizedBy": _normalize_text(_value(detail_record, "organized_by")),
                    "subject": _value(detail_record, "subject_of_training"),
                    "trainees": _value(detail_record, "total_trainees"),
                    "duration": _value(detail_record, "total_duration"),
                },
                timeout=5_000,
            )
        except PlaywrightTimeout:
            pass

        category_selected = self.page.locator("#trngCatCdId option:checked").inner_text().strip()
        organized_by_selected = self.page.locator("#trngOrgByCdId option:checked").inner_text().strip()
        subject_value = self.page.locator("#trngSubjectId").input_value().strip()
        trainees_value = self.page.locator("#totTraineesId").input_value().strip()
        duration_value = self.page.locator("#totDurationDaysId").input_value().strip()

        checks = [
            (_normalize_text(category_selected) == _normalize_text(_value(detail_record, "training_category")), "Training Category"),
            (_normalize_text(organized_by_selected) == _normalize_text(_value(detail_record, "organized_by")), "Organized By"),
            (subject_value == _value(detail_record, "subject_of_training"), "Subject of Training"),
            (trainees_value == _value(detail_record, "total_trainees"), "Total Trainees"),
            (duration_value == _value(detail_record, "total_duration"), "Total Duration"),
        ]

        failed = [name for ok, name in checks if not ok]
        if failed:
            raise ActivityOutputError(
                f"Training fields verification failed for Activity_Key {activity_key}: {', '.join(failed)}"
            )

        logger.info("[%s] Training fields verified: 5/5", activity_key)

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
            raise ActivityOutputError("Training submit button with validationTraining() not found")

        expect(submit).to_be_enabled(timeout=SELECTOR_TIMEOUT)
        submit.click()

        try:
            modal.wait_for(state="hidden", timeout=SELECTOR_TIMEOUT)
        except PlaywrightTimeout:
            errors = self._collect_validation_errors()
            if errors:
                raise ActivityOutputError(
                    f"Training modal remained open for Activity_Key {activity_key}: {'; '.join(errors)}"
                )
            raise ActivityOutputError(
                f"Training modal remained open for Activity_Key {activity_key}"
            )

        logger.info("[%s] Training popup submitted successfully", activity_key)

    def _verify_output_retained(self, activity_key: str) -> None:
        radio = self.page.locator(f"#{self.definition.radio_id}")
        if not radio.is_checked():
            raise ActivityOutputError(
                f"Training output radio is not retained for Activity_Key {activity_key}"
            )


class ActivityOutputHandlerRegistry:
    def __init__(self, page: Page) -> None:
        self.page = page

    def resolve(self, output_type: str) -> BaseOutputHandler:
        definition = get_output_definition(output_type)
        if not definition:
            raise ActivityOutputError(f"Unsupported Activity Output: {output_type}")

        if definition.canonical_name == "training":
            return TrainingOutputHandler(self.page, definition)

        if definition.implemented:
            return BaseOutputHandler(self.page, definition)

        return UnsupportedOutputHandler(self.page, definition)

    @staticmethod
    def supported_definitions() -> list[ActivityOutputDefinition]:
        return list(ACTIVITY_OUTPUT_DEFINITIONS.values())
