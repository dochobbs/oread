"""
FHIR R4 Exporter for SynthPatient.

Converts internal Patient model to FHIR R4 Bundle format.
Reference: https://www.hl7.org/fhir/R4/

Conformance notes:
- Validates under fhir.resources.R4B (R4-compatible models).
- No null or empty-string values: optional elements are omitted.
- Datetimes carry a timezone. The generator stores naive clock times; they
  are labeled UTC so the output is deterministic across machines.
- US Core profiles (STU 6.1) are claimed in meta.profile only where the
  resource carries every element the profile requires.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import uuid4

from dateutil.relativedelta import relativedelta

from src.exporters.codes import (
    ACT_REASON_URI,
    CDC_RACE_ETH_URI,
    OMB_ETHNICITY,
    OMB_RACE,
    V3_ROUTE_URI,
    encounter_cpt,
    is_placeholder_code,
    lab_ucum,
    language_code,
    not_done_reason,
    oread_oid,
    parse_number,
    route_info,
    system_uri,
)
from src.exporters.measurements import as_date, vital_sessions
from src.exporters.sig import build_sig
from src.models import (
    Patient,
    Encounter,
    Condition,
    Medication,
    Allergy,
    Immunization,
    LabResult,
    ImagingResult,
    PatientMessage,
    MessageCategory,
    MessageMedium,
    MessageStatus,
    Sex,
    ConditionStatus,
    MedicationStatus,
    AllergyCategory,
    EncounterClass,
    EncounterStatus,
    ImmunizationStatus,
    Interpretation,
    ResultStatus,
)

UCUM = "http://unitsofmeasure.org"
LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
US_CORE = "http://hl7.org/fhir/us/core/StructureDefinition/"
OBS_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"


def generate_uuid() -> str:
    """Generate a UUID for FHIR resources."""
    return str(uuid4())


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def format_date(d: date | datetime | None) -> str | None:
    """Format a FHIR date/dateTime. Datetimes always carry a timezone."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return _aware(d).isoformat(timespec="seconds")
    return d.isoformat()


def format_instant(d: date | datetime | None) -> str | None:
    """Format a FHIR instant (full timestamp with timezone)."""
    if d is None:
        return None
    if not isinstance(d, datetime):
        d = datetime.combine(d, time(0, 0))
    return _aware(d).isoformat(timespec="seconds")


