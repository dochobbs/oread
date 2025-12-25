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
    NS_HL7 = "urn:hl7-org:v3"
    NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
    NS_SDTC = "urn:hl7-org:sdtc"

    NS = {
        "": NS_HL7,
        "xsi": NS_XSI,
        "sdtc": NS_SDTC,
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
        # Register namespaces so ElementTree uses proper prefixes
        ET.register_namespace('', self.NS_HL7)
        ET.register_namespace('xsi', self.NS_XSI)
        ET.register_namespace('sdtc', self.NS_SDTC)

    def export(self, patient: Patient) -> str:
        """
        Export a patient to C-CDA XML format.

        Returns XML string.
        """
        # Create root element with proper namespace
        root = ET.Element(f"{{{self.NS_HL7}}}ClinicalDocument")
        # Explicitly declare xsi and sdtc namespaces as attributes
        # (the default namespace is handled automatically by ElementTree)
        root.set(f"{{{self.NS_XSI}}}schemaLocation",
                 "urn:hl7-org:v3 CDA.xsd")

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
            value.set(f"{{{self.NS_XSI}}}type", "CD")
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
        """Add immunizations section with structured entries."""
        section = self._add_section(
            parent,
            self.TEMPLATES["immunizations"],
            "11369-6",
            "Immunizations"
        )

        text = ET.SubElement(section, "text")
        if patient.immunization_record:
            # Narrative table
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Vaccine", "Date", "Dose", "Lot #", "Manufacturer"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for imm in patient.immunization_record:
                tr = ET.SubElement(tbody, "tr")
                td = ET.SubElement(tr, "td")
                td.text = imm.display_name
                td = ET.SubElement(tr, "td")
                td.text = str(imm.date) if imm.date else ""
                td = ET.SubElement(tr, "td")
                td.text = str(imm.dose_number) if imm.dose_number else ""
                td = ET.SubElement(tr, "td")
                td.text = imm.lot_number or ""
                td = ET.SubElement(tr, "td")
                td.text = imm.manufacturer or ""

            # Structured entries for each immunization
            for imm in patient.immunization_record:
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                subst_admin = ET.SubElement(entry, "substanceAdministration")
                subst_admin.set("classCode", "SBADM")
                subst_admin.set("moodCode", "EVN")
                subst_admin.set("negationInd", "false")

                # Immunization activity template
                template = ET.SubElement(subst_admin, "templateId")
                template.set("root", "2.16.840.1.113883.10.20.22.4.52")
                template.set("extension", "2015-08-01")

                imm_id = ET.SubElement(subst_admin, "id")
                imm_id.set("root", generate_uuid())

                status = ET.SubElement(subst_admin, "statusCode")
                status.set("code", "completed")

                # Effective time (administration date)
                eff_time = ET.SubElement(subst_admin, "effectiveTime")
                if imm.date:
                    eff_time.set("value", format_date(imm.date))

                # Route code (if available)
                if imm.route:
                    route = ET.SubElement(subst_admin, "routeCode")
                    route.set("displayName", imm.route)
                    route.set("codeSystem", "2.16.840.1.113883.3.26.1.1")
                    route.set("codeSystemName", "NCI Thesaurus")

                # Dose quantity
                if imm.dose_number:
                    dose_qty = ET.SubElement(subst_admin, "doseQuantity")
                    dose_qty.set("value", "1")

                # Consumable (vaccine product)
                consumable = ET.SubElement(subst_admin, "consumable")
                manuf_product = ET.SubElement(consumable, "manufacturedProduct")
                manuf_product.set("classCode", "MANU")

                # Immunization medication template
                prod_template = ET.SubElement(manuf_product, "templateId")
                prod_template.set("root", "2.16.840.1.113883.10.20.22.4.54")
                prod_template.set("extension", "2014-06-09")

                manuf_material = ET.SubElement(manuf_product, "manufacturedMaterial")

                # Vaccine code (CVX)
                code = ET.SubElement(manuf_material, "code")
                if imm.vaccine_code:
                    code.set("code", imm.vaccine_code.code)
                    code.set("codeSystem", "2.16.840.1.113883.12.292")  # CVX
                    code.set("codeSystemName", "CVX")
                code.set("displayName", imm.display_name)

                # Lot number
                if imm.lot_number:
                    lot = ET.SubElement(manuf_material, "lotNumberText")
                    lot.text = imm.lot_number

                # Manufacturer
                if imm.manufacturer:
                    manuf_org = ET.SubElement(manuf_product, "manufacturerOrganization")
                    manuf_name = ET.SubElement(manuf_org, "name")
                    manuf_name.text = imm.manufacturer

                # Series information (dose number in series)
                if imm.dose_number and imm.series_doses:
                    entry_rel = ET.SubElement(subst_admin, "entryRelationship")
                    entry_rel.set("typeCode", "SUBJ")

                    obs = ET.SubElement(entry_rel, "observation")
                    obs.set("classCode", "OBS")
                    obs.set("moodCode", "EVN")

                    obs_code = ET.SubElement(obs, "code")
                    obs_code.set("code", "30973-2")
                    obs_code.set("codeSystem", "2.16.840.1.113883.6.1")
                    obs_code.set("displayName", "Dose number")

                    obs_val = ET.SubElement(obs, "value")
                    obs_val.set(f"{{{self.NS_XSI}}}type", "INT")
                    obs_val.set("value", str(imm.dose_number))
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No immunization records"

    def _add_encounters_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add encounters section with structured entries and clinical notes."""
        section = self._add_section(
            parent,
            self.TEMPLATES["encounters"],
            "46240-8",
            "Encounters"
        )

        text = ET.SubElement(section, "text")
        if patient.encounters:
            # Narrative table
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Date", "Type", "Chief Complaint", "Provider"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for enc in sorted(patient.encounters, key=lambda e: e.date, reverse=True)[:20]:
                tr = ET.SubElement(tbody, "tr")
                td = ET.SubElement(tr, "td")
                td.text = str(enc.date.date()) if enc.date else ""
                td = ET.SubElement(tr, "td")
                td.text = enc.type.value.replace("-", " ").title()
                td = ET.SubElement(tr, "td")
                td.text = enc.chief_complaint or ""
                td = ET.SubElement(tr, "td")
                td.text = enc.provider.name if enc.provider else ""

            # Structured entries for each encounter
            for enc in sorted(patient.encounters, key=lambda e: e.date, reverse=True):
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                encounter_el = ET.SubElement(entry, "encounter")
                encounter_el.set("classCode", "ENC")
                encounter_el.set("moodCode", "EVN")

                # Encounter activity template
                template = ET.SubElement(encounter_el, "templateId")
                template.set("root", "2.16.840.1.113883.10.20.22.4.49")
                template.set("extension", "2015-08-01")

                enc_id = ET.SubElement(encounter_el, "id")
                enc_id.set("root", "urn:oread:encounter")
                enc_id.set("extension", enc.id)

                # Encounter type code
                code = ET.SubElement(encounter_el, "code")
                code.set("displayName", enc.type.value.replace("-", " ").title())
                code.set("codeSystem", "2.16.840.1.113883.6.12")  # CPT
                code.set("codeSystemName", "CPT")

                # Original text (chief complaint)
                if enc.chief_complaint:
                    orig_text = ET.SubElement(code, "originalText")
                    orig_text.text = enc.chief_complaint

                # Effective time
                eff_time = ET.SubElement(encounter_el, "effectiveTime")
                if enc.date:
                    low = ET.SubElement(eff_time, "low")
                    low.set("value", format_datetime(enc.date))
                if enc.end_date:
                    high = ET.SubElement(eff_time, "high")
                    high.set("value", format_datetime(enc.end_date))

                # Performer (provider)
                if enc.provider:
                    performer = ET.SubElement(encounter_el, "performer")
                    assigned_entity = ET.SubElement(performer, "assignedEntity")
                    entity_id = ET.SubElement(assigned_entity, "id")
                    entity_id.set("root", "urn:oread:provider")
                    if enc.provider.npi:
                        entity_id.set("extension", enc.provider.npi)

                    assigned_person = ET.SubElement(assigned_entity, "assignedPerson")
                    prov_name = ET.SubElement(assigned_person, "name")
                    prov_name_text = ET.SubElement(prov_name, "given")
                    prov_name_text.text = enc.provider.name

                # Location
                if enc.location:
                    participant = ET.SubElement(encounter_el, "participant")
                    participant.set("typeCode", "LOC")
                    participant_role = ET.SubElement(participant, "participantRole")
                    participant_role.set("classCode", "SDLOC")

                    loc_name = ET.SubElement(participant_role, "playingEntity")
                    loc_name.set("classCode", "PLC")
                    name_el = ET.SubElement(loc_name, "name")
                    name_el.text = enc.location.name

                # Clinical notes as entry relationships
                # HPI (History of Present Illness)
                if enc.hpi:
                    self._add_note_entry(encounter_el, "10164-2", "History of Present Illness",
                                         enc.hpi, enc.date)

                # Assessment
                if enc.assessment:
                    assessment_text = "\n".join([
                        f"{a.diagnosis}" + (f": {a.clinical_notes}" if a.clinical_notes else "")
                        for a in enc.assessment
                    ])
                    self._add_note_entry(encounter_el, "51848-0", "Assessment",
                                         assessment_text, enc.date)

                # Plan
                if enc.plan:
                    plan_text = "\n".join([
                        f"- [{p.category}] {p.description}" for p in enc.plan
                    ])
                    self._add_note_entry(encounter_el, "18776-5", "Treatment Plan",
                                         plan_text, enc.date)
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No encounter records"

    def _add_note_entry(self, parent: ET.Element, loinc_code: str,
                        title: str, note_text: str, enc_date: datetime) -> None:
        """Add a clinical note as an entry relationship."""
        entry_rel = ET.SubElement(parent, "entryRelationship")
        entry_rel.set("typeCode", "SUBJ")

        act = ET.SubElement(entry_rel, "act")
        act.set("classCode", "ACT")
        act.set("moodCode", "EVN")

        # Note activity template
        template = ET.SubElement(act, "templateId")
        template.set("root", "2.16.840.1.113883.10.20.22.4.202")
        template.set("extension", "2016-11-01")

        act_id = ET.SubElement(act, "id")
        act_id.set("root", generate_uuid())

        code = ET.SubElement(act, "code")
        code.set("code", loinc_code)
        code.set("codeSystem", "2.16.840.1.113883.6.1")
        code.set("codeSystemName", "LOINC")
        code.set("displayName", title)

        status = ET.SubElement(act, "statusCode")
        status.set("code", "completed")

        eff_time = ET.SubElement(act, "effectiveTime")
        eff_time.set("value", format_datetime(enc_date))

        # The actual note text
        text_el = ET.SubElement(act, "text")
        text_el.text = note_text

    def _add_vitals_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add vital signs section with structured entries."""
        section = self._add_section(
            parent,
            self.TEMPLATES["vitals"],
            "8716-3",
            "Vital Signs"
        )

        text = ET.SubElement(section, "text")

        # Collect all vitals from encounters
        vitals_entries = []
        for enc in sorted(patient.encounters, key=lambda e: e.date, reverse=True):
            if enc.vital_signs:
                vitals_entries.append((enc.date, enc.vital_signs))

        if vitals_entries:
            # Narrative text
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Date", "Height", "Weight", "HR", "BP", "Temp"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for enc_date, vitals in vitals_entries[:10]:  # Last 10 encounters
                tr = ET.SubElement(tbody, "tr")
                td = ET.SubElement(tr, "td")
                td.text = str(enc_date.date()) if enc_date else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{vitals.height_cm} cm" if vitals.height_cm else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{vitals.weight_kg} kg" if vitals.weight_kg else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{vitals.heart_rate}" if vitals.heart_rate else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{vitals.blood_pressure_systolic}/{vitals.blood_pressure_diastolic}" if vitals.blood_pressure_systolic else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{vitals.temperature_f}°F" if vitals.temperature_f else ""

            # Structured entries for each vitals set
            for enc_date, vitals in vitals_entries:
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                organizer = ET.SubElement(entry, "organizer")
                organizer.set("classCode", "CLUSTER")
                organizer.set("moodCode", "EVN")

                # Vital signs organizer template
                template = ET.SubElement(organizer, "templateId")
                template.set("root", "2.16.840.1.113883.10.20.22.4.26")
                template.set("extension", "2015-08-01")

                org_id = ET.SubElement(organizer, "id")
                org_id.set("root", generate_uuid())

                code = ET.SubElement(organizer, "code")
                code.set("code", "46680005")
                code.set("codeSystem", "2.16.840.1.113883.6.96")
                code.set("codeSystemName", "SNOMED CT")
                code.set("displayName", "Vital signs")

                status = ET.SubElement(organizer, "statusCode")
                status.set("code", "completed")

                eff_time = ET.SubElement(organizer, "effectiveTime")
                eff_time.set("value", format_datetime(enc_date))

                # Add individual vital sign observations
                if vitals.height_cm:
                    self._add_vital_observation(
                        organizer, enc_date, "8302-2", "Body height",
                        vitals.height_cm, "cm", "[cm_i]"
                    )

                if vitals.weight_kg:
                    self._add_vital_observation(
                        organizer, enc_date, "29463-7", "Body weight",
                        vitals.weight_kg, "kg", "kg"
                    )

                if vitals.heart_rate:
                    self._add_vital_observation(
                        organizer, enc_date, "8867-4", "Heart rate",
                        vitals.heart_rate, "/min", "/min"
                    )

                if vitals.blood_pressure_systolic:
                    self._add_vital_observation(
                        organizer, enc_date, "8480-6", "Systolic blood pressure",
                        vitals.blood_pressure_systolic, "mmHg", "mm[Hg]"
                    )

                if vitals.blood_pressure_diastolic:
                    self._add_vital_observation(
                        organizer, enc_date, "8462-4", "Diastolic blood pressure",
                        vitals.blood_pressure_diastolic, "mmHg", "mm[Hg]"
                    )

                if vitals.temperature_f:
                    self._add_vital_observation(
                        organizer, enc_date, "8310-5", "Body temperature",
                        vitals.temperature_f, "degF", "[degF]"
                    )
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No vital signs recorded"

    def _add_vital_observation(self, parent: ET.Element, enc_date: datetime,
                                loinc_code: str, display_name: str,
                                value: float, unit_display: str, ucum_unit: str) -> None:
        """Add a single vital sign observation component."""
        component = ET.SubElement(parent, "component")
        obs = ET.SubElement(component, "observation")
        obs.set("classCode", "OBS")
        obs.set("moodCode", "EVN")

        # Vital sign observation template
        template = ET.SubElement(obs, "templateId")
        template.set("root", "2.16.840.1.113883.10.20.22.4.27")
        template.set("extension", "2014-06-09")

        obs_id = ET.SubElement(obs, "id")
        obs_id.set("root", generate_uuid())

        code = ET.SubElement(obs, "code")
        code.set("code", loinc_code)
        code.set("codeSystem", "2.16.840.1.113883.6.1")
        code.set("codeSystemName", "LOINC")
        code.set("displayName", display_name)

        status = ET.SubElement(obs, "statusCode")
        status.set("code", "completed")

        eff_time = ET.SubElement(obs, "effectiveTime")
        eff_time.set("value", format_datetime(enc_date))

        val = ET.SubElement(obs, "value")
        val.set(f"{{{self.NS_XSI}}}type", "PQ")
        val.set("value", str(value))
        val.set("unit", ucum_unit)


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
