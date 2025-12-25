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
        "results": "2.16.840.1.113883.10.20.22.2.3.1",
        "procedures": "2.16.840.1.113883.10.20.22.2.7.1",
        "social_history": "2.16.840.1.113883.10.20.22.2.17",
        "family_history": "2.16.840.1.113883.10.20.22.2.15",
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
        self._add_results_section(structured_body, patient)
        self._add_procedures_section(structured_body, patient)
        self._add_social_history_section(structured_body, patient)
        self._add_family_history_section(structured_body, patient)
        self._add_growth_data_section(structured_body, patient)
        self._add_developmental_milestones_section(structured_body, patient)

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
        """Add problems/conditions section with proper C-CDA structure."""
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
            for idx, condition in enumerate(patient.problem_list):
                li = ET.SubElement(ul, "item")
                li.set("ID", f"problem{idx}")
                li.text = f"{condition.display_name} - {condition.clinical_status.value}"
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No known problems"

        # Structured entries for each problem
        for idx, condition in enumerate(patient.problem_list):
            entry = ET.SubElement(section, "entry")
            entry.set("typeCode", "DRIV")

            # Problem Concern Act
            act = ET.SubElement(entry, "act")
            act.set("classCode", "ACT")
            act.set("moodCode", "EVN")

            # Problem Concern Act template
            act_template = ET.SubElement(act, "templateId")
            act_template.set("root", "2.16.840.1.113883.10.20.22.4.3")
            act_template.set("extension", "2015-08-01")

            act_id = ET.SubElement(act, "id")
            act_id.set("root", generate_uuid())

            act_code = ET.SubElement(act, "code")
            act_code.set("code", "CONC")
            act_code.set("codeSystem", "2.16.840.1.113883.5.6")
            act_code.set("displayName", "Concern")

            act_status = ET.SubElement(act, "statusCode")
            status_code = "active" if condition.clinical_status.value == "active" else "completed"
            act_status.set("code", status_code)

            # Effective time (when concern was recorded)
            act_eff = ET.SubElement(act, "effectiveTime")
            if condition.onset_date:
                low = ET.SubElement(act_eff, "low")
                low.set("value", format_date(condition.onset_date))

            # Problem Observation (entryRelationship)
            entry_rel = ET.SubElement(act, "entryRelationship")
            entry_rel.set("typeCode", "SUBJ")

            obs = ET.SubElement(entry_rel, "observation")
            obs.set("classCode", "OBS")
            obs.set("moodCode", "EVN")

            # Problem Observation template
            obs_template = ET.SubElement(obs, "templateId")
            obs_template.set("root", "2.16.840.1.113883.10.20.22.4.4")
            obs_template.set("extension", "2015-08-01")

            obs_id = ET.SubElement(obs, "id")
            obs_id.set("root", generate_uuid())

            # Problem type code (diagnosis)
            obs_code = ET.SubElement(obs, "code")
            obs_code.set("code", "282291009")
            obs_code.set("codeSystem", "2.16.840.1.113883.6.96")
            obs_code.set("codeSystemName", "SNOMED CT")
            obs_code.set("displayName", "Diagnosis")

            # Reference to narrative
            obs_text = ET.SubElement(obs, "text")
            ref = ET.SubElement(obs_text, "reference")
            ref.set("value", f"#problem{idx}")

            obs_status = ET.SubElement(obs, "statusCode")
            obs_status.set("code", "completed")

            # Onset date
            obs_eff = ET.SubElement(obs, "effectiveTime")
            if condition.onset_date:
                onset_low = ET.SubElement(obs_eff, "low")
                onset_low.set("value", format_date(condition.onset_date))

            # Value (the actual condition code)
            value = ET.SubElement(obs, "value")
            value.set(f"{{{self.NS_XSI}}}type", "CD")
            if condition.code:
                value.set("code", condition.code.code)
                # Map code system URIs to OIDs
                system_map = {
                    "http://hl7.org/fhir/sid/icd-10-cm": ("2.16.840.1.113883.6.90", "ICD-10-CM"),
                    "http://snomed.info/sct": ("2.16.840.1.113883.6.96", "SNOMED CT"),
                    "http://hl7.org/fhir/sid/icd-10": ("2.16.840.1.113883.6.3", "ICD-10"),
                }
                if condition.code.system in system_map:
                    oid, name = system_map[condition.code.system]
                    value.set("codeSystem", oid)
                    value.set("codeSystemName", name)
                elif "snomed" in condition.code.system.lower():
                    value.set("codeSystem", "2.16.840.1.113883.6.96")
                    value.set("codeSystemName", "SNOMED CT")
                else:
                    # Default to ICD-10-CM
                    value.set("codeSystem", "2.16.840.1.113883.6.90")
                    value.set("codeSystemName", "ICD-10-CM")
            value.set("displayName", condition.display_name)

    def _add_medications_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add medications section with structured entries."""
        section = self._add_section(
            parent,
            self.TEMPLATES["medications"],
            "10160-0",
            "Medications"
        )

        text = ET.SubElement(section, "text")
        active_meds = [m for m in patient.medication_list if m.status.value == "active"]

        if active_meds:
            # Narrative table
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Medication", "Dose", "Frequency", "Route", "Start Date"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for idx, med in enumerate(active_meds):
                tr = ET.SubElement(tbody, "tr")
                tr.set("ID", f"med{idx}")
                td = ET.SubElement(tr, "td")
                td.text = med.display_name
                td = ET.SubElement(tr, "td")
                td.text = f"{med.dose_quantity} {med.dose_unit}" if med.dose_quantity else ""
                td = ET.SubElement(tr, "td")
                td.text = med.frequency or ""
                td = ET.SubElement(tr, "td")
                td.text = med.route or ""
                td = ET.SubElement(tr, "td")
                td.text = str(med.start_date) if med.start_date else ""

            # Structured entries for each medication
            for idx, med in enumerate(active_meds):
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                subst_admin = ET.SubElement(entry, "substanceAdministration")
                subst_admin.set("classCode", "SBADM")
                subst_admin.set("moodCode", "EVN")

                # Medication Activity template
                template = ET.SubElement(subst_admin, "templateId")
                template.set("root", "2.16.840.1.113883.10.20.22.4.16")
                template.set("extension", "2014-06-09")

                med_id = ET.SubElement(subst_admin, "id")
                med_id.set("root", generate_uuid())

                # Reference to narrative
                med_text = ET.SubElement(subst_admin, "text")
                ref = ET.SubElement(med_text, "reference")
                ref.set("value", f"#med{idx}")

                status = ET.SubElement(subst_admin, "statusCode")
                status.set("code", "active" if med.status.value == "active" else "completed")

                # Effective time (medication period)
                eff_time = ET.SubElement(subst_admin, "effectiveTime")
                eff_time.set(f"{{{self.NS_XSI}}}type", "IVL_TS")
                if med.start_date:
                    low = ET.SubElement(eff_time, "low")
                    low.set("value", format_date(med.start_date))
                if med.end_date:
                    high = ET.SubElement(eff_time, "high")
                    high.set("value", format_date(med.end_date))
                else:
                    high = ET.SubElement(eff_time, "high")
                    high.set("nullFlavor", "UNK")

                # Frequency (as second effectiveTime for periodic dose)
                if med.frequency:
                    freq_time = ET.SubElement(subst_admin, "effectiveTime")
                    freq_time.set(f"{{{self.NS_XSI}}}type", "PIVL_TS")
                    freq_time.set("operator", "A")
                    freq_time.set("institutionSpecified", "true")
                    period = ET.SubElement(freq_time, "period")
                    # Map common frequencies to period
                    freq_map = {
                        "once daily": ("24", "h"),
                        "daily": ("24", "h"),
                        "twice daily": ("12", "h"),
                        "BID": ("12", "h"),
                        "three times daily": ("8", "h"),
                        "TID": ("8", "h"),
                        "four times daily": ("6", "h"),
                        "QID": ("6", "h"),
                        "every 4 hours": ("4", "h"),
                        "every 6 hours": ("6", "h"),
                        "every 8 hours": ("8", "h"),
                        "every 12 hours": ("12", "h"),
                        "weekly": ("1", "wk"),
                        "monthly": ("1", "mo"),
                    }
                    freq_lower = med.frequency.lower()
                    if freq_lower in freq_map:
                        period.set("value", freq_map[freq_lower][0])
                        period.set("unit", freq_map[freq_lower][1])
                    else:
                        period.set("value", "24")
                        period.set("unit", "h")

                # Route code
                if med.route:
                    route = ET.SubElement(subst_admin, "routeCode")
                    route_map = {
                        "oral": ("C38288", "ORAL"),
                        "topical": ("C38304", "TOPICAL"),
                        "inhalation": ("C38216", "RESPIRATORY (INHALATION)"),
                        "injection": ("C38276", "INTRAMUSCULAR"),
                        "subcutaneous": ("C38299", "SUBCUTANEOUS"),
                        "intravenous": ("C38276", "INTRAVENOUS"),
                        "rectal": ("C38295", "RECTAL"),
                        "ophthalmic": ("C38287", "OPHTHALMIC"),
                        "otic": ("C38192", "AURICULAR (OTIC)"),
                        "nasal": ("C38284", "NASAL"),
                    }
                    route_lower = med.route.lower()
                    if route_lower in route_map:
                        route.set("code", route_map[route_lower][0])
                        route.set("displayName", route_map[route_lower][1])
                    else:
                        route.set("displayName", med.route)
                    route.set("codeSystem", "2.16.840.1.113883.3.26.1.1")
                    route.set("codeSystemName", "NCI Thesaurus")

                # Dose quantity
                if med.dose_quantity:
                    dose = ET.SubElement(subst_admin, "doseQuantity")
                    dose.set("value", med.dose_quantity)
                    if med.dose_unit:
                        dose.set("unit", med.dose_unit)

                # Consumable (the medication itself)
                consumable = ET.SubElement(subst_admin, "consumable")
                manuf_product = ET.SubElement(consumable, "manufacturedProduct")
                manuf_product.set("classCode", "MANU")

                # Medication Information template
                prod_template = ET.SubElement(manuf_product, "templateId")
                prod_template.set("root", "2.16.840.1.113883.10.20.22.4.23")
                prod_template.set("extension", "2014-06-09")

                manuf_material = ET.SubElement(manuf_product, "manufacturedMaterial")

                # Medication code (RxNorm)
                code = ET.SubElement(manuf_material, "code")
                if med.code:
                    code.set("code", med.code.code)
                    code.set("codeSystem", "2.16.840.1.113883.6.88")  # RxNorm
                    code.set("codeSystemName", "RxNorm")
                code.set("displayName", med.display_name)

                # Indication (reason for medication)
                if med.indication:
                    entry_rel = ET.SubElement(subst_admin, "entryRelationship")
                    entry_rel.set("typeCode", "RSON")

                    ind_obs = ET.SubElement(entry_rel, "observation")
                    ind_obs.set("classCode", "OBS")
                    ind_obs.set("moodCode", "EVN")

                    ind_template = ET.SubElement(ind_obs, "templateId")
                    ind_template.set("root", "2.16.840.1.113883.10.20.22.4.19")
                    ind_template.set("extension", "2014-06-09")

                    ind_id = ET.SubElement(ind_obs, "id")
                    ind_id.set("root", generate_uuid())

                    ind_code = ET.SubElement(ind_obs, "code")
                    ind_code.set("code", "75321-0")
                    ind_code.set("codeSystem", "2.16.840.1.113883.6.1")
                    ind_code.set("displayName", "Clinical finding")

                    ind_status = ET.SubElement(ind_obs, "statusCode")
                    ind_status.set("code", "completed")

                    ind_val = ET.SubElement(ind_obs, "value")
                    ind_val.set(f"{{{self.NS_XSI}}}type", "CD")
                    ind_val.set("displayName", med.indication)
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No current medications"

    def _add_allergies_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add allergies section with structured entries."""
        section = self._add_section(
            parent,
            self.TEMPLATES["allergies"],
            "48765-2",
            "Allergies and Adverse Reactions"
        )

        text = ET.SubElement(section, "text")
        if patient.allergy_list:
            # Narrative table
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Allergen", "Reaction", "Severity", "Status"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for idx, allergy in enumerate(patient.allergy_list):
                tr = ET.SubElement(tbody, "tr")
                tr.set("ID", f"allergy{idx}")
                td = ET.SubElement(tr, "td")
                td.text = allergy.display_name
                td = ET.SubElement(tr, "td")
                if allergy.reactions:
                    td.text = ", ".join([r.manifestation for r in allergy.reactions])
                else:
                    td.text = "Unknown"
                td = ET.SubElement(tr, "td")
                if allergy.reactions and allergy.reactions[0].severity:
                    td.text = allergy.reactions[0].severity.value
                else:
                    td.text = ""
                td = ET.SubElement(tr, "td")
                td.text = allergy.clinical_status

            # Structured entries for each allergy
            for idx, allergy in enumerate(patient.allergy_list):
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                # Allergy Concern Act
                act = ET.SubElement(entry, "act")
                act.set("classCode", "ACT")
                act.set("moodCode", "EVN")

                # Allergy Concern Act template
                act_template = ET.SubElement(act, "templateId")
                act_template.set("root", "2.16.840.1.113883.10.20.22.4.30")
                act_template.set("extension", "2015-08-01")

                act_id = ET.SubElement(act, "id")
                act_id.set("root", generate_uuid())

                act_code = ET.SubElement(act, "code")
                act_code.set("code", "CONC")
                act_code.set("codeSystem", "2.16.840.1.113883.5.6")
                act_code.set("displayName", "Concern")

                act_status = ET.SubElement(act, "statusCode")
                status_code = "active" if allergy.clinical_status == "active" else "completed"
                act_status.set("code", status_code)

                # Effective time
                act_eff = ET.SubElement(act, "effectiveTime")
                if allergy.onset_date:
                    low = ET.SubElement(act_eff, "low")
                    low.set("value", format_date(allergy.onset_date))
                else:
                    low = ET.SubElement(act_eff, "low")
                    low.set("nullFlavor", "UNK")

                # Allergy Observation (entryRelationship)
                entry_rel = ET.SubElement(act, "entryRelationship")
                entry_rel.set("typeCode", "SUBJ")

                obs = ET.SubElement(entry_rel, "observation")
                obs.set("classCode", "OBS")
                obs.set("moodCode", "EVN")

                # Handle negation for "No Known Allergies" - not applicable here since we have allergies

                # Allergy Observation template
                obs_template = ET.SubElement(obs, "templateId")
                obs_template.set("root", "2.16.840.1.113883.10.20.22.4.7")
                obs_template.set("extension", "2014-06-09")

                obs_id = ET.SubElement(obs, "id")
                obs_id.set("root", generate_uuid())

                # Allergy type code
                obs_code = ET.SubElement(obs, "code")
                obs_code.set("code", "ASSERTION")
                obs_code.set("codeSystem", "2.16.840.1.113883.5.4")

                # Reference to narrative
                obs_text = ET.SubElement(obs, "text")
                ref = ET.SubElement(obs_text, "reference")
                ref.set("value", f"#allergy{idx}")

                obs_status = ET.SubElement(obs, "statusCode")
                obs_status.set("code", "completed")

                # Effective time (onset)
                obs_eff = ET.SubElement(obs, "effectiveTime")
                if allergy.onset_date:
                    onset_low = ET.SubElement(obs_eff, "low")
                    onset_low.set("value", format_date(allergy.onset_date))

                # Value - allergy or intolerance type
                value = ET.SubElement(obs, "value")
                value.set(f"{{{self.NS_XSI}}}type", "CD")
                # Map category to SNOMED codes
                category_codes = {
                    "food": ("414285001", "Allergy to food"),
                    "medication": ("416098002", "Drug allergy"),
                    "environment": ("426232007", "Environmental allergy"),
                    "biologic": ("419199007", "Allergy to substance"),
                }
                if allergy.category.value in category_codes:
                    code_val, display = category_codes[allergy.category.value]
                    value.set("code", code_val)
                    value.set("displayName", display)
                else:
                    value.set("code", "419199007")
                    value.set("displayName", "Allergy to substance")
                value.set("codeSystem", "2.16.840.1.113883.6.96")
                value.set("codeSystemName", "SNOMED CT")

                # Participant - the allergen substance
                participant = ET.SubElement(obs, "participant")
                participant.set("typeCode", "CSM")

                participant_role = ET.SubElement(participant, "participantRole")
                participant_role.set("classCode", "MANU")

                playing_entity = ET.SubElement(participant_role, "playingEntity")
                playing_entity.set("classCode", "MMAT")

                allergen_code = ET.SubElement(playing_entity, "code")
                if allergy.code:
                    allergen_code.set("code", allergy.code.code)
                    allergen_code.set("codeSystem", allergy.code.system or "2.16.840.1.113883.6.88")
                allergen_code.set("displayName", allergy.display_name)

                # Reaction observations
                for reaction in allergy.reactions:
                    reaction_rel = ET.SubElement(obs, "entryRelationship")
                    reaction_rel.set("typeCode", "MFST")
                    reaction_rel.set("inversionInd", "true")

                    reaction_obs = ET.SubElement(reaction_rel, "observation")
                    reaction_obs.set("classCode", "OBS")
                    reaction_obs.set("moodCode", "EVN")

                    # Reaction Observation template
                    reaction_template = ET.SubElement(reaction_obs, "templateId")
                    reaction_template.set("root", "2.16.840.1.113883.10.20.22.4.9")
                    reaction_template.set("extension", "2014-06-09")

                    reaction_id = ET.SubElement(reaction_obs, "id")
                    reaction_id.set("root", generate_uuid())

                    reaction_code = ET.SubElement(reaction_obs, "code")
                    reaction_code.set("code", "ASSERTION")
                    reaction_code.set("codeSystem", "2.16.840.1.113883.5.4")

                    reaction_status = ET.SubElement(reaction_obs, "statusCode")
                    reaction_status.set("code", "completed")

                    # Reaction value (manifestation)
                    reaction_val = ET.SubElement(reaction_obs, "value")
                    reaction_val.set(f"{{{self.NS_XSI}}}type", "CD")
                    reaction_val.set("displayName", reaction.manifestation)
                    reaction_val.set("codeSystem", "2.16.840.1.113883.6.96")
                    reaction_val.set("codeSystemName", "SNOMED CT")

                    # Severity observation
                    if reaction.severity:
                        severity_rel = ET.SubElement(reaction_obs, "entryRelationship")
                        severity_rel.set("typeCode", "SUBJ")
                        severity_rel.set("inversionInd", "true")

                        severity_obs = ET.SubElement(severity_rel, "observation")
                        severity_obs.set("classCode", "OBS")
                        severity_obs.set("moodCode", "EVN")

                        # Severity Observation template
                        sev_template = ET.SubElement(severity_obs, "templateId")
                        sev_template.set("root", "2.16.840.1.113883.10.20.22.4.8")
                        sev_template.set("extension", "2014-06-09")

                        sev_code = ET.SubElement(severity_obs, "code")
                        sev_code.set("code", "SEV")
                        sev_code.set("codeSystem", "2.16.840.1.113883.5.4")
                        sev_code.set("displayName", "Severity Observation")

                        sev_status = ET.SubElement(severity_obs, "statusCode")
                        sev_status.set("code", "completed")

                        # Severity value
                        severity_map = {
                            "mild": ("255604002", "Mild"),
                            "moderate": ("6736007", "Moderate"),
                            "severe": ("24484000", "Severe"),
                            "life-threatening": ("442452003", "Life threatening severity"),
                        }
                        sev_val = ET.SubElement(severity_obs, "value")
                        sev_val.set(f"{{{self.NS_XSI}}}type", "CD")
                        if reaction.severity.value in severity_map:
                            code_val, display = severity_map[reaction.severity.value]
                            sev_val.set("code", code_val)
                            sev_val.set("displayName", display)
                        sev_val.set("codeSystem", "2.16.840.1.113883.6.96")
                        sev_val.set("codeSystemName", "SNOMED CT")
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


    def _add_results_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add results (laboratory) section with structured entries."""
        section = self._add_section(
            parent,
            self.TEMPLATES["results"],
            "30954-2",
            "Results"
        )

        text = ET.SubElement(section, "text")

        # Collect lab results from all encounters
        all_results = []
        for enc in patient.encounters:
            for result in enc.lab_results:
                all_results.append((enc.date, result))

        if all_results:
            # Narrative table
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Date", "Test", "Value", "Units", "Reference Range", "Interpretation"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for enc_date, result in sorted(all_results, key=lambda x: x[0], reverse=True)[:50]:
                # Handle both LabPanel and LabResult
                if hasattr(result, 'results'):  # LabPanel
                    for lab in result.results:
                        self._add_lab_result_row(tbody, enc_date, lab)
                else:  # LabResult
                    self._add_lab_result_row(tbody, enc_date, result)

            # Structured entries
            for enc_date, result in all_results:
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                if hasattr(result, 'results'):  # LabPanel - use organizer
                    organizer = ET.SubElement(entry, "organizer")
                    organizer.set("classCode", "BATTERY")
                    organizer.set("moodCode", "EVN")

                    # Result organizer template
                    template = ET.SubElement(organizer, "templateId")
                    template.set("root", "2.16.840.1.113883.10.20.22.4.1")
                    template.set("extension", "2015-08-01")

                    org_id = ET.SubElement(organizer, "id")
                    org_id.set("root", generate_uuid())

                    code = ET.SubElement(organizer, "code")
                    if result.code:
                        code.set("code", result.code.code)
                        code.set("codeSystem", "2.16.840.1.113883.6.1")
                        code.set("codeSystemName", "LOINC")
                    code.set("displayName", result.display_name)

                    status = ET.SubElement(organizer, "statusCode")
                    status.set("code", "completed")

                    for lab in result.results:
                        self._add_lab_observation(organizer, lab)
                else:  # Single LabResult
                    self._add_lab_observation(entry, result, is_standalone=True)
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No laboratory results"

    def _add_lab_result_row(self, tbody: ET.Element, enc_date, lab) -> None:
        """Add a row to the lab results narrative table."""
        tr = ET.SubElement(tbody, "tr")
        td = ET.SubElement(tr, "td")
        td.text = str(enc_date.date()) if enc_date else ""
        td = ET.SubElement(tr, "td")
        td.text = lab.display_name
        td = ET.SubElement(tr, "td")
        td.text = str(lab.value) if lab.value is not None else ""
        td = ET.SubElement(tr, "td")
        td.text = lab.unit or ""
        td = ET.SubElement(tr, "td")
        if lab.reference_range:
            td.text = f"{lab.reference_range.low}-{lab.reference_range.high}"
        else:
            td.text = ""
        td = ET.SubElement(tr, "td")
        td.text = lab.interpretation.value if lab.interpretation else ""

    def _add_lab_observation(self, parent: ET.Element, lab, is_standalone: bool = False) -> None:
        """Add a lab result observation."""
        if is_standalone:
            obs = ET.SubElement(parent, "observation")
        else:
            component = ET.SubElement(parent, "component")
            obs = ET.SubElement(component, "observation")

        obs.set("classCode", "OBS")
        obs.set("moodCode", "EVN")

        # Result observation template
        template = ET.SubElement(obs, "templateId")
        template.set("root", "2.16.840.1.113883.10.20.22.4.2")
        template.set("extension", "2015-08-01")

        obs_id = ET.SubElement(obs, "id")
        obs_id.set("root", generate_uuid())

        code = ET.SubElement(obs, "code")
        if lab.code:
            code.set("code", lab.code.code)
            code.set("codeSystem", "2.16.840.1.113883.6.1")
            code.set("codeSystemName", "LOINC")
        code.set("displayName", lab.display_name)

        status = ET.SubElement(obs, "statusCode")
        status.set("code", "completed")

        eff_time = ET.SubElement(obs, "effectiveTime")
        if lab.resulted_date:
            eff_time.set("value", format_datetime(lab.resulted_date))

        # Value
        if lab.value is not None:
            val = ET.SubElement(obs, "value")
            if isinstance(lab.value, (int, float)):
                val.set(f"{{{self.NS_XSI}}}type", "PQ")
                val.set("value", str(lab.value))
                if lab.unit:
                    val.set("unit", lab.unit)
            else:
                val.set(f"{{{self.NS_XSI}}}type", "ST")
                val.text = str(lab.value)

        # Interpretation
        if lab.interpretation:
            interp = ET.SubElement(obs, "interpretationCode")
            interp_map = {
                "normal": ("N", "Normal"),
                "abnormal": ("A", "Abnormal"),
                "high": ("H", "High"),
                "low": ("L", "Low"),
                "critical": ("AA", "Critical abnormal"),
            }
            if lab.interpretation.value in interp_map:
                code_val, display = interp_map[lab.interpretation.value]
                interp.set("code", code_val)
                interp.set("displayName", display)
            interp.set("codeSystem", "2.16.840.1.113883.5.83")

        # Reference range
        if lab.reference_range:
            ref_range = ET.SubElement(obs, "referenceRange")
            obs_range = ET.SubElement(ref_range, "observationRange")
            range_val = ET.SubElement(obs_range, "value")
            range_val.set(f"{{{self.NS_XSI}}}type", "IVL_PQ")
            if lab.reference_range.low is not None:
                low = ET.SubElement(range_val, "low")
                low.set("value", str(lab.reference_range.low))
                if lab.unit:
                    low.set("unit", lab.unit)
            if lab.reference_range.high is not None:
                high = ET.SubElement(range_val, "high")
                high.set("value", str(lab.reference_range.high))
                if lab.unit:
                    high.set("unit", lab.unit)

    def _add_procedures_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add procedures section with structured entries."""
        section = self._add_section(
            parent,
            self.TEMPLATES["procedures"],
            "47519-4",
            "Procedures"
        )

        text = ET.SubElement(section, "text")

        # Combine procedure_history and surgical_history
        all_procedures = []
        for proc in patient.procedure_history:
            all_procedures.append(("procedure", proc))
        for surg in patient.surgical_history:
            all_procedures.append(("surgery", surg))

        if all_procedures:
            # Narrative table
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Date", "Procedure", "Reason", "Outcome"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for idx, (proc_type, proc) in enumerate(sorted(all_procedures,
                key=lambda x: x[1].performed_date if hasattr(x[1], 'performed_date') else x[1].date,
                reverse=True)):
                tr = ET.SubElement(tbody, "tr")
                tr.set("ID", f"proc{idx}")
                td = ET.SubElement(tr, "td")
                proc_date = proc.performed_date if hasattr(proc, 'performed_date') else proc.date
                td.text = str(proc_date) if proc_date else ""
                td = ET.SubElement(tr, "td")
                td.text = proc.display_name
                td = ET.SubElement(tr, "td")
                td.text = proc.reason if hasattr(proc, 'reason') else (proc.indication or "")
                td = ET.SubElement(tr, "td")
                td.text = proc.outcome or ""

            # Structured entries
            for idx, (proc_type, proc) in enumerate(all_procedures):
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                procedure = ET.SubElement(entry, "procedure")
                procedure.set("classCode", "PROC")
                procedure.set("moodCode", "EVN")

                # Procedure activity template
                template = ET.SubElement(procedure, "templateId")
                template.set("root", "2.16.840.1.113883.10.20.22.4.14")
                template.set("extension", "2014-06-09")

                proc_id = ET.SubElement(procedure, "id")
                proc_id.set("root", generate_uuid())

                # Procedure code
                code = ET.SubElement(procedure, "code")
                if hasattr(proc, 'code') and proc.code:
                    code.set("code", proc.code.code)
                    code.set("codeSystem", "2.16.840.1.113883.6.96")  # SNOMED
                    code.set("codeSystemName", "SNOMED CT")
                code.set("displayName", proc.display_name)

                # Reference to narrative
                proc_text = ET.SubElement(procedure, "text")
                ref = ET.SubElement(proc_text, "reference")
                ref.set("value", f"#proc{idx}")

                status = ET.SubElement(procedure, "statusCode")
                status.set("code", "completed")

                # Effective time
                eff_time = ET.SubElement(procedure, "effectiveTime")
                proc_date = proc.performed_date if hasattr(proc, 'performed_date') else proc.date
                if proc_date:
                    eff_time.set("value", format_date(proc_date))

                # Performer
                performer_name = proc.performer if hasattr(proc, 'performer') else (proc.surgeon if hasattr(proc, 'surgeon') else None)
                if performer_name:
                    performer = ET.SubElement(procedure, "performer")
                    assigned_entity = ET.SubElement(performer, "assignedEntity")
                    entity_id = ET.SubElement(assigned_entity, "id")
                    entity_id.set("nullFlavor", "UNK")
                    assigned_person = ET.SubElement(assigned_entity, "assignedPerson")
                    name = ET.SubElement(assigned_person, "name")
                    name.text = performer_name
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No procedures recorded"

    def _add_social_history_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add social history section."""
        section = self._add_section(
            parent,
            self.TEMPLATES["social_history"],
            "29762-2",
            "Social History"
        )

        text = ET.SubElement(section, "text")
        sh = patient.social_history

        if sh:
            ul = ET.SubElement(text, "list")

            # Living situation
            if sh.living_situation:
                li = ET.SubElement(ul, "item")
                li.text = f"Living Situation: {sh.living_situation}"

            # School (pediatric)
            if sh.school_name:
                li = ET.SubElement(ul, "item")
                li.text = f"School: {sh.school_name}"
                if sh.grade_level:
                    li.text += f" (Grade {sh.grade_level})"

            # Tobacco use (for older children/teens)
            if sh.tobacco and sh.tobacco.status:
                li = ET.SubElement(ul, "item")
                li.text = f"Tobacco Use: {sh.tobacco.status}"
                if sh.tobacco.frequency:
                    li.text += f" ({sh.tobacco.frequency})"

                # Add structured entry for smoking status (Meaningful Use requirement)
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                obs = ET.SubElement(entry, "observation")
                obs.set("classCode", "OBS")
                obs.set("moodCode", "EVN")

                # Smoking status template
                template = ET.SubElement(obs, "templateId")
                template.set("root", "2.16.840.1.113883.10.20.22.4.78")
                template.set("extension", "2014-06-09")

                obs_id = ET.SubElement(obs, "id")
                obs_id.set("root", generate_uuid())

                obs_code = ET.SubElement(obs, "code")
                obs_code.set("code", "72166-2")
                obs_code.set("codeSystem", "2.16.840.1.113883.6.1")
                obs_code.set("codeSystemName", "LOINC")
                obs_code.set("displayName", "Tobacco smoking status")

                obs_status = ET.SubElement(obs, "statusCode")
                obs_status.set("code", "completed")

                obs_eff = ET.SubElement(obs, "effectiveTime")
                obs_eff.set("value", format_datetime(datetime.now()))

                # Value - SNOMED code for smoking status
                value = ET.SubElement(obs, "value")
                value.set(f"{{{self.NS_XSI}}}type", "CD")
                smoking_codes = {
                    "never": ("266919005", "Never smoker"),
                    "former": ("8517006", "Former smoker"),
                    "current": ("77176002", "Smoker"),
                    "unknown": ("266927001", "Unknown if ever smoked"),
                }
                status_lower = sh.tobacco.status.lower()
                for key, (code_val, display) in smoking_codes.items():
                    if key in status_lower:
                        value.set("code", code_val)
                        value.set("displayName", display)
                        break
                else:
                    value.set("code", "266927001")
                    value.set("displayName", "Unknown if ever smoked")
                value.set("codeSystem", "2.16.840.1.113883.6.96")
                value.set("codeSystemName", "SNOMED CT")

            # Alcohol use
            if sh.alcohol and sh.alcohol.status:
                li = ET.SubElement(ul, "item")
                li.text = f"Alcohol Use: {sh.alcohol.status}"
                if sh.alcohol.frequency:
                    li.text += f" ({sh.alcohol.frequency})"

            # Food security (SDOH)
            if sh.food_security and sh.food_security != "secure":
                li = ET.SubElement(ul, "item")
                li.text = f"Food Security: {sh.food_security}"

            # Housing stability (SDOH)
            if sh.housing_stability and sh.housing_stability != "stable":
                li = ET.SubElement(ul, "item")
                li.text = f"Housing: {sh.housing_stability}"
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No social history recorded"

    def _add_family_history_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add family history section with structured entries."""
        section = self._add_section(
            parent,
            self.TEMPLATES["family_history"],
            "10157-6",
            "Family History"
        )

        text = ET.SubElement(section, "text")

        if patient.family_history:
            # Narrative table
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Relationship", "Condition", "Age at Onset", "Deceased"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for fh in patient.family_history:
                tr = ET.SubElement(tbody, "tr")
                td = ET.SubElement(tr, "td")
                td.text = fh.relationship
                td = ET.SubElement(tr, "td")
                td.text = fh.condition
                td = ET.SubElement(tr, "td")
                td.text = str(fh.onset_age) if fh.onset_age else ""
                td = ET.SubElement(tr, "td")
                td.text = "Yes" if fh.deceased else "No"

            # Structured entries
            for fh in patient.family_history:
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                organizer = ET.SubElement(entry, "organizer")
                organizer.set("classCode", "CLUSTER")
                organizer.set("moodCode", "EVN")

                # Family history organizer template
                template = ET.SubElement(organizer, "templateId")
                template.set("root", "2.16.840.1.113883.10.20.22.4.45")
                template.set("extension", "2015-08-01")

                org_id = ET.SubElement(organizer, "id")
                org_id.set("root", generate_uuid())

                org_status = ET.SubElement(organizer, "statusCode")
                org_status.set("code", "completed")

                # Subject (the relative)
                subject = ET.SubElement(organizer, "subject")
                related_subject = ET.SubElement(subject, "relatedSubject")
                related_subject.set("classCode", "PRS")

                # Relationship code
                rel_code = ET.SubElement(related_subject, "code")
                relationship_codes = {
                    "mother": ("MTH", "Mother"),
                    "father": ("FTH", "Father"),
                    "sister": ("SIS", "Sister"),
                    "brother": ("BRO", "Brother"),
                    "maternal grandmother": ("MGRMTH", "Maternal grandmother"),
                    "maternal grandfather": ("MGRFTH", "Maternal grandfather"),
                    "paternal grandmother": ("PGRMTH", "Paternal grandmother"),
                    "paternal grandfather": ("PGRFTH", "Paternal grandfather"),
                    "aunt": ("AUNT", "Aunt"),
                    "uncle": ("UNCLE", "Uncle"),
                }
                rel_lower = fh.relationship.lower()
                for key, (code_val, display) in relationship_codes.items():
                    if key in rel_lower:
                        rel_code.set("code", code_val)
                        rel_code.set("displayName", display)
                        break
                else:
                    rel_code.set("code", "FAMMEMB")
                    rel_code.set("displayName", "Family member")
                rel_code.set("codeSystem", "2.16.840.1.113883.5.111")
                rel_code.set("codeSystemName", "RoleCode")

                # Deceased indicator
                if fh.deceased:
                    deceased_ind = ET.SubElement(related_subject, "deceasedInd")
                    deceased_ind.set("value", "true")
                    if fh.death_age:
                        deceased_time = ET.SubElement(related_subject, "deceasedTime")
                        deceased_time.set("value", str(fh.death_age))

                # Family history observation (the condition)
                component = ET.SubElement(organizer, "component")
                obs = ET.SubElement(component, "observation")
                obs.set("classCode", "OBS")
                obs.set("moodCode", "EVN")

                # Family history observation template
                obs_template = ET.SubElement(obs, "templateId")
                obs_template.set("root", "2.16.840.1.113883.10.20.22.4.46")
                obs_template.set("extension", "2015-08-01")

                obs_id = ET.SubElement(obs, "id")
                obs_id.set("root", generate_uuid())

                obs_code = ET.SubElement(obs, "code")
                obs_code.set("code", "64572001")
                obs_code.set("codeSystem", "2.16.840.1.113883.6.96")
                obs_code.set("displayName", "Condition")

                obs_status = ET.SubElement(obs, "statusCode")
                obs_status.set("code", "completed")

                # Age at onset
                if fh.onset_age:
                    obs_eff = ET.SubElement(obs, "effectiveTime")
                    obs_eff.set("value", str(fh.onset_age))

                # The condition
                value = ET.SubElement(obs, "value")
                value.set(f"{{{self.NS_XSI}}}type", "CD")
                if fh.code:
                    value.set("code", fh.code.code)
                    value.set("codeSystem", "2.16.840.1.113883.6.96")
                    value.set("codeSystemName", "SNOMED CT")
                value.set("displayName", fh.condition)
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No family history recorded"

    def _add_growth_data_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add growth data section (pediatric-specific)."""
        # Use a custom section for growth data with percentiles
        component = ET.SubElement(parent, "component")
        section = ET.SubElement(component, "section")

        # No standard template for growth charts, use generic
        template = ET.SubElement(section, "templateId")
        template.set("root", "2.16.840.1.113883.10.20.22.2.4.1")  # Vitals template

        code = ET.SubElement(section, "code")
        code.set("code", "8716-3")
        code.set("codeSystem", "2.16.840.1.113883.6.1")
        code.set("codeSystemName", "LOINC")
        code.set("displayName", "Vital signs")

        title = ET.SubElement(section, "title")
        title.text = "Growth Data"

        text = ET.SubElement(section, "text")

        if patient.growth_data:
            # Narrative table with percentiles
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Date", "Age", "Weight", "Wt %ile", "Height", "Ht %ile", "HC", "HC %ile", "BMI", "BMI %ile"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for gm in sorted(patient.growth_data, key=lambda x: x.date, reverse=True)[:20]:
                tr = ET.SubElement(tbody, "tr")
                td = ET.SubElement(tr, "td")
                td.text = str(gm.date)
                td = ET.SubElement(tr, "td")
                # Convert age_in_days to readable format
                months = gm.age_in_days // 30
                if months < 24:
                    td.text = f"{months} mo"
                else:
                    td.text = f"{months // 12} yr"
                td = ET.SubElement(tr, "td")
                td.text = f"{gm.weight_kg:.1f} kg" if gm.weight_kg else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{gm.weight_percentile:.0f}%" if gm.weight_percentile else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{gm.height_cm:.1f} cm" if gm.height_cm else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{gm.height_percentile:.0f}%" if gm.height_percentile else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{gm.head_circumference_cm:.1f} cm" if gm.head_circumference_cm else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{gm.hc_percentile:.0f}%" if gm.hc_percentile else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{gm.bmi:.1f}" if gm.bmi else ""
                td = ET.SubElement(tr, "td")
                td.text = f"{gm.bmi_percentile:.0f}%" if gm.bmi_percentile else ""

            # Structured entries for growth measurements with percentiles
            for gm in patient.growth_data:
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                organizer = ET.SubElement(entry, "organizer")
                organizer.set("classCode", "CLUSTER")
                organizer.set("moodCode", "EVN")

                template = ET.SubElement(organizer, "templateId")
                template.set("root", "2.16.840.1.113883.10.20.22.4.26")

                org_id = ET.SubElement(organizer, "id")
                org_id.set("root", generate_uuid())

                org_code = ET.SubElement(organizer, "code")
                org_code.set("code", "46680005")
                org_code.set("codeSystem", "2.16.840.1.113883.6.96")
                org_code.set("displayName", "Vital signs")

                org_status = ET.SubElement(organizer, "statusCode")
                org_status.set("code", "completed")

                eff_time = ET.SubElement(organizer, "effectiveTime")
                eff_time.set("value", format_date(gm.date))

                # Add measurements with percentiles
                if gm.weight_kg:
                    self._add_growth_observation(organizer, "29463-7", "Body weight",
                        gm.weight_kg, "kg", gm.weight_percentile)
                if gm.height_cm:
                    self._add_growth_observation(organizer, "8302-2", "Body height",
                        gm.height_cm, "cm", gm.height_percentile)
                if gm.head_circumference_cm:
                    self._add_growth_observation(organizer, "9843-4", "Head circumference",
                        gm.head_circumference_cm, "cm", gm.hc_percentile)
                if gm.bmi:
                    self._add_growth_observation(organizer, "39156-5", "BMI",
                        gm.bmi, "kg/m2", gm.bmi_percentile)
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No growth data recorded"

    def _add_growth_observation(self, parent: ET.Element, loinc_code: str,
                                 display_name: str, value: float, unit: str,
                                 percentile: float | None) -> None:
        """Add a growth observation with optional percentile."""
        component = ET.SubElement(parent, "component")
        obs = ET.SubElement(component, "observation")
        obs.set("classCode", "OBS")
        obs.set("moodCode", "EVN")

        template = ET.SubElement(obs, "templateId")
        template.set("root", "2.16.840.1.113883.10.20.22.4.27")

        obs_id = ET.SubElement(obs, "id")
        obs_id.set("root", generate_uuid())

        code = ET.SubElement(obs, "code")
        code.set("code", loinc_code)
        code.set("codeSystem", "2.16.840.1.113883.6.1")
        code.set("displayName", display_name)

        obs_status = ET.SubElement(obs, "statusCode")
        obs_status.set("code", "completed")

        val = ET.SubElement(obs, "value")
        val.set(f"{{{self.NS_XSI}}}type", "PQ")
        val.set("value", str(value))
        val.set("unit", unit)

        # Add percentile as reference range interpretation
        if percentile is not None:
            interp = ET.SubElement(obs, "interpretationCode")
            if percentile < 5:
                interp.set("code", "L")
                interp.set("displayName", f"Below 5th percentile ({percentile:.0f}%)")
            elif percentile > 95:
                interp.set("code", "H")
                interp.set("displayName", f"Above 95th percentile ({percentile:.0f}%)")
            else:
                interp.set("code", "N")
                interp.set("displayName", f"{percentile:.0f}th percentile")
            interp.set("codeSystem", "2.16.840.1.113883.5.83")

    def _add_developmental_milestones_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add developmental milestones section (pediatric-specific)."""
        component = ET.SubElement(parent, "component")
        section = ET.SubElement(component, "section")

        # Use functional status section template
        template = ET.SubElement(section, "templateId")
        template.set("root", "2.16.840.1.113883.10.20.22.2.14")

        code = ET.SubElement(section, "code")
        code.set("code", "47420-5")
        code.set("codeSystem", "2.16.840.1.113883.6.1")
        code.set("codeSystemName", "LOINC")
        code.set("displayName", "Functional status assessment")

        title = ET.SubElement(section, "title")
        title.text = "Developmental Milestones"

        text = ET.SubElement(section, "text")

        if patient.developmental_milestones:
            # Group milestones by domain
            domains = {}
            for ms in patient.developmental_milestones:
                if ms.domain not in domains:
                    domains[ms.domain] = []
                domains[ms.domain].append(ms)

            for domain, milestones in domains.items():
                # Domain header
                para = ET.SubElement(text, "paragraph")
                para.text = domain.replace("-", " ").title()

                ul = ET.SubElement(text, "list")
                for ms in sorted(milestones, key=lambda x: x.expected_age_months):
                    li = ET.SubElement(ul, "item")
                    status = "Achieved" if ms.achieved else "Not yet achieved"
                    li.text = f"{ms.milestone} (expected: {ms.expected_age_months} mo) - {status}"
                    if ms.achieved_age_months:
                        li.text += f" at {ms.achieved_age_months} mo"

            # Structured entries
            for ms in patient.developmental_milestones:
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                obs = ET.SubElement(entry, "observation")
                obs.set("classCode", "OBS")
                obs.set("moodCode", "EVN")

                # Functional status observation template
                obs_template = ET.SubElement(obs, "templateId")
                obs_template.set("root", "2.16.840.1.113883.10.20.22.4.67")
                obs_template.set("extension", "2014-06-09")

                obs_id = ET.SubElement(obs, "id")
                obs_id.set("root", generate_uuid())

                obs_code = ET.SubElement(obs, "code")
                obs_code.set("code", "54522-8")
                obs_code.set("codeSystem", "2.16.840.1.113883.6.1")
                obs_code.set("displayName", "Developmental milestone")

                obs_status = ET.SubElement(obs, "statusCode")
                obs_status.set("code", "completed")

                if ms.achieved_date:
                    obs_eff = ET.SubElement(obs, "effectiveTime")
                    obs_eff.set("value", format_date(ms.achieved_date))

                # Value - the milestone description
                value = ET.SubElement(obs, "value")
                value.set(f"{{{self.NS_XSI}}}type", "CD")
                domain_codes = {
                    "gross-motor": "282716004",
                    "fine-motor": "282717008",
                    "language": "61909002",
                    "social-emotional": "225597007",
                    "cognitive": "311465003",
                }
                if ms.domain in domain_codes:
                    value.set("code", domain_codes[ms.domain])
                value.set("displayName", f"{ms.domain}: {ms.milestone}")
                value.set("codeSystem", "2.16.840.1.113883.6.96")

                # Interpretation - achieved or not
                interp = ET.SubElement(obs, "interpretationCode")
                if ms.achieved:
                    interp.set("code", "N")
                    interp.set("displayName", "Achieved on time" if ms.achieved_age_months and ms.achieved_age_months <= ms.expected_age_months else "Achieved")
                else:
                    interp.set("code", "A")
                    interp.set("displayName", "Not yet achieved")
                interp.set("codeSystem", "2.16.840.1.113883.5.83")
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No developmental milestones recorded"


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
