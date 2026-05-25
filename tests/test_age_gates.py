"""
Regression tests for the age-gate validation bug (W1.1).

Bug: age gates were checking the patient's CURRENT age instead of age AT
ONSET, so a 5-year-old could carry asthma with onset at 11.8mo, or a
4-year-old could carry ADHD with onset at 18mo, etc.

Fix: PedsEngine._validate_and_fix_age_gates (and AdultEngine equivalent)
runs as a post-generation pass that re-validates every condition's
onset_date against its age gate using age AT ONSET, clamping if needed.
PatientValidator.AGE_GATES also catches any leak as a safety net.

See: output/oread_persistent_issues.md
See: BETA-WORKLIST.md (W1.1)
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from dateutil.relativedelta import relativedelta

# Make project root importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import (
    CodeableConcept,
    Condition,
    ConditionStatus,
    Demographics,
    Patient,
    Sex,
    SocialHistory,
)
from src.models.patient import Address, Contact
from src.validators import (
    PatientValidator,
    ValidationSeverity,
    ValidationType,
)


# ---------------------------------------------------------------------- #
# Helpers                                                                #
# ---------------------------------------------------------------------- #

def _make_patient(
    dob: date,
    sex: Sex = Sex.MALE,
    conditions: list[Condition] | None = None,
) -> Patient:
    """Build a minimal Patient with the given DOB and optional problem_list."""
    address = Address(
        line1="123 Main St",
        city="Springfield",
        state="MN",
        postal_code="55555",
    )
    emergency_contact = Contact(
        name="Jane Doe",
        relationship="Mother",
        phone="(555) 123-4567",
    )
    demographics = Demographics(
        given_names=["Test"],
        family_name="Patient",
        date_of_birth=dob,
        sex_at_birth=sex,
        address=address,
        phone="(555) 987-6543",
        emergency_contact=emergency_contact,
    )
    social = SocialHistory(living_situation="Lives with parents")
    return Patient(
        demographics=demographics,
        social_history=social,
        problem_list=conditions or [],
    )


def _make_condition(display_name: str, icd10: str, onset_date: date) -> Condition:
    return Condition(
        display_name=display_name,
        code=CodeableConcept(
            system="http://hl7.org/fhir/sid/icd-10-cm",
            code=icd10,
            display=display_name,
        ),
        clinical_status=ConditionStatus.ACTIVE,
        onset_date=onset_date,
    )


# ---------------------------------------------------------------------- #
# Validator-level tests (catches violations regardless of code path)      #
# ---------------------------------------------------------------------- #

class TestPatientValidatorAgeGates:
    """The validator must flag onset_date that violates an age gate."""

    def test_asthma_onset_below_12mo_is_flagged_even_for_older_patient(self):
        """Asthma onset Jan 27 2021 with DOB Feb 2 2020 is 11.8mo - reject."""
        dob = date(2020, 2, 2)
        # Today is well past the patient's 5th birthday in this test world,
        # so this exactly mirrors the Patient 5 scenario from the bug doc.
        asthma = _make_condition("Asthma", "J45.20", date(2021, 1, 27))
        patient = _make_patient(dob, conditions=[asthma])

        result = PatientValidator().validate(patient)
        age_gate_issues = [i for i in result.issues if i.type == ValidationType.AGE_GATE]

        assert len(age_gate_issues) == 1
        assert age_gate_issues[0].severity == ValidationSeverity.CRITICAL
        assert "asthma" in age_gate_issues[0].message.lower()
        assert "12" in age_gate_issues[0].message  # mentions the gate

    def test_adhd_onset_at_18mo_is_flagged_even_for_4yo_patient(self):
        """ADHD onset Aug 20 2022 with DOB Jan 27 2021 is 18.7mo - reject."""
        dob = date(2021, 1, 27)
        adhd = _make_condition("ADHD", "F90.2", date(2022, 8, 20))
        patient = _make_patient(dob, conditions=[adhd])

        result = PatientValidator().validate(patient)
        age_gate_issues = [i for i in result.issues if i.type == ValidationType.AGE_GATE]

        assert len(age_gate_issues) == 1
        assert age_gate_issues[0].severity == ValidationSeverity.CRITICAL
        assert "48" in age_gate_issues[0].message

    def test_bronchiolitis_onset_at_30mo_is_flagged(self):
        """Bronchiolitis has a MAX age of 24mo - 30mo onset must be rejected."""
        dob = date(2020, 1, 1)
        bronc = _make_condition("Bronchiolitis", "J21.9", date(2022, 7, 1))  # ~30mo
        patient = _make_patient(dob, conditions=[bronc])

        result = PatientValidator().validate(patient)
        age_gate_issues = [i for i in result.issues if i.type == ValidationType.AGE_GATE]

        assert len(age_gate_issues) == 1
        assert "24" in age_gate_issues[0].message
        assert "bronchiolitis" in age_gate_issues[0].message.lower()

    def test_asthma_at_12mo_exactly_is_accepted(self):
        """Asthma onset 12mo from DOB should pass (boundary)."""
        dob = date(2020, 1, 1)
        asthma = _make_condition(
            "Asthma", "J45.20", dob + relativedelta(months=12) + timedelta(days=1)
        )
        patient = _make_patient(dob, conditions=[asthma])

        result = PatientValidator().validate(patient)
        age_gate_issues = [i for i in result.issues if i.type == ValidationType.AGE_GATE]
        assert age_gate_issues == []

    def test_no_problem_list_no_issues(self):
        """A patient with no problems should produce no age-gate issues."""
        patient = _make_patient(date(2020, 1, 1))
        result = PatientValidator().validate(patient)
        age_gate_issues = [i for i in result.issues if i.type == ValidationType.AGE_GATE]
        assert age_gate_issues == []


# ---------------------------------------------------------------------- #
# Engine post-generation fix tests                                       #
# ---------------------------------------------------------------------- #

class TestEnginePostGenerationFix:
    """
    PedsEngine._validate_and_fix_age_gates should clamp any bad onset_date
    rather than leave the violation in the patient record.
    """

    def _engine(self):
        # Avoid LLM init - we're only testing the pure-Python fix pass.
        from src.engines.engine import PedsEngine
        return PedsEngine(use_llm=False)

    def test_fix_pass_clamps_asthma_onset_below_12mo(self):
        engine = self._engine()
        dob = date(2020, 2, 2)
        asthma = _make_condition("Asthma", "J45.20", date(2021, 1, 27))  # 11.8mo
        patient = _make_patient(dob, conditions=[asthma])

        fixed = engine._validate_and_fix_age_gates(patient)

        age_at_onset = (fixed.problem_list[0].onset_date - dob).days / 30.44
        assert age_at_onset >= 12, (
            f"Asthma onset still at {age_at_onset:.1f}mo after fix pass"
        )

    def test_fix_pass_clamps_adhd_onset_below_48mo(self):
        engine = self._engine()
        dob = date(2021, 1, 27)
        adhd = _make_condition("ADHD", "F90.2", date(2022, 8, 20))  # 18.7mo
        patient = _make_patient(dob, conditions=[adhd])

        fixed = engine._validate_and_fix_age_gates(patient)

        age_at_onset = (fixed.problem_list[0].onset_date - dob).days / 30.44
        assert age_at_onset >= 48

    def test_fix_pass_clamps_bronchiolitis_onset_above_24mo(self):
        engine = self._engine()
        dob = date(2020, 1, 1)
        bronc = _make_condition("Bronchiolitis", "J21.9", date(2022, 7, 1))  # ~30mo
        patient = _make_patient(dob, conditions=[bronc])

        fixed = engine._validate_and_fix_age_gates(patient)

        age_at_onset = (fixed.problem_list[0].onset_date - dob).days / 30.44
        assert age_at_onset <= 24
        # Sanity: still after DOB
        assert fixed.problem_list[0].onset_date >= dob

    def test_fix_pass_leaves_valid_onset_unchanged(self):
        engine = self._engine()
        dob = date(2018, 1, 1)
        valid_onset = date(2024, 1, 1)  # ~6 years old, well past all gates
        adhd = _make_condition("ADHD", "F90.2", valid_onset)
        patient = _make_patient(dob, conditions=[adhd])

        fixed = engine._validate_and_fix_age_gates(patient)

        assert fixed.problem_list[0].onset_date == valid_onset

    def test_temporal_fix_respects_age_gate(self):
        """
        _fix_temporal_issue must no longer set onset to dob + 30 days
        blindly - the new behavior clamps to the age gate.

        Regression for the smoking-gun path noted in the bug doc.
        """
        from src.validators.models import ValidationIssue, ValidationSeverity as VS, ValidationType as VT

        engine = self._engine()
        dob = date(2018, 1, 1)
        # Onset before DOB will trigger _fix_temporal_issue
        adhd = _make_condition("ADHD", "F90.2", date(2017, 1, 1))
        patient = _make_patient(dob, conditions=[adhd])

        issue = ValidationIssue(
            type=VT.TEMPORAL,
            severity=VS.CRITICAL,
            message="onset before DOB",
            path="problem_list[0].onset_date",
            auto_fixable=True,
        )
        fixed = engine._fix_temporal_issue(patient, issue)

        age_at_onset = (fixed.problem_list[0].onset_date - dob).days / 30.44
        assert age_at_onset >= 48, (
            f"_fix_temporal_issue produced ADHD onset at {age_at_onset:.1f}mo "
            f"(must be >=48mo per age gate)"
        )


# ---------------------------------------------------------------------- #
# End-to-end: validator sees no AGE_GATE issues after the engine pass     #
# ---------------------------------------------------------------------- #

class TestEngineThenValidator:
    """After running the engine fix pass, the validator should be silent."""

    def test_full_pipeline_no_age_gate_violations(self):
        from src.engines.engine import PedsEngine
        engine = PedsEngine(use_llm=False)

        dob = date(2020, 2, 2)
        patient = _make_patient(
            dob,
            conditions=[
                _make_condition("Asthma", "J45.20", date(2021, 1, 27)),
                _make_condition("ADHD", "F90.2", dob + timedelta(days=400)),
            ],
        )

        patient = engine._validate_and_fix_age_gates(patient)
        result = PatientValidator().validate(patient)

        age_gate_issues = [i for i in result.issues if i.type == ValidationType.AGE_GATE]
        assert age_gate_issues == [], (
            f"Validator still flagged age-gate issues after fix pass: "
            f"{[i.message for i in age_gate_issues]}"
        )
