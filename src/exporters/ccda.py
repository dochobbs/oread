"""
CCDA (Consolidated Clinical Document Architecture) Exporter for Oread.

Exports patient data to CDA R2 format following HL7 C-CDA 2.1 templates.
Reference: https://www.hl7.org/ccdasearch/
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree as ET
from xml.dom import minidom

from src.models import (
    Patient,
    Encounter,
    Condition,
    Medication,
    Allergy,
    Immunization,
    Sex,
)


def generate_uuid() -> str:
    """Generate a UUID for CCDA document IDs."""
    return str(uuid4())


def format_datetime(dt: datetime | None) -> str:
    """Format datetime for CCDA (YYYYMMDDHHMMSS)."""
    if dt is None:
        return ""
    return dt.strftime("%Y%m%d%H%M%S")


def format_date(dt: datetime | None) -> str:
    """Format date for CCDA (YYYYMMDD)."""
    if dt is None:
        return ""
    if hasattr(dt, 'strftime'):
        return dt.strftime("%Y%m%d")
    return str(dt).replace("-", "")


class CCDAExporter:
    """
    Exports Patient data to C-CDA 2.1 format.

    Generates a Continuity of Care Document (CCD) containing:
    - Patient demographics
    - Problem list (conditions)
    - Medications
    - Allergies
    - Immunizations
    - Encounters
    - Vital signs
    """

    # XML namespaces
    NS = {
        "": "urn:hl7-org:v3",
        "xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "sdtc": "urn:hl7-org:sdtc",
    }

    # Template OIDs for C-CDA 2.1
    TEMPLATES = {
        "ccd": "2.16.840.1.113883.10.20.22.1.2",
        "problems": "2.16.840.1.113883.10.20.22.2.5.1",
        "medications": "2.16.840.1.113883.10.20.22.2.1.1",
        "allergies": "2.16.840.1.113883.10.20.22.2.6.1",
        "immunizations": "2.16.840.1.113883.10.20.22.2.2.1",
        "encounters": "2.16.840.1.113883.10.20.22.2.22.1",
        "vitals": "2.16.840.1.113883.10.20.22.2.4.1",
    }

    def __init__(self):
        self.document_id = generate_uuid()

    def export(self, patient: Patient) -> str:
        """
        Export a patient to C-CDA XML format.

        Returns XML string.
        """
        # Create root element with namespaces
        root = ET.Element("ClinicalDocument")
        root.set("xmlns", self.NS[""])
        root.set("xmlns:xsi", self.NS["xsi"])
        root.set("xmlns:sdtc", self.NS["sdtc"])

        # Add header
        self._add_header(root, patient)

        # Add record target (patient demographics)
        self._add_record_target(root, patient)

        # Add author
        self._add_author(root)

        # Add custodian
        self._add_custodian(root)

        # Add document body with sections
        component = ET.SubElement(root, "component")
        structured_body = ET.SubElement(component, "structuredBody")

        # Add sections
        self._add_problems_section(structured_body, patient)
        self._add_medications_section(structured_body, patient)
        self._add_allergies_section(structured_body, patient)
        self._add_immunizations_section(structured_body, patient)
        self._add_encounters_section(structured_body, patient)
        self._add_vitals_section(structured_body, patient)

        # Convert to string with pretty printing
        xml_str = ET.tostring(root, encoding="unicode")
        dom = minidom.parseString(xml_str)
        return dom.toprettyxml(indent="  ")

    def _add_header(self, root: ET.Element, patient: Patient) -> None:
        """Add CDA header elements."""
        # Realm code (US)
        realm = ET.SubElement(root, "realmCode")
        realm.set("code", "US")

        # Type ID
        type_id = ET.SubElement(root, "typeId")
        type_id.set("root", "2.16.840.1.113883.1.3")
        type_id.set("extension", "POCD_HD000040")

        # Template ID for CCD
        template = ET.SubElement(root, "templateId")
        template.set("root", self.TEMPLATES["ccd"])
        template.set("extension", "2015-08-01")

        # Document ID
        doc_id = ET.SubElement(root, "id")
        doc_id.set("root", self.document_id)

        # Document code (CCD)
        code = ET.SubElement(root, "code")
        code.set("code", "34133-9")
        code.set("codeSystem", "2.16.840.1.113883.6.1")
        code.set("codeSystemName", "LOINC")
        code.set("displayName", "Summarization of Episode Note")

        # Title
        title = ET.SubElement(root, "title")
        title.text = f"Continuity of Care Document - {patient.demographics.full_name}"

        # Effective time
        effective_time = ET.SubElement(root, "effectiveTime")
        effective_time.set("value", format_datetime(datetime.now()))

        # Confidentiality
        conf = ET.SubElement(root, "confidentialityCode")
        conf.set("code", "N")
        conf.set("codeSystem", "2.16.840.1.113883.5.25")

        # Language
        lang = ET.SubElement(root, "languageCode")
        lang.set("code", "en-US")

    def _add_record_target(self, root: ET.Element, patient: Patient) -> None:
        """Add patient demographics."""
        record_target = ET.SubElement(root, "recordTarget")
        patient_role = ET.SubElement(record_target, "patientRole")

        # Patient ID
        pid = ET.SubElement(patient_role, "id")
        pid.set("root", "urn:oread:patient")
        pid.set("extension", patient.id)

        # Address
        if patient.demographics.address:
            addr = ET.SubElement(patient_role, "addr")
            addr.set("use", "HP")  # Home primary

            street = ET.SubElement(addr, "streetAddressLine")
            street.text = patient.demographics.address.line1

            city = ET.SubElement(addr, "city")
            city.text = patient.demographics.address.city

            state = ET.SubElement(addr, "state")
            state.text = patient.demographics.address.state

            postal = ET.SubElement(addr, "postalCode")
            postal.text = patient.demographics.address.postal_code

            country = ET.SubElement(addr, "country")
            country.text = patient.demographics.address.country or "US"

        # Phone
        if patient.demographics.phone:
            telecom = ET.SubElement(patient_role, "telecom")
            telecom.set("use", "HP")
            telecom.set("value", f"tel:{patient.demographics.phone}")

        # Patient element
        pat = ET.SubElement(patient_role, "patient")

        # Name
        name = ET.SubElement(pat, "name")
        name.set("use", "L")  # Legal

        for given in patient.demographics.given_names:
            given_el = ET.SubElement(name, "given")
            given_el.text = given

        family = ET.SubElement(name, "family")
        family.text = patient.demographics.family_name

        # Gender
        gender = ET.SubElement(pat, "administrativeGenderCode")
        gender_code = "M" if patient.demographics.sex_at_birth == Sex.MALE else "F"
        gender.set("code", gender_code)
        gender.set("codeSystem", "2.16.840.1.113883.5.1")

        # Birth time
        birth = ET.SubElement(pat, "birthTime")
        birth.set("value", format_date(patient.demographics.date_of_birth))

        # Race
        if patient.demographics.race:
            race = ET.SubElement(pat, "raceCode")
            race.set("displayName", patient.demographics.race[0])
            race.set("codeSystem", "2.16.840.1.113883.6.238")

        # Ethnicity
        if patient.demographics.ethnicity:
            ethnicity = ET.SubElement(pat, "ethnicGroupCode")
            ethnicity.set("displayName", patient.demographics.ethnicity)
            ethnicity.set("codeSystem", "2.16.840.1.113883.6.238")

        # Language
        lang_comm = ET.SubElement(pat, "languageCommunication")
        lang_code = ET.SubElement(lang_comm, "languageCode")
        lang_code.set("code", patient.demographics.preferred_language or "en")

    def _add_author(self, root: ET.Element) -> None:
        """Add document author."""
        author = ET.SubElement(root, "author")

        time = ET.SubElement(author, "time")
        time.set("value", format_datetime(datetime.now()))

        assigned_author = ET.SubElement(author, "assignedAuthor")

        author_id = ET.SubElement(assigned_author, "id")
        author_id.set("root", "urn:oread:author")
        author_id.set("extension", "oread-system")

        represented_org = ET.SubElement(assigned_author, "representedOrganization")
        org_name = ET.SubElement(represented_org, "name")
        org_name.text = "Oread Synthetic Patient Generator"

    def _add_custodian(self, root: ET.Element) -> None:
        """Add document custodian."""
        custodian = ET.SubElement(root, "custodian")
        assigned_custodian = ET.SubElement(custodian, "assignedCustodian")
        represented_org = ET.SubElement(assigned_custodian, "representedCustodianOrganization")

        org_id = ET.SubElement(represented_org, "id")
        org_id.set("root", "urn:oread:custodian")

        org_name = ET.SubElement(represented_org, "name")
        org_name.text = "Oread Synthetic Patient Generator"

    def _add_section(self, parent: ET.Element, template_oid: str,
                     loinc_code: str, title: str) -> ET.Element:
        """Add a standard section structure."""
        component = ET.SubElement(parent, "component")
        section = ET.SubElement(component, "section")

        # Template ID
        template = ET.SubElement(section, "templateId")
        template.set("root", template_oid)

        # Code
        code = ET.SubElement(section, "code")
        code.set("code", loinc_code)
        code.set("codeSystem", "2.16.840.1.113883.6.1")
        code.set("codeSystemName", "LOINC")

        # Title
        title_el = ET.SubElement(section, "title")
        title_el.text = title

        return section

    def _add_problems_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add problems/conditions section."""
        section = self._add_section(
            parent,
            self.TEMPLATES["problems"],
            "11450-4",
            "Problem List"
        )

        # Narrative text
        text = ET.SubElement(section, "text")
        if patient.problem_list:
            ul = ET.SubElement(text, "list")
            for condition in patient.problem_list:
                li = ET.SubElement(ul, "item")
                li.text = f"{condition.display_name} - {condition.clinical_status.value}"
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No known problems"

        # Entries
        for condition in patient.problem_list:
            entry = ET.SubElement(section, "entry")
            act = ET.SubElement(entry, "act")
            act.set("classCode", "ACT")
            act.set("moodCode", "EVN")

            # Problem concern
            entry_rel = ET.SubElement(act, "entryRelationship")
            entry_rel.set("typeCode", "SUBJ")

            obs = ET.SubElement(entry_rel, "observation")
            obs.set("classCode", "OBS")
            obs.set("moodCode", "EVN")

            # Problem code
            code = ET.SubElement(obs, "code")
            code.set("code", "55607006")
            code.set("codeSystem", "2.16.840.1.113883.6.96")
            code.set("displayName", "Problem")

            # Value (the actual condition)
            value = ET.SubElement(obs, "value")
            value.set("{%s}type" % self.NS["xsi"], "CD")
            if condition.code:
                value.set("code", condition.code.code)
                value.set("codeSystem", "2.16.840.1.113883.6.90")  # ICD-10-CM
            value.set("displayName", condition.display_name)

    def _add_medications_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add medications section."""
        section = self._add_section(
            parent,
            self.TEMPLATES["medications"],
            "10160-0",
            "Medications"
        )

        text = ET.SubElement(section, "text")
        active_meds = [m for m in patient.medication_list if m.status.value == "active"]

        if active_meds:
            ul = ET.SubElement(text, "list")
            for med in active_meds:
                li = ET.SubElement(ul, "item")
                li.text = f"{med.display_name}"
                if med.dosage:
                    li.text += f" - {med.dosage}"
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No current medications"

    def _add_allergies_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add allergies section."""
        section = self._add_section(
            parent,
            self.TEMPLATES["allergies"],
            "48765-2",
            "Allergies and Adverse Reactions"
        )

        text = ET.SubElement(section, "text")
        if patient.allergy_list:
            ul = ET.SubElement(text, "list")
            for allergy in patient.allergy_list:
                li = ET.SubElement(ul, "item")
                reaction = allergy.reactions[0] if allergy.reactions else "Unknown reaction"
                li.text = f"{allergy.allergen} - {reaction}"
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No known allergies"

    def _add_immunizations_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add immunizations section."""
        section = self._add_section(
            parent,
            self.TEMPLATES["immunizations"],
            "11369-6",
            "Immunizations"
        )

        text = ET.SubElement(section, "text")
        if patient.immunization_record:
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Vaccine", "Date", "Dose"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for imm in patient.immunization_record:
                tr = ET.SubElement(tbody, "tr")

                td1 = ET.SubElement(tr, "td")
                td1.text = imm.display_name

                td2 = ET.SubElement(tr, "td")
                td2.text = str(imm.date) if imm.date else ""

                td3 = ET.SubElement(tr, "td")
                td3.text = str(imm.dose_number) if imm.dose_number else ""
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No immunization records"

    def _add_encounters_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add encounters section."""
        section = self._add_section(
            parent,
            self.TEMPLATES["encounters"],
            "46240-8",
            "Encounters"
        )

        text = ET.SubElement(section, "text")
        if patient.encounters:
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Date", "Type", "Chief Complaint"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for enc in sorted(patient.encounters, key=lambda e: e.date, reverse=True)[:20]:
                tr = ET.SubElement(tbody, "tr")

                td1 = ET.SubElement(tr, "td")
                td1.text = str(enc.date.date()) if enc.date else ""

                td2 = ET.SubElement(tr, "td")
                td2.text = enc.type.value.replace("-", " ").title()

                td3 = ET.SubElement(tr, "td")
                td3.text = enc.chief_complaint or ""
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No encounter records"

    def _add_vitals_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add vital signs section."""
        section = self._add_section(
            parent,
            self.TEMPLATES["vitals"],
            "8716-3",
            "Vital Signs"
        )

        text = ET.SubElement(section, "text")

        # Get most recent vitals from encounters
        recent_vitals = None
        for enc in sorted(patient.encounters, key=lambda e: e.date, reverse=True):
            if enc.vital_signs:
                recent_vitals = enc.vital_signs
                break

        if recent_vitals:
            ul = ET.SubElement(text, "list")

            if recent_vitals.weight_kg:
                li = ET.SubElement(ul, "item")
                li.text = f"Weight: {recent_vitals.weight_kg} kg"

            if recent_vitals.height_cm:
                li = ET.SubElement(ul, "item")
                li.text = f"Height: {recent_vitals.height_cm} cm"

            if recent_vitals.heart_rate:
                li = ET.SubElement(ul, "item")
                li.text = f"Heart Rate: {recent_vitals.heart_rate} bpm"

            if recent_vitals.blood_pressure_systolic:
                li = ET.SubElement(ul, "item")
                li.text = f"Blood Pressure: {recent_vitals.blood_pressure_systolic}/{recent_vitals.blood_pressure_diastolic} mmHg"

            if recent_vitals.temperature_f:
                li = ET.SubElement(ul, "item")
                li.text = f"Temperature: {recent_vitals.temperature_f}°F"
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No vital signs recorded"


def export_to_ccda(patient: Patient, output_path: Path | None = None) -> str:
    """
    Export a patient to C-CDA XML format.

    Args:
        patient: Patient to export
        output_path: Optional path to write XML file

    Returns:
        C-CDA XML string
    """
    exporter = CCDAExporter()
    xml_content = exporter.export(patient)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(xml_content)

    return xml_content