def _clean(value: Any) -> Any:
    """Drop None, "", empty lists and empty dicts, recursively.

    FHIR forbids empty elements and null values; omitting is the right call
    for optional elements.
    """
    if isinstance(value, dict):
        out = {k: _clean(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        out = [_clean(v) for v in value]
        return [v for v in out if v not in (None, "", [], {})]
    return value


def _coding(concept, display: str | None = None) -> dict | None:
    """A Coding from a CodeableConcept, or None for placeholder/unknown codes."""
    if concept is None or is_placeholder_code(concept.code):
        return None
    system = system_uri(concept.system)
    if not system:
        return None
    return {"system": system, "code": concept.code, "display": display or concept.display}


def _concept(concept, text: str | None) -> dict:
    coding = _coding(concept)
    return {"coding": [coding] if coding else [], "text": text}


def _quantity(value: float, unit_text: str | None, ucum: str | None) -> dict:
    q = {"value": value, "unit": unit_text or ucum}
    if ucum:
        q["system"] = UCUM
        q["code"] = ucum
    return q


class FHIRExporter:
    """
    Exports Patient data to FHIR R4 Bundle format.
    """

    def __init__(self):
        self.base_url = "urn:uuid:"

    def export(self, patient: Patient) -> dict[str, Any]:
        """
        Export a patient to a FHIR R4 Bundle.

        Returns a dictionary that can be serialized to JSON.
        """
        entries = []
        self._patient = patient

        # Patient resource
        patient_id = generate_uuid()
        patient_resource = self._create_patient_resource(patient, patient_id)
        entries.append(self._bundle_entry(patient_resource, patient_id))

        # Conditions
        for condition in patient.problem_list:
            condition_id = generate_uuid()
            condition_resource = self._create_condition_resource(condition, patient_id, condition_id)
            entries.append(self._bundle_entry(condition_resource, condition_id))

        # Medications
        for med in patient.medication_list:
            med_id = generate_uuid()
            med_resource = self._create_medication_statement_resource(med, patient_id, med_id)
            entries.append(self._bundle_entry(med_resource, med_id))

        # Allergies
        for allergy in patient.allergy_list:
            allergy_id = generate_uuid()
            allergy_resource = self._create_allergy_resource(allergy, patient_id, allergy_id)
            entries.append(self._bundle_entry(allergy_resource, allergy_id))

        # Immunizations
        for imm in patient.immunization_record:
            imm_id = generate_uuid()
            imm_resource = self._create_immunization_resource(imm, patient_id, imm_id)
            entries.append(self._bundle_entry(imm_resource, imm_id))

        # Encounters
        encounter_id_map = {}
        for encounter in patient.encounters:
            enc_id = generate_uuid()
            encounter_id_map[encounter.id] = enc_id
            enc_resource = self._create_encounter_resource(encounter, patient_id, enc_id)
            entries.append(self._bundle_entry(enc_resource, enc_id))

            # Lab results from this encounter
            for lab_result in encounter.lab_results:
                labs = lab_result.results if hasattr(lab_result, 'results') else [lab_result]
                for lab in labs:
                    lab_id = generate_uuid()
                    lab_obs = self._create_lab_observation(lab, patient_id, enc_id, lab_id,
                                                           encounter.date)
                    entries.append(self._bundle_entry(lab_obs, lab_id))

            # Imaging results from this encounter
            for imaging in encounter.imaging_results:
                img_id = generate_uuid()
                img_report = self._create_diagnostic_report(imaging, patient_id, enc_id, img_id)
                entries.append(self._bundle_entry(img_report, img_id))

        # Vital signs: encounter vitals merged with same-day growth data, so
        # each measurement (e.g. one weigh-in) is one Observation.
        for session in vital_sessions(patient):
            for obs in self._create_vital_observations(
                    session, patient_id, encounter_id_map.get(session.encounter_id)):
                entries.append(self._bundle_entry(obs, obs["id"]))

        # Patient messages (Communications)
        for message in patient.patient_messages:
            comm_id = generate_uuid()
            comm_resource = self._create_communication_resource(
                message, patient_id, encounter_id_map.get(message.related_encounter_id), comm_id
            )
            entries.append(self._bundle_entry(comm_resource, comm_id))

        # Build the bundle
        bundle = {
            "resourceType": "Bundle",
            "id": generate_uuid(),
            "type": "collection",
            "timestamp": format_instant(datetime.now(timezone.utc)),
            "entry": entries,
        }

        return bundle

    def export_json(self, patient: Patient, indent: int = 2) -> str:
        """Export to JSON string."""
        bundle = self.export(patient)
        return json.dumps(bundle, indent=indent, default=str)

    def _bundle_entry(self, resource: dict, resource_id: str) -> dict:
        """Wrap a resource in a bundle entry (empty/null values removed)."""
        return {
            "fullUrl": f"{self.base_url}{resource_id}",
            "resource": _clean(resource),
        }

    @staticmethod
    def _profile(name: str) -> dict:
        return {"profile": [f"{US_CORE}{name}"]}

    def _create_patient_resource(self, patient: Patient, patient_id: str) -> dict:
        """Create FHIR Patient resource (US Core Patient)."""
        demo = patient.demographics

        name = {
            "use": "official",
            "family": demo.family_name,
            "given": demo.given_names,
        }

        gender_map = {
            Sex.MALE: "male",
            Sex.FEMALE: "female",
            Sex.INTERSEX: "other",
            Sex.UNKNOWN: "unknown",
        }

        address = {
            "use": "home",
            "type": "physical",
            "line": [demo.address.line1, demo.address.line2],
            "city": demo.address.city,
            "state": demo.address.state,
            "postalCode": demo.address.postal_code,
            "country": demo.address.country,
        }

        telecom = []
        if demo.phone:
            telecom.append({"system": "phone", "value": demo.phone, "use": "home"})
        if demo.email:
            telecom.append({"system": "email", "value": demo.email})

        contact = []
        if demo.emergency_contact:
            ec = demo.emergency_contact
            contact.append({
                "relationship": [{"text": ec.relationship}],
                "name": {"text": ec.name},
                "telecom": [{"system": "phone", "value": ec.phone}] if ec.phone else [],
            })

        resource = {
            "resourceType": "Patient",
            "id": patient_id,
            "meta": self._profile("us-core-patient"),
            "identifier": [{
                "system": f"urn:oid:{oread_oid('patient')}",
                "value": patient.id,
            }],
            "name": [name],
            "gender": gender_map.get(demo.sex_at_birth, "unknown"),
            "birthDate": format_date(demo.date_of_birth),
            "address": [address],
            "telecom": telecom,
            "contact": contact,
            "communication": [{
                "language": {
                    "coding": [{
                        "system": "urn:ietf:bcp:47",
                        "code": language_code(demo.preferred_language),
                        "display": demo.preferred_language,
                    }],
                    "text": demo.preferred_language,
                },
                "preferred": True,
            }],
        }

        # US Core race / ethnicity: one extension each, OMB categories + text
        extensions = []
        if demo.race:
            race_ext = [{"url": "ombCategory", "valueCoding": {
                "system": CDC_RACE_ETH_URI, "code": code, "display": display}}
                for code, display in filter(None, (OMB_RACE.get(r.strip().lower())
                                                   for r in demo.race))]
            race_ext.append({"url": "text", "valueString": ", ".join(demo.race)})
            extensions.append({"url": f"{US_CORE}us-core-race", "extension": race_ext})
        if demo.ethnicity:
            eth_ext = []
            eth = OMB_ETHNICITY.get(demo.ethnicity.strip().lower())
            if eth:
                eth_ext.append({"url": "ombCategory", "valueCoding": {
                    "system": CDC_RACE_ETH_URI, "code": eth[0], "display": eth[1]}})
            eth_ext.append({"url": "text", "valueString": demo.ethnicity})
            extensions.append({"url": f"{US_CORE}us-core-ethnicity", "extension": eth_ext})
        resource["extension"] = extensions
        return resource

    # Condition.category from the model's category.
    # Source: FHIR condition-category; US Core condition-category (health-concern).
    CONDITION_CATEGORY = {
        "problem": ("http://terminology.hl7.org/CodeSystem/condition-category",
                    "problem-list-item", "Problem List Item"),
        "diagnosis": ("http://terminology.hl7.org/CodeSystem/condition-category",
                      "problem-list-item", "Problem List Item"),
        "encounter-diagnosis": ("http://terminology.hl7.org/CodeSystem/condition-category",
                                "encounter-diagnosis", "Encounter Diagnosis"),
        "health-concern": ("http://hl7.org/fhir/us/core/CodeSystem/condition-category",
                           "health-concern", "Health Concern"),
    }

    def _create_condition_resource(self, condition: Condition, patient_id: str, condition_id: str) -> dict:
        """Create FHIR Condition resource."""
        status_map = {
            ConditionStatus.ACTIVE: "active",
            ConditionStatus.RECURRENCE: "recurrence",
            ConditionStatus.RELAPSE: "relapse",
            ConditionStatus.INACTIVE: "inactive",
            ConditionStatus.REMISSION: "remission",
            ConditionStatus.RESOLVED: "resolved",
        }
        system, code, display = self.CONDITION_CATEGORY.get(
            condition.category, self.CONDITION_CATEGORY["problem"])
        coding = _coding(condition.code)
        profile = ("us-core-condition-encounter-diagnosis" if code == "encounter-diagnosis"
                   else "us-core-condition-problems-health-concerns")

        resource = {
            "resourceType": "Condition",
            "id": condition_id,
            "meta": self._profile(profile) if coding else None,
            "clinicalStatus": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": status_map.get(condition.clinical_status, "active"),
                }],
            },
            "verificationStatus": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                    "code": condition.verification_status.value,
                }],
            },
            "category": [{"coding": [{"system": system, "code": code, "display": display}]}],
            "code": {"coding": [coding] if coding else [], "text": condition.display_name},
            "subject": {"reference": f"urn:uuid:{patient_id}"},
            "onsetDateTime": format_date(condition.onset_date),
            "abatementDateTime": format_date(condition.abatement_date),
            "recordedDate": format_date(condition.recorded_date),
        }

        severity_codes = {"mild": "255604002", "moderate": "6736007", "severe": "24484000"}
        if condition.severity and condition.severity.value in severity_codes:
            resource["severity"] = {
                "coding": [{
                    "system": SNOMED,
                    "code": severity_codes[condition.severity.value],
                    "display": condition.severity.value.title(),
                }],
            }

        if condition.notes:
            resource["note"] = [{"text": condition.notes}]

        return resource

    def _dosage(self, med: Medication) -> dict:
        """Dosage with structured timing, PRN, numeric dose and the full sig."""
        sig = build_sig(med)
        t = sig.timing
        dosage: dict[str, Any] = {"text": sig.text}

        repeat: dict[str, Any] = {}
        if t.once:
            repeat = {"count": 1}
        elif t.event:
            repeat = {"frequency": 1, "period": 1, "periodUnit": "d", "when": [t.event]}
        elif t.period is not None:
            repeat = {"frequency": 1, "period": t.period, "periodUnit": t.period_unit}
            if t.period_max is not None:
                repeat["periodMax"] = t.period_max
        if repeat:
            dosage["timing"] = {"repeat": repeat}

        if t.prn:
            reason = med.prn_reason
            if reason:
                dosage["asNeededCodeableConcept"] = {"text": reason}
            else:
                dosage["asNeededBoolean"] = True

        info = route_info(med.route)
        if info:
            dosage["route"] = {"coding": [{"system": SNOMED, "code": info[2], "display": info[3]}],
                               "text": med.route}
        elif med.route:
            dosage["route"] = {"text": med.route}

        unit_text = med.dose_unit or None
        if sig.dose and sig.unit_known:
            low, high = sig.dose
            if high is None:
                dosage["doseAndRate"] = [{"doseQuantity": _quantity(low, unit_text, sig.ucum)}]
            else:
                dosage["doseAndRate"] = [{"doseRange": {
                    "low": _quantity(low, unit_text, sig.ucum),
                    "high": _quantity(high, unit_text, sig.ucum)}}]
        if sig.daily_dose is not None and sig.unit_known:
            dosage["maxDosePerPeriod"] = {
                "numerator": _quantity(sig.daily_dose, unit_text, sig.ucum),
                "denominator": {"value": 1, "unit": "day", "system": UCUM, "code": "d"},
            }
        return dosage

    def _create_medication_statement_resource(self, med: Medication, patient_id: str, med_id: str) -> dict:
        """Create FHIR MedicationStatement resource."""
        status_map = {
            MedicationStatus.ACTIVE: "active",
            MedicationStatus.COMPLETED: "completed",
            MedicationStatus.STOPPED: "stopped",
            MedicationStatus.ON_HOLD: "on-hold",
        }

        resource = {
            "resourceType": "MedicationStatement",
            "id": med_id,
            "status": status_map.get(med.status, "active"),
            "medicationCodeableConcept": _concept(med.code, med.display_name),
            "subject": {"reference": f"urn:uuid:{patient_id}"},
            "effectivePeriod": {
                "start": format_date(med.start_date),
                "end": format_date(med.end_date),
            },
            "dosage": [self._dosage(med)],
        }

        if med.indication:
            resource["reasonCode"] = [{"text": med.indication}]
        if med.discontinuation_reason and med.status != MedicationStatus.ACTIVE:
            resource["statusReason"] = [{"text": med.discontinuation_reason}]

        return resource

    def _create_allergy_resource(self, allergy: Allergy, patient_id: str, allergy_id: str) -> dict:
        """Create FHIR AllergyIntolerance resource (US Core AllergyIntolerance)."""
        category_map = {
            AllergyCategory.FOOD: "food",
            AllergyCategory.MEDICATION: "medication",
            AllergyCategory.ENVIRONMENT: "environment",
            AllergyCategory.BIOLOGIC: "biologic",
        }

        resource = {
            "resourceType": "AllergyIntolerance",
            "id": allergy_id,
            "meta": self._profile("us-core-allergyintolerance"),
            "clinicalStatus": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                    "code": allergy.clinical_status,
                }],
            },
            "verificationStatus": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                    "code": allergy.verification_status,
                }],
            },
            "type": allergy.type.value,
            "category": [category_map.get(allergy.category, "environment")],
            "criticality": allergy.criticality,
            "code": _concept(allergy.code, allergy.display_name),
            "patient": {"reference": f"urn:uuid:{patient_id}"},
            "onsetDateTime": format_date(allergy.onset_date),
            "recordedDate": format_date(allergy.recorded_date),
        }

        if allergy.reactions:
            severities = [r.severity.value for r in allergy.reactions
                          if r.severity and r.severity.value in ("mild", "moderate", "severe")]
            resource["reaction"] = [{
                "manifestation": [{"text": r.manifestation} for r in allergy.reactions],
                "severity": max(severities, key=["mild", "moderate", "severe"].index)
                if severities else None,
            }]

        return resource

    def _create_immunization_resource(self, imm: Immunization, patient_id: str, imm_id: str) -> dict:
        """Create FHIR Immunization resource (US Core Immunization)."""
        not_done = imm.status == ImmunizationStatus.NOT_DONE
        coding = _coding(imm.vaccine_code)
        resource = {
            "resourceType": "Immunization",
            "id": imm_id,
            "meta": self._profile("us-core-immunization") if coding else None,
            "status": imm.status.value,
            "vaccineCode": {"coding": [coding] if coding else [], "text": imm.display_name},
            "patient": {"reference": f"urn:uuid:{patient_id}"},
            "occurrenceDateTime": format_date(imm.date),
            "primarySource": True,
        }

        if not_done:
            reason = not_done_reason(imm.status_reason)
            resource["statusReason"] = {
                "coding": [{"system": ACT_REASON_URI, "code": reason[0], "display": reason[1]}]
                if reason else [],
                "text": imm.status_reason or "Not given",
            }
        else:
            if imm.dose_number:
                resource["protocolApplied"] = [{
                    "doseNumberPositiveInt": imm.dose_number,
                    "seriesDosesPositiveInt": imm.series_doses,
                }]
            resource["lotNumber"] = imm.lot_number
            resource["expirationDate"] = format_date(imm.expiration_date)
            if imm.manufacturer:
                resource["manufacturer"] = {"display": imm.manufacturer}
            if imm.site:
                resource["site"] = {"text": imm.site}
            info = route_info(imm.route)
            if info:
                resource["route"] = {"coding": [{"system": V3_ROUTE_URI, "code": info[4]}],
                                     "text": imm.route}
            elif imm.route:
                resource["route"] = {"text": imm.route}
        if imm.notes:
            resource["note"] = [{"text": imm.notes}]

        return resource

    def _create_encounter_resource(self, encounter: Encounter, patient_id: str, enc_id: str) -> dict:
        """Create FHIR Encounter resource (US Core Encounter)."""
        class_map = {
            EncounterClass.AMBULATORY: {"code": "AMB", "display": "ambulatory"},
            EncounterClass.EMERGENCY: {"code": "EMER", "display": "emergency"},
            EncounterClass.INPATIENT: {"code": "IMP", "display": "inpatient encounter"},
            EncounterClass.VIRTUAL: {"code": "VR", "display": "virtual"},
            EncounterClass.HOME: {"code": "HH", "display": "home health"},
        }

        status_map = {
            EncounterStatus.PLANNED: "planned",
            EncounterStatus.IN_PROGRESS: "in-progress",
            EncounterStatus.FINISHED: "finished",
            EncounterStatus.CANCELLED: "cancelled",
        }

        enc_class = class_map.get(encounter.encounter_class, {"code": "AMB", "display": "ambulatory"})
        age_years = relativedelta(as_date(encounter.date),
                                  self._patient.demographics.date_of_birth).years
        cpt = encounter_cpt(encounter.type.value, age_years)

        resource = {
            "resourceType": "Encounter",
            "id": enc_id,
            "meta": self._profile("us-core-encounter"),
            "identifier": [{"system": f"urn:oid:{oread_oid('encounter')}", "value": encounter.id}],
            "status": status_map.get(encounter.status, "finished"),
            "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": enc_class["code"],
                "display": enc_class["display"],
            },
            "type": [{
                "coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": cpt[0],
                            "display": cpt[1]}] if cpt else [],
                "text": encounter.type.value.replace("-", " ").title(),
            }],
            "subject": {"reference": f"urn:uuid:{patient_id}"},
            "period": {
                "start": format_date(encounter.date),
                "end": format_date(encounter.end_date),
            },
            "reasonCode": [{"text": encounter.chief_complaint}],
        }

        if encounter.provider:
            resource["participant"] = [{"individual": {"display": encounter.provider.name}}]

        if encounter.location:
            resource["location"] = [{"location": {"display": encounter.location.name}}]

        return resource

    # (key, LOINC, display, unit text, UCUM, US Core profile)
    # Sources: FHIR R4 Vital Signs profile codes; US Core STU 6.1 vital-sign
    # and pediatric percentile profiles.
    VITAL_SPECS = [
        ("height", "8302-2", "Body height", "cm", "cm", "us-core-body-height"),
        ("weight", "29463-7", "Body weight", "kg", "kg", "us-core-body-weight"),
        ("bmi", "39156-5", "Body mass index (BMI) [Ratio]", "kg/m2", "kg/m2", "us-core-bmi"),
        ("bmi_pct", "59576-9", "Body mass index (BMI) [Percentile] Per age and sex", "%", "%",
         "pediatric-bmi-for-age"),
        ("hc", "9843-4", "Head Occipital-frontal circumference", "cm", "cm",
         "us-core-head-circumference"),
        ("hc_pct", "8289-1", "Head Occipital-frontal circumference Percentile", "%", "%",
         "head-occipital-frontal-circumference-percentile"),
        ("temp", "8310-5", "Body temperature", "degF", "[degF]", "us-core-body-temperature"),
        ("hr", "8867-4", "Heart rate", "beats/minute", "/min", "us-core-heart-rate"),
        ("rr", "9279-1", "Respiratory rate", "breaths/minute", "/min", "us-core-respiratory-rate"),
        ("spo2", "59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry", "%", "%",
         "us-core-pulse-oximetry"),
    ]

    def _vital_base(self, patient_id: str, enc_id: str | None, when, loinc: str,
                    display: str, profile: str) -> dict:
        codings = [{"system": LOINC, "code": loinc, "display": display}]
        if loinc == "59408-5":
            # US Core pulse oximetry requires both codes
            codings.append({"system": LOINC, "code": "2708-6", "display": "Oxygen saturation in Arterial blood"})
        return {
            "resourceType": "Observation",
            "id": generate_uuid(),
            "meta": self._profile(profile),
            "status": "final",
            "category": [{
                "coding": [{
                    "system": OBS_CATEGORY,
                    "code": "vital-signs",
                    "display": "Vital Signs",
                }],
            }],
            "code": {"coding": codings, "text": display},
            "subject": {"reference": f"urn:uuid:{patient_id}"},
            "encounter": {"reference": f"urn:uuid:{enc_id}"} if enc_id else None,
            "effectiveDateTime": format_date(when),
        }

    def _create_vital_observations(self, session, patient_id: str, enc_id: str | None) -> list[dict]:
        """One Observation per measurement; BP as the 85354-9 panel."""
        observations = []
        values = session.values()

        for key, loinc, display, unit_text, ucum, profile in self.VITAL_SPECS:
            value = values[key]
            if value is None:
                continue
            obs = self._vital_base(patient_id, enc_id, session.when, loinc, display, profile)
            obs["valueQuantity"] = _quantity(value, unit_text, ucum)
            observations.append(obs)

        sbp, dbp = values["sbp"], values["dbp"]
        if sbp is not None or dbp is not None:
            obs = self._vital_base(patient_id, enc_id, session.when, "85354-9",
                                   "Blood pressure panel with all children optional",
                                   "us-core-blood-pressure")
            components = []
            for loinc, display, value in (("8480-6", "Systolic blood pressure", sbp),
                                          ("8462-4", "Diastolic blood pressure", dbp)):
                comp = {"code": {"coding": [{"system": LOINC, "code": loinc, "display": display}]}}
                if value is not None:
                    comp["valueQuantity"] = _quantity(value, "mmHg", "mm[Hg]")
                else:
                    comp["dataAbsentReason"] = {"coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
                        "code": "not-performed"}]}
                components.append(comp)
            obs["component"] = components
            observations.append(obs)

        return observations

    def _create_lab_observation(
        self,
        lab: LabResult,
        patient_id: str,
        enc_id: str | None,
        obs_id: str | None = None,
        fallback_date: datetime | None = None,
    ) -> dict:
        """Create FHIR Observation resource for a lab result."""
        interpretation_map = {
            Interpretation.NORMAL: {"code": "N", "display": "Normal"},
            Interpretation.ABNORMAL: {"code": "A", "display": "Abnormal"},
            Interpretation.CRITICAL: {"code": "AA", "display": "Critical abnormal"},
            Interpretation.HIGH: {"code": "H", "display": "High"},
            Interpretation.LOW: {"code": "L", "display": "Low"},
            Interpretation.POSITIVE: {"code": "POS", "display": "Positive"},
            Interpretation.NEGATIVE: {"code": "NEG", "display": "Negative"},
        }
        coding = _coding(lab.code)

        obs = {
            "resourceType": "Observation",
            "id": obs_id or generate_uuid(),
            "meta": self._profile("us-core-observation-lab") if coding else None,
            "status": "final",
            "category": [{
                "coding": [{
                    "system": OBS_CATEGORY,
                    "code": "laboratory",
                    "display": "Laboratory",
                }],
            }],
            "code": {"coding": [coding] if coding else [], "text": lab.display_name},
            "subject": {"reference": f"urn:uuid:{patient_id}"},
            "encounter": {"reference": f"urn:uuid:{enc_id}"} if enc_id else None,
            "effectiveDateTime": format_date(lab.collected_date or lab.resulted_date or fallback_date),
            "issued": format_instant(lab.resulted_date),
        }

        interp = interpretation_map.get(lab.interpretation)
        if interp:
            obs["interpretation"] = [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                    "code": interp["code"],
                    "display": interp["display"],
                }],
            }]

        # Value: numeric -> valueQuantity (UCUM when mappable), else valueString
        number = parse_number(lab.value)
        if number is not None:
            obs["valueQuantity"] = _quantity(number, lab.unit or None, lab_ucum(lab.unit))
        elif lab.value is not None and str(lab.value).strip():
            obs["valueString"] = str(lab.value)
        else:
            obs["dataAbsentReason"] = {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
                "code": "unknown"}]}

        rr = lab.reference_range
        if rr and (rr.low is not None or rr.high is not None or rr.text):
            unit = rr.unit or lab.unit
            ucum = lab_ucum(unit)
            obs["referenceRange"] = [{
                "low": _quantity(rr.low, unit, ucum) if rr.low is not None else None,
                "high": _quantity(rr.high, unit, ucum) if rr.high is not None else None,
                "text": rr.text,
            }]

        return obs

    def _create_diagnostic_report(
        self,
        imaging: ImagingResult,
        patient_id: str,
        enc_id: str | None,
        report_id: str | None = None,
    ) -> dict:
        """Create FHIR DiagnosticReport resource for imaging result."""
        status_map = {
            ResultStatus.PRELIMINARY: "preliminary",
            ResultStatus.FINAL: "final",
            ResultStatus.AMENDED: "amended",
            ResultStatus.CANCELLED: "cancelled",
        }
        report_text = f"FINDINGS:\n{imaging.findings}\n\nIMPRESSION:\n{imaging.impression}\n"

        report = {
            "resourceType": "DiagnosticReport",
            "id": report_id or generate_uuid(),
            "status": status_map.get(imaging.status, "final"),
            "category": [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                    "code": "RAD",
                    "display": "Radiology",
                }],
            }],
            "code": _concept(imaging.code, imaging.display_name),
            "subject": {"reference": f"urn:uuid:{patient_id}"},
            "encounter": {"reference": f"urn:uuid:{enc_id}"} if enc_id else None,
            "effectiveDateTime": format_date(imaging.performed_date),
            "issued": format_instant(imaging.reported_date),
            "conclusion": imaging.impression,
            "presentedForm": [{
                "contentType": "text/plain",
                "data": base64.b64encode(report_text.encode("utf-8")).decode("ascii"),
                "title": imaging.display_name,
            }],
            "performer": [{"display": name} for name in
                          (imaging.radiologist, imaging.performing_facility) if name],
        }

        return report

    def _create_communication_resource(
        self,
        message: PatientMessage,
        patient_id: str,
        encounter_id: str | None,
        comm_id: str
    ) -> dict:
        """Create FHIR Communication resource for patient messages."""
        # Using http://terminology.hl7.org/CodeSystem/communication-category
        category_map = {
            MessageCategory.REFILL_REQUEST: {"code": "prescription", "display": "Prescription"},
            MessageCategory.CLINICAL_QUESTION: {"code": "notification", "display": "Notification"},
            MessageCategory.APPOINTMENT_REQUEST: {"code": "reminder", "display": "Reminder"},
            MessageCategory.LAB_RESULT_QUESTION: {"code": "notification", "display": "Notification"},
            MessageCategory.FOLLOW_UP: {"code": "notification", "display": "Notification"},
            MessageCategory.AVOID_VISIT: {"code": "notification", "display": "Notification"},
            MessageCategory.SCHOOL_FORM: {"code": "instruction", "display": "Instruction"},
            MessageCategory.REFERRAL_STATUS: {"code": "notification", "display": "Notification"},
            MessageCategory.OTHER: {"code": "notification", "display": "Notification"},
        }

        medium_map = {
            MessageMedium.PORTAL: {"code": "WRITTEN", "display": "Written"},
            MessageMedium.PHONE: {"code": "PHONE", "display": "Telephone"},
            MessageMedium.EMAIL: {"code": "EMAILWRIT", "display": "Email"},
            MessageMedium.FAX: {"code": "FAXWRIT", "display": "Fax"},
            MessageMedium.SMS: {"code": "SMSWRIT", "display": "SMS"},
        }

        status_map = {
            MessageStatus.COMPLETED: "completed",
            MessageStatus.IN_PROGRESS: "in-progress",
            MessageStatus.NOT_DONE: "not-done",
        }

        cat_info = category_map.get(message.category, {"code": "notification", "display": "Notification"})
        med_info = medium_map.get(message.medium, {"code": "WRITTEN", "display": "Written"})

        resource = {
            "resourceType": "Communication",
            "id": comm_id,
            "status": status_map.get(message.status, "completed"),
            "category": [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/communication-category",
                    "code": cat_info["code"],
                    "display": cat_info["display"],
                }],
                "text": message.category.value.replace("-", " ").title(),
            }],
            "medium": [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ParticipationMode",
                    "code": med_info["code"],
                    "display": med_info["display"],
                }],
                "text": message.medium.value.title(),
            }],
            "subject": {"reference": f"urn:uuid:{patient_id}"},
            "encounter": {"reference": f"urn:uuid:{encounter_id}"} if encounter_id else None,
            "sent": format_date(message.sent_datetime),
            "payload": [{"contentString": message.message_body}],
            "sender": {"display": message.sender_name},
            "recipient": [{"display": message.recipient_name}],
            "topic": {"text": message.subject} if message.subject else None,
            "reasonCode": [{"text": message.related_condition}] if message.related_condition else None,
        }

        if message.reply_body and message.reply_datetime:
            resource["note"] = [{
                "authorString": message.replier_name or "Office Staff",
                "time": format_date(message.reply_datetime),
                "text": message.reply_body,
            }]
            resource["received"] = format_date(message.reply_datetime)

        return resource


def export_to_fhir(patient: Patient, output_path=None) -> dict[str, Any]:
    """Convenience function to export a patient to FHIR.

    Args:
        patient: The patient to export
        output_path: Optional path to write the FHIR bundle JSON

    Returns:
        FHIR Bundle as a dictionary
    """
    from pathlib import Path

    exporter = FHIRExporter()
    bundle = exporter.export(patient)

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle, indent=2, default=str))

    return bundle


def export_to_fhir_json(patient: Patient, indent: int = 2) -> str:
    """Convenience function to export a patient to FHIR JSON string."""
    exporter = FHIRExporter()
    return exporter.export_json(patient, indent=indent)
