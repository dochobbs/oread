"""
FHIR R4 Exporter for SynthPatient.

Converts internal Patient model to FHIR R4 Bundle format.
Reference: https://www.hl7.org/fhir/R4/
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from src.models import (
    Patient,
    Encounter,
    Condition,
    Medication,
    Allergy,
    Immunization,
    Observation,
    LabResult,
    LabPanel,
    Procedure,
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
)


def generate_uuid() -> str:
    """Generate a UUID for FHIR resources."""
    return str(uuid4())


def format_date(d: date | datetime | None) -> str | None:
    """Format a date for FHIR."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.isoformat()
    return d.isoformat()


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
            
            # Observations from this encounter (vitals)
            if encounter.vital_signs:
                for obs in self._create_vital_observations(encounter, patient_id, enc_id):
                    obs_id = generate_uuid()
                    entries.append(self._bundle_entry(obs, obs_id))
        
        # Growth observations
        for growth in patient.growth_data:
            for obs in self._create_growth_observations(growth, patient_id, encounter_id_map.get(growth.encounter_id)):
                obs_id = generate_uuid()
                entries.append(self._bundle_entry(obs, obs_id))

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
            "timestamp": datetime.now().isoformat(),
            "entry": entries,
            "total": len(entries),
        }
        
        return bundle
    
    def export_json(self, patient: Patient, indent: int = 2) -> str:
        """Export to JSON string."""
        bundle = self.export(patient)
        return json.dumps(bundle, indent=indent, default=str)
    
    def _bundle_entry(self, resource: dict, resource_id: str) -> dict:
        """Wrap a resource in a bundle entry."""
        return {
            "fullUrl": f"{self.base_url}{resource_id}",
            "resource": resource,
        }
    
    def _create_patient_resource(self, patient: Patient, patient_id: str) -> dict:
        """Create FHIR Patient resource."""
        demo = patient.demographics
        
        # Name
        name = {
            "use": "official",
            "family": demo.family_name,
            "given": demo.given_names,
        }
        
        # Gender mapping
        gender_map = {
            Sex.MALE: "male",
            Sex.FEMALE: "female",
            Sex.INTERSEX: "other",
            Sex.UNKNOWN: "unknown",
        }
        
        # Address
        address = {
            "use": "home",
            "type": "physical",
            "line": [demo.address.line1],
            "city": demo.address.city,
            "state": demo.address.state,
            "postalCode": demo.address.postal_code,
            "country": demo.address.country,
        }
        if demo.address.line2:
            address["line"].append(demo.address.line2)
        
        # Telecom
        telecom = []
        if demo.phone:
            telecom.append({
                "system": "phone",
                "value": demo.phone,
                "use": "home",
            })
        if demo.email:
            telecom.append({
                "system": "email",
                "value": demo.email,
            })
        
        # Contact (emergency contact / guardian)
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
            "identifier": [{
                "system": "urn:oread:id",
                "value": patient.id,
            }],
            "name": [name],
            "gender": gender_map.get(demo.sex_at_birth, "unknown"),
            "birthDate": format_date(demo.date_of_birth),
            "address": [address],
            "telecom": telecom,
            "contact": contact if contact else None,
            "communication": [{
                "language": {
                    "coding": [{
                        "system": "urn:ietf:bcp:47",
                        "code": "en" if demo.preferred_language == "English" else demo.preferred_language.lower()[:2],
                        "display": demo.preferred_language,
                    }],
                },
                "preferred": True,
            }],
        }
        
        # Extensions for race/ethnicity
        extensions = []
        if demo.race:
            for race in demo.race:
                extensions.append({
                    "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race",
                    "extension": [{
                        "url": "text",
                        "valueString": race,
                    }],
                })
        if demo.ethnicity:
            extensions.append({
                "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity",
                "extension": [{
                    "url": "text",
                    "valueString": demo.ethnicity,
                }],
            })
        
        if extensions:
            resource["extension"] = extensions
        
        # Remove None values
        return {k: v for k, v in resource.items() if v is not None}
    
    def _create_condition_resource(self, condition: Condition, patient_id: str, condition_id: str) -> dict:
        """Create FHIR Condition resource."""
        # Clinical status mapping
        status_map = {
            ConditionStatus.ACTIVE: "active",
            ConditionStatus.RECURRENCE: "recurrence",
            ConditionStatus.RELAPSE: "relapse",
            ConditionStatus.INACTIVE: "inactive",
            ConditionStatus.REMISSION: "remission",
            ConditionStatus.RESOLVED: "resolved",
        }
        
        resource = {
            "resourceType": "Condition",
            "id": condition_id,
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
            "code": {
                "coding": [{
                    "system": condition.code.system,
                    "code": condition.code.code,
                    "display": condition.code.display,
                }],
                "text": condition.display_name,
            },
            "subject": {
                "reference": f"urn:uuid:{patient_id}",
            },
            "onsetDateTime": format_date(condition.onset_date),
        }
        
        if condition.severity:
            resource["severity"] = {
                "coding": [{
                    "system": "http://snomed.info/sct",
                    "code": {"mild": "255604002", "moderate": "6736007", "severe": "24484000"}.get(condition.severity.value, ""),
                    "display": condition.severity.value.title(),
                }],
            }
        
        if condition.abatement_date:
            resource["abatementDateTime"] = format_date(condition.abatement_date)
        
        if condition.notes:
            resource["note"] = [{"text": condition.notes}]
        
        return resource
    
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
            "medicationCodeableConcept": {
                "coding": [{
                    "system": med.code.system,
                    "code": med.code.code,
                    "display": med.code.display,
                }],
                "text": med.display_name,
            },
            "subject": {
                "reference": f"urn:uuid:{patient_id}",
            },
            "effectivePeriod": {
                "start": format_date(med.start_date),
            },
            "dosage": [{
                "text": f"{med.dose_quantity} {med.dose_unit} {med.frequency}",
                "route": {
                    "text": med.route,
                },
                "doseAndRate": [{
                    "doseQuantity": {
                        "value": float(med.dose_quantity) if med.dose_quantity.replace(".", "").isdigit() else None,
                        "unit": med.dose_unit,
                    },
                }],
            }],
        }
        
        if med.end_date:
            resource["effectivePeriod"]["end"] = format_date(med.end_date)
        
        if med.indication:
            resource["reasonCode"] = [{"text": med.indication}]
        
        return resource
    
    def _create_allergy_resource(self, allergy: Allergy, patient_id: str, allergy_id: str) -> dict:
        """Create FHIR AllergyIntolerance resource."""
        category_map = {
            AllergyCategory.FOOD: "food",
            AllergyCategory.MEDICATION: "medication",
            AllergyCategory.ENVIRONMENT: "environment",
            AllergyCategory.BIOLOGIC: "biologic",
        }
        
        resource = {
            "resourceType": "AllergyIntolerance",
            "id": allergy_id,
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
            "code": {
                "text": allergy.display_name,
            },
            "patient": {
                "reference": f"urn:uuid:{patient_id}",
            },
        }
        
        if allergy.code:
            resource["code"]["coding"] = [{
                "system": allergy.code.system,
                "code": allergy.code.code,
                "display": allergy.code.display,
            }]
        
        if allergy.reactions:
            resource["reaction"] = [{
                "manifestation": [{"text": r.manifestation} for r in allergy.reactions],
            }]
        
        return resource
    
    def _create_immunization_resource(self, imm: Immunization, patient_id: str, imm_id: str) -> dict:
        """Create FHIR Immunization resource."""
        resource = {
            "resourceType": "Immunization",
            "id": imm_id,
            "status": imm.status.value,
            "vaccineCode": {
                "coding": [{
                    "system": imm.vaccine_code.system,
                    "code": imm.vaccine_code.code,
                    "display": imm.vaccine_code.display,
                }],
                "text": imm.display_name,
            },
            "patient": {
                "reference": f"urn:uuid:{patient_id}",
            },
            "occurrenceDateTime": format_date(imm.date),
            "primarySource": True,
        }
        
        if imm.dose_number:
            resource["protocolApplied"] = [{
                "doseNumberPositiveInt": imm.dose_number,
            }]
            if imm.series_doses:
                resource["protocolApplied"][0]["seriesDosesPositiveInt"] = imm.series_doses
        
        if imm.lot_number:
            resource["lotNumber"] = imm.lot_number
        
        if imm.site:
            resource["site"] = {"text": imm.site}
        
        return resource
    
    def _create_encounter_resource(self, encounter: Encounter, patient_id: str, enc_id: str) -> dict:
        """Create FHIR Encounter resource."""
        class_map = {
            EncounterClass.AMBULATORY: {"code": "AMB", "display": "ambulatory"},
            EncounterClass.EMERGENCY: {"code": "EMER", "display": "emergency"},
            EncounterClass.INPATIENT: {"code": "IMP", "display": "inpatient"},
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
        
        resource = {
            "resourceType": "Encounter",
            "id": enc_id,
            "status": status_map.get(encounter.status, "finished"),
            "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": enc_class["code"],
                "display": enc_class["display"],
            },
            "type": [{
                "text": encounter.type.value.replace("-", " ").title(),
            }],
            "subject": {
                "reference": f"urn:uuid:{patient_id}",
            },
            "period": {
                "start": format_date(encounter.date),
            },
            "reasonCode": [{"text": encounter.chief_complaint}],
        }
        
        if encounter.end_date:
            resource["period"]["end"] = format_date(encounter.end_date)
        
        if encounter.provider:
            resource["participant"] = [{
                "individual": {
                    "display": encounter.provider.name,
                },
            }]
        
        if encounter.location:
            resource["location"] = [{
                "location": {
                    "display": encounter.location.name,
                },
            }]
        
        return resource
    
    def _create_vital_observations(self, encounter: Encounter, patient_id: str, enc_id: str) -> list[dict]:
        """Create FHIR Observation resources for vital signs."""
        observations = []
        vs = encounter.vital_signs
        
        if not vs:
            return observations
        
        # LOINC codes for vitals
        vital_configs = [
            ("8310-5", "Body temperature", vs.temperature_f, "degF", "[degF]"),
            ("8867-4", "Heart rate", vs.heart_rate, "beats/minute", "/min"),
            ("9279-1", "Respiratory rate", vs.respiratory_rate, "breaths/minute", "/min"),
            ("8480-6", "Systolic blood pressure", vs.blood_pressure_systolic, "mmHg", "mm[Hg]"),
            ("8462-4", "Diastolic blood pressure", vs.blood_pressure_diastolic, "mmHg", "mm[Hg]"),
            ("2708-6", "Oxygen saturation", vs.oxygen_saturation, "%", "%"),
            ("29463-7", "Body weight", vs.weight_kg, "kg", "kg"),
            ("8302-2", "Body height", vs.height_cm, "cm", "cm"),
            ("9843-4", "Head circumference", vs.head_circumference_cm, "cm", "cm"),
        ]
        
        for loinc, display, value, unit_display, unit_code in vital_configs:
            if value is not None:
                obs = {
                    "resourceType": "Observation",
                    "status": "final",
                    "category": [{
                        "coding": [{
                            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "code": "vital-signs",
                            "display": "Vital Signs",
                        }],
                    }],
                    "code": {
                        "coding": [{
                            "system": "http://loinc.org",
                            "code": loinc,
                            "display": display,
                        }],
                    },
                    "subject": {
                        "reference": f"urn:uuid:{patient_id}",
                    },
                    "encounter": {
                        "reference": f"urn:uuid:{enc_id}",
                    },
                    "effectiveDateTime": format_date(vs.date),
                    "valueQuantity": {
                        "value": value,
                        "unit": unit_display,
                        "system": "http://unitsofmeasure.org",
                        "code": unit_code,
                    },
                }
                observations.append(obs)
        
        return observations
    
    def _create_growth_observations(self, growth, patient_id: str, enc_id: str | None) -> list[dict]:
        """Create FHIR Observation resources for growth measurements."""
        observations = []
        
        # Weight with percentile
        if growth.weight_kg:
            obs = {
                "resourceType": "Observation",
                "status": "final",
                "category": [{
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "vital-signs",
                    }],
                }],
                "code": {
                    "coding": [{
                        "system": "http://loinc.org",
                        "code": "29463-7",
                        "display": "Body weight",
                    }],
                },
                "subject": {"reference": f"urn:uuid:{patient_id}"},
                "effectiveDateTime": format_date(growth.date),
                "valueQuantity": {
                    "value": growth.weight_kg,
                    "unit": "kg",
                    "system": "http://unitsofmeasure.org",
                    "code": "kg",
                },
            }
            if enc_id:
                obs["encounter"] = {"reference": f"urn:uuid:{enc_id}"}
            observations.append(obs)

        return observations

    def _create_communication_resource(
        self,
        message: PatientMessage,
        patient_id: str,
        encounter_id: str | None,
        comm_id: str
    ) -> dict:
        """Create FHIR Communication resource for patient messages."""
        # Map internal category to FHIR Communication category codes
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

        # Map medium to FHIR ContactPoint system
        medium_map = {
            MessageMedium.PORTAL: {"code": "WRITTEN", "display": "Written"},
            MessageMedium.PHONE: {"code": "PHONE", "display": "Telephone"},
            MessageMedium.EMAIL: {"code": "EMAILWRIT", "display": "Email"},
            MessageMedium.FAX: {"code": "FAX", "display": "Fax"},
            MessageMedium.SMS: {"code": "SMS", "display": "SMS"},
        }

        # Map status
        status_map = {
            MessageStatus.COMPLETED: "completed",
            MessageStatus.IN_PROGRESS: "in-progress",
            MessageStatus.NOT_DONE: "not-done",
        }

        cat_info = category_map.get(message.category, {"code": "notification", "display": "Notification"})
        med_info = medium_map.get(message.medium, {"code": "WRITTEN", "display": "Written"})

        # Build the resource
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
            "subject": {
                "reference": f"urn:uuid:{patient_id}",
            },
            "sent": format_date(message.sent_datetime),
            "payload": [{
                "contentString": message.message_body,
            }],
        }

        # Add sender info
        resource["sender"] = {
            "display": message.sender_name,
        }

        # Add recipient info
        resource["recipient"] = [{
            "display": message.recipient_name,
        }]

        # Add encounter reference if available
        if encounter_id:
            resource["encounter"] = {
                "reference": f"urn:uuid:{encounter_id}",
            }

        # Add topic/subject
        if message.subject:
            resource["topic"] = {
                "text": message.subject,
            }

        # Add reply as a note if present
        if message.reply_body and message.reply_datetime:
            resource["note"] = [{
                "authorString": message.replier_name or "Office Staff",
                "time": format_date(message.reply_datetime),
                "text": message.reply_body,
            }]
            resource["received"] = format_date(message.reply_datetime)

        # Add related condition as reasonCode if present
        if message.related_condition:
            resource["reasonCode"] = [{
                "text": message.related_condition,
            }]

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
