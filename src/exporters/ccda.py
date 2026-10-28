"""
CCDA (Consolidated Clinical Document Architecture) Exporter for Oread.

Exports patient data to CDA R2 format following HL7 C-CDA 2.1 templates.
Reference: https://www.hl7.org/ccdasearch/
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET
from xml.dom import minidom

from dateutil.relativedelta import relativedelta

from src.exporters.codes import (
    ACT_REASON_OID,
    CDC_RACE_ETH_OID,
    NCI_OID,
    NPI_OID,
    OMB_ETHNICITY,
    OMB_RACE,
    encounter_cpt,
    is_placeholder_code,
    lab_ucum,
    language_code,
    not_done_reason,
    oread_oid,
    parse_number,
    route_info,
    system_oid,
)
from src.exporters.measurements import vital_sessions
from src.exporters.sig import build_sig
from src.models import (
    ImmunizationStatus,
    Patient,
    Sex,
)

# Characters XML 1.0 forbids (C0 controls except tab/LF/CR, plus U+FFFE/FFFF).
# minidom refuses to reparse a document containing them.
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


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


def format_ts(dt: date | datetime | None) -> str:
    """Date-only values as YYYYMMDD, datetimes as YYYYMMDDHHMMSS."""
    if isinstance(dt, datetime):
        return format_datetime(dt)
    return format_date(dt)


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def clean_xml_text(value: str | None) -> str | None:
    """Drop characters that are not allowed in XML 1.0."""
    if value is None:
        return None
    return _XML_INVALID.sub("", value)


def _num(value: float) -> str:
    """Render a number without a trailing .0 (PQ values)."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


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
        "us_realm": "2.16.840.1.113883.10.20.22.1.1",
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
        "functional_status": "2.16.840.1.113883.10.20.22.2.14",
    }

    # Template version extensions in C-CDA R2.1 (HL7 C-CDA R2.1, Aug 2015;
    # templates that were not revised in 2015 keep their 2014-06-09 version).
    TEMPLATE_EXT = {
        "us_realm": "2015-08-01",
        "ccd": "2015-08-01",
        "problems": "2015-08-01",
        "medications": "2014-06-09",
        "allergies": "2015-08-01",
        "immunizations": "2015-08-01",
        "encounters": "2015-08-01",
        "vitals": "2015-08-01",
        "results": "2015-08-01",
        "procedures": "2014-06-09",
        "social_history": "2015-08-01",
        "family_history": "2015-08-01",
        "functional_status": "2014-06-09",
    }

    def __init__(self):
        self.document_id = generate_uuid()
        # Register namespaces so ElementTree uses proper prefixes
        ET.register_namespace('', self.NS_HL7)
        ET.register_namespace('xsi', self.NS_XSI)
        ET.register_namespace('sdtc', self.NS_SDTC)

    # ------------------------------------------------------------------ #
    # Coding helpers                                                     #
    # ------------------------------------------------------------------ #

    def _set_code(self, el: ET.Element, concept, display: str | None = None,
                  default_system: str | None = None) -> None:
        """Fill a CD/CE element from a CodeableConcept.

        Unknown systems and placeholder codes become nullFlavor="OTH" with
        originalText, never a bare displayName or a fake code.
        """
        display = display or (concept.display if concept else None)
        code = concept.code if concept else None
        mapped = system_oid(concept.system if concept else default_system) \
            if (concept or default_system) else None
        if concept is not None and not is_placeholder_code(code) and mapped:
            oid, name = mapped
            el.set("code", code)
            el.set("codeSystem", oid)
            if name:
                el.set("codeSystemName", name)
            if display:
                el.set("displayName", display)
            return
        self._set_uncoded(el, display)

    def _set_uncoded(self, el: ET.Element, text: str | None) -> None:
        el.set("nullFlavor", "OTH" if text else "UNK")
        if text:
            orig = ET.SubElement(el, "originalText")
            orig.text = text

    def _set_route(self, parent: ET.Element, route: str | None) -> None:
        """routeCode from the NCI Thesaurus route value set."""
        if not route:
            return
        el = ET.SubElement(parent, "routeCode")
        info = route_info(route)
        if info:
            el.set("code", info[0])
            el.set("codeSystem", NCI_OID)
            el.set("codeSystemName", "NCI Thesaurus")
            el.set("displayName", info[1])
        else:
            self._set_uncoded(el, route)

    @staticmethod
    def _template(parent: ET.Element, root: str, ext: str | None = None) -> None:
        template = ET.SubElement(parent, "templateId")
        template.set("root", root)
        if ext:
            template.set("extension", ext)

    @staticmethod
    def _id(parent: ET.Element, root: str | None = None, extension: str | None = None) -> None:
        el = ET.SubElement(parent, "id")
        el.set("root", root or generate_uuid())
        if extension:
            el.set("extension", extension)

    @staticmethod
    def _sanitize(root: ET.Element) -> None:
        """Strip XML-invalid control characters from all text and attributes."""
        for el in root.iter():
            if el.text:
                el.text = clean_xml_text(el.text)
            if el.tail:
                el.tail = clean_xml_text(el.tail)
            for key, value in el.attrib.items():
                el.attrib[key] = clean_xml_text(value)

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
        # Growth data lives in the Vitals section (one section per templateId).
        self._add_developmental_milestones_section(structured_body, patient)

        # Convert to string with pretty printing
        self._sanitize(root)
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

        # US Realm Header, then CCD document template
        self._template(root, self.TEMPLATES["us_realm"], self.TEMPLATE_EXT["us_realm"])
        self._template(root, self.TEMPLATES["ccd"], self.TEMPLATE_EXT["ccd"])

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
        lang.set("code", "en")

    def _add_record_target(self, root: ET.Element, patient: Patient) -> None:
        """Add patient demographics (US Realm Header recordTarget)."""
        demo = patient.demographics
        record_target = ET.SubElement(root, "recordTarget")
        patient_role = ET.SubElement(record_target, "patientRole")

        # Patient ID
        self._id(patient_role, oread_oid("patient"), patient.id)

        # Address
        addr = ET.SubElement(patient_role, "addr")
        if demo.address:
            addr.set("use", "HP")  # Home primary
            for line in (demo.address.line1, demo.address.line2):
                if line:
                    street = ET.SubElement(addr, "streetAddressLine")
                    street.text = line
            for tag, value in (("city", demo.address.city), ("state", demo.address.state),
                               ("postalCode", demo.address.postal_code),
                               ("country", demo.address.country or "US")):
                el = ET.SubElement(addr, tag)
                el.text = value
        else:
            addr.set("nullFlavor", "UNK")

        # Phone
        telecom = ET.SubElement(patient_role, "telecom")
        if demo.phone:
            telecom.set("use", "HP")
            telecom.set("value", f"tel:{demo.phone}")
        else:
            telecom.set("nullFlavor", "UNK")

        # Patient element
        pat = ET.SubElement(patient_role, "patient")

        # Name
        name = ET.SubElement(pat, "name")
        name.set("use", "L")  # Legal

        for given in demo.given_names:
            given_el = ET.SubElement(name, "given")
            given_el.text = given

        family = ET.SubElement(name, "family")
        family.text = demo.family_name

        # Administrative gender (HL7 AdministrativeGender: M, F, UN)
        gender = ET.SubElement(pat, "administrativeGenderCode")
        gender_codes = {Sex.MALE: ("M", "Male"), Sex.FEMALE: ("F", "Female"),
                        Sex.INTERSEX: ("UN", "Undifferentiated")}
        if demo.sex_at_birth in gender_codes:
            code_val, display = gender_codes[demo.sex_at_birth]
            gender.set("code", code_val)
            gender.set("displayName", display)
            gender.set("codeSystem", "2.16.840.1.113883.5.1")
            gender.set("codeSystemName", "AdministrativeGender")
        else:
            gender.set("nullFlavor", "UNK")

        # Birth time
        birth = ET.SubElement(pat, "birthTime")
        birth.set("value", format_date(demo.date_of_birth))

        # Race: first OMB category in raceCode, others in sdtc:raceCode.
        races = [OMB_RACE.get(r.strip().lower()) for r in demo.race]
        coded = [r for r in races if r]
        race = ET.SubElement(pat, "raceCode")
        if coded and len(coded) == len(races):
            self._set_race_eth(race, coded[0])
        else:
            # "Two or more races" or free text with no single OMB category
            race.set("nullFlavor", "OTH" if demo.race else "UNK")
            coded = []
        for extra in coded[1:]:
            self._set_race_eth(ET.SubElement(pat, f"{{{self.NS_SDTC}}}raceCode"), extra)

        # Ethnicity
        ethnicity = ET.SubElement(pat, "ethnicGroupCode")
        eth = OMB_ETHNICITY.get((demo.ethnicity or "").strip().lower())
        if eth:
            self._set_race_eth(ethnicity, eth)
        else:
            ethnicity.set("nullFlavor", "OTH" if demo.ethnicity else "UNK")

        # Language (BCP 47)
        lang_comm = ET.SubElement(pat, "languageCommunication")
        lang_code = ET.SubElement(lang_comm, "languageCode")
        lang_code.set("code", language_code(demo.preferred_language))
        pref = ET.SubElement(lang_comm, "preferenceInd")
        pref.set("value", "true")

    @staticmethod
    def _set_race_eth(el: ET.Element, coded: tuple[str, str]) -> None:
        el.set("code", coded[0])
        el.set("displayName", coded[1])
        el.set("codeSystem", CDC_RACE_ETH_OID)
        el.set("codeSystemName", "CDC Race and Ethnicity")

    @staticmethod
    def _null_addr_telecom(parent: ET.Element) -> None:
        addr = ET.SubElement(parent, "addr")
        addr.set("nullFlavor", "NI")
        telecom = ET.SubElement(parent, "telecom")
        telecom.set("nullFlavor", "NI")

    def _add_author(self, root: ET.Element) -> None:
        """Add document author (the generating software)."""
        author = ET.SubElement(root, "author")

        time = ET.SubElement(author, "time")
        time.set("value", format_datetime(datetime.now()))

        assigned_author = ET.SubElement(author, "assignedAuthor")
        self._id(assigned_author, oread_oid("author"), "oread-system")
        self._null_addr_telecom(assigned_author)

        device = ET.SubElement(assigned_author, "assignedAuthoringDevice")
        model = ET.SubElement(device, "manufacturerModelName")
        model.text = "Oread"
        software = ET.SubElement(device, "softwareName")
        software.text = "Oread Synthetic Patient Generator"

        represented_org = ET.SubElement(assigned_author, "representedOrganization")
        self._id(represented_org, oread_oid("organization"))
        org_name = ET.SubElement(represented_org, "name")
        org_name.text = "Oread Synthetic Patient Generator"

    def _add_custodian(self, root: ET.Element) -> None:
        """Add document custodian."""
        custodian = ET.SubElement(root, "custodian")
        assigned_custodian = ET.SubElement(custodian, "assignedCustodian")
        represented_org = ET.SubElement(assigned_custodian, "representedCustodianOrganization")

        self._id(represented_org, oread_oid("organization"))

        org_name = ET.SubElement(represented_org, "name")
        org_name.text = "Oread Synthetic Patient Generator"
        telecom = ET.SubElement(represented_org, "telecom")
        telecom.set("nullFlavor", "NI")
        addr = ET.SubElement(represented_org, "addr")
        addr.set("nullFlavor", "NI")

    def _add_section(self, parent: ET.Element, key: str,
                     loinc_code: str, title: str) -> ET.Element:
        """Add a standard section structure."""
        component = ET.SubElement(parent, "component")
        section = ET.SubElement(component, "section")

        # Template ID with its C-CDA R2.1 version
        self._template(section, self.TEMPLATES[key], self.TEMPLATE_EXT[key])

        # Code
        code = ET.SubElement(section, "code")
        code.set("code", loinc_code)
        code.set("codeSystem", "2.16.840.1.113883.6.1")
        code.set("codeSystemName", "LOINC")
        code.set("displayName", title)

        # Title
        title_el = ET.SubElement(section, "title")
        title_el.text = title

        return section

    def _add_problems_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add problems/conditions section with proper C-CDA structure."""
        section = self._add_section(
            parent,
            "problems",
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
                li.text += f" (onset {condition.onset_date}"
                if condition.abatement_date:
                    li.text += f", resolved {condition.abatement_date}"
                li.text += ")"
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No known problems"

        active_statuses = {"active", "recurrence", "relapse"}

        # Structured entries for each problem
        for idx, condition in enumerate(patient.problem_list):
            entry = ET.SubElement(section, "entry")
            entry.set("typeCode", "DRIV")
            is_active = condition.clinical_status.value in active_statuses

            # Problem Concern Act
            act = ET.SubElement(entry, "act")
            act.set("classCode", "ACT")
            act.set("moodCode", "EVN")
            self._template(act, "2.16.840.1.113883.10.20.22.4.3", "2015-08-01")
            self._id(act, oread_oid("condition"), condition.id)

            act_code = ET.SubElement(act, "code")
            act_code.set("code", "CONC")
            act_code.set("codeSystem", "2.16.840.1.113883.5.6")
            act_code.set("displayName", "Concern")

            act_status = ET.SubElement(act, "statusCode")
            act_status.set("code", "active" if is_active else "completed")

            # Concern tracking period: low SHALL; high when the concern is closed
            act_eff = ET.SubElement(act, "effectiveTime")
            low = ET.SubElement(act_eff, "low")
            low.set("value", format_date(condition.onset_date))
            if condition.abatement_date:
                high = ET.SubElement(act_eff, "high")
                high.set("value", format_date(condition.abatement_date))
            elif not is_active:
                high = ET.SubElement(act_eff, "high")
                high.set("nullFlavor", "UNK")

            # Problem Observation (entryRelationship)
            entry_rel = ET.SubElement(act, "entryRelationship")
            entry_rel.set("typeCode", "SUBJ")

            obs = ET.SubElement(entry_rel, "observation")
            obs.set("classCode", "OBS")
            obs.set("moodCode", "EVN")
            self._template(obs, "2.16.840.1.113883.10.20.22.4.4", "2015-08-01")
            self._id(obs)

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

            # Biological onset (low) and resolution (high)
            obs_eff = ET.SubElement(obs, "effectiveTime")
            onset_low = ET.SubElement(obs_eff, "low")
            onset_low.set("value", format_date(condition.onset_date))
            if condition.abatement_date:
                onset_high = ET.SubElement(obs_eff, "high")
                onset_high.set("value", format_date(condition.abatement_date))
            elif not is_active:
                onset_high = ET.SubElement(obs_eff, "high")
                onset_high.set("nullFlavor", "UNK")

            # Value (the actual condition code)
            value = ET.SubElement(obs, "value")
            value.set(f"{{{self.NS_XSI}}}type", "CD")
            self._set_code(value, condition.code, condition.display_name,
                           default_system="http://hl7.org/fhir/sid/icd-10-cm")

    # Medication status -> Medication Activity statusCode (HL7 ActStatus).
    MED_STATUS = {"active": "active", "completed": "completed", "stopped": "aborted",
                  "on-hold": "suspended"}

    def _add_medications_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add medications section with structured entries."""
        section = self._add_section(
            parent,
            "medications",
            "10160-0",
            "Medications"
        )

        text = ET.SubElement(section, "text")
        # Include ALL medications (active, completed, discontinued, etc.)
        all_meds = list(patient.medication_list)
        sigs = [build_sig(med) for med in all_meds]

        if all_meds:
            # Narrative table
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Medication", "Sig", "Route", "Start Date", "End Date", "Status"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for idx, (med, sig) in enumerate(zip(all_meds, sigs)):
                tr = ET.SubElement(tbody, "tr")
                tr.set("ID", f"med{idx}")
                td = ET.SubElement(tr, "td")
                td.text = med.display_name
                td = ET.SubElement(tr, "td")
                td.set("ID", f"medsig{idx}")
                td.text = sig.text
                td = ET.SubElement(tr, "td")
                td.text = med.route or ""
                td = ET.SubElement(tr, "td")
                td.text = str(med.start_date) if med.start_date else ""
                td = ET.SubElement(tr, "td")
                td.text = str(med.end_date) if med.end_date else ""
                td = ET.SubElement(tr, "td")
                td.text = med.status.value.title() if med.status else "Active"

            # Structured entries for each medication
            for idx, (med, sig) in enumerate(zip(all_meds, sigs)):
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")
                self._add_medication_activity(entry, med, sig, idx)
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No medications on record"

    def _add_medication_activity(self, entry: ET.Element, med, sig, idx: int) -> None:
        """Medication Activity (2.16.840.1.113883.10.20.22.4.16), elements in CDA schema order."""
        timing = sig.timing
        subst_admin = ET.SubElement(entry, "substanceAdministration")
        subst_admin.set("classCode", "SBADM")
        subst_admin.set("moodCode", "EVN")
        self._template(subst_admin, "2.16.840.1.113883.10.20.22.4.16", "2014-06-09")
        self._id(subst_admin, oread_oid("medication"), med.id)

        # Reference to narrative
        med_text = ET.SubElement(subst_admin, "text")
        ref = ET.SubElement(med_text, "reference")
        ref.set("value", f"#med{idx}")

        status = ET.SubElement(subst_admin, "statusCode")
        status.set("code", self.MED_STATUS.get(med.status.value, "completed"))

        # Effective time (medication period)
        eff_time = ET.SubElement(subst_admin, "effectiveTime")
        eff_time.set(f"{{{self.NS_XSI}}}type", "IVL_TS")
        low = ET.SubElement(eff_time, "low")
        low.set("value", format_date(med.start_date))
        high = ET.SubElement(eff_time, "high")
        if med.end_date:
            high.set("value", format_date(med.end_date))
        elif timing.once:
            high.set("value", format_date(med.start_date))
        else:
            high.set("nullFlavor", "UNK")

        # Frequency. PRN meds have no fixed schedule: the interval lives in the
        # sig and the condition in <precondition>, not in a PIVL period.
        if timing.has_fixed_period:
            freq_time = ET.SubElement(subst_admin, "effectiveTime")
            freq_time.set(f"{{{self.NS_XSI}}}type", "PIVL_TS")
            freq_time.set("operator", "A")
            # BID/daily mean "at the clinic's usual times"; q8h is a strict interval.
            freq_time.set("institutionSpecified",
                          "true" if timing.institution_specified else "false")
            period = ET.SubElement(freq_time, "period")
            period.set("value", _num(timing.period))
            period.set("unit", timing.period_unit)
        elif timing.event and not timing.prn:
            freq_time = ET.SubElement(subst_admin, "effectiveTime")
            freq_time.set(f"{{{self.NS_XSI}}}type", "EIVL_TS")
            freq_time.set("operator", "A")
            event = ET.SubElement(freq_time, "event")
            event.set("code", timing.event)

        self._set_route(subst_admin, med.route)

        # Dose quantity: numeric per-administration amount with a UCUM unit
        # (or unitless for countable products). Anything else is sig-only.
        if sig.dose and sig.unit_known:
            dose = ET.SubElement(subst_admin, "doseQuantity")
            low_val, high_val = sig.dose
            if high_val is None:
                dose.set("value", _num(low_val))
                if sig.ucum:
                    dose.set("unit", sig.ucum)
            else:
                for tag, v in (("low", low_val), ("high", high_val)):
                    bound = ET.SubElement(dose, tag)
                    bound.set("value", _num(v))
                    if sig.ucum:
                        bound.set("unit", sig.ucum)

        # Divided dosing: state the daily maximum explicitly
        if sig.daily_dose is not None and sig.unit_known:
            max_dose = ET.SubElement(subst_admin, "maxDoseQuantity")
            num = ET.SubElement(max_dose, "numerator")
            num.set("value", _num(sig.daily_dose))
            if sig.ucum:
                num.set("unit", sig.ucum)
            den = ET.SubElement(max_dose, "denominator")
            den.set("value", "1")
            den.set("unit", "d")

        # Consumable (the medication itself)
        consumable = ET.SubElement(subst_admin, "consumable")
        manuf_product = ET.SubElement(consumable, "manufacturedProduct")
        manuf_product.set("classCode", "MANU")
        self._template(manuf_product, "2.16.840.1.113883.10.20.22.4.23", "2014-06-09")
        manuf_material = ET.SubElement(manuf_product, "manufacturedMaterial")
        code = ET.SubElement(manuf_material, "code")
        self._set_code(code, med.code, med.display_name,
                       default_system="http://www.nlm.nih.gov/research/umls/rxnorm")

        # Indication (reason for medication)
        if med.indication:
            entry_rel = ET.SubElement(subst_admin, "entryRelationship")
            entry_rel.set("typeCode", "RSON")

            ind_obs = ET.SubElement(entry_rel, "observation")
            ind_obs.set("classCode", "OBS")
            ind_obs.set("moodCode", "EVN")
            self._template(ind_obs, "2.16.840.1.113883.10.20.22.4.19", "2014-06-09")
            self._id(ind_obs)

            ind_code = ET.SubElement(ind_obs, "code")
            ind_code.set("code", "75321-0")
            ind_code.set("codeSystem", "2.16.840.1.113883.6.1")
            ind_code.set("displayName", "Clinical finding")

            ind_status = ET.SubElement(ind_obs, "statusCode")
            ind_status.set("code", "completed")

            ind_val = ET.SubElement(ind_obs, "value")
            ind_val.set(f"{{{self.NS_XSI}}}type", "CD")
            self._set_uncoded(ind_val, med.indication)

        # Medication Free Text Sig (2.16.840.1.113883.10.20.22.4.147)
        sig_rel = ET.SubElement(subst_admin, "entryRelationship")
        sig_rel.set("typeCode", "COMP")
        sig_sa = ET.SubElement(sig_rel, "substanceAdministration")
        sig_sa.set("classCode", "SBADM")
        sig_sa.set("moodCode", "EVN")
        self._template(sig_sa, "2.16.840.1.113883.10.20.22.4.147")
        sig_code = ET.SubElement(sig_sa, "code")
        sig_code.set("code", "76662-6")
        sig_code.set("codeSystem", "2.16.840.1.113883.6.1")
        sig_code.set("codeSystemName", "LOINC")
        sig_code.set("displayName", "Instructions Medication")
        sig_text = ET.SubElement(sig_sa, "text")
        sig_ref = ET.SubElement(sig_text, "reference")
        sig_ref.set("value", f"#medsig{idx}")
        sig_ref.tail = sig.text
        sig_cons = ET.SubElement(sig_sa, "consumable")
        sig_prod = ET.SubElement(sig_cons, "manufacturedProduct")
        sig_mat = ET.SubElement(sig_prod, "manufacturedLabeledDrug")
        sig_mat.set("nullFlavor", "NA")

        # PRN: Precondition for Substance Administration (4.25)
        if timing.prn:
            precondition = ET.SubElement(subst_admin, "precondition")
            precondition.set("typeCode", "PRCN")
            self._template(precondition, "2.16.840.1.113883.10.20.22.4.25", "2014-06-09")
            criterion = ET.SubElement(precondition, "criterion")
            crit_code = ET.SubElement(criterion, "code")
            crit_code.set("code", "ASSERTION")
            crit_code.set("codeSystem", "2.16.840.1.113883.5.4")
            crit_val = ET.SubElement(criterion, "value")
            crit_val.set(f"{{{self.NS_XSI}}}type", "CD")
            self._set_uncoded(crit_val, med.prn_reason or med.indication or "as needed")

    def _add_allergies_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add allergies section with structured entries."""
        section = self._add_section(
            parent,
            "allergies",
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
                onset_low = ET.SubElement(obs_eff, "low")
                if allergy.onset_date:
                    onset_low.set("value", format_date(allergy.onset_date))
                else:
                    onset_low.set("nullFlavor", "UNK")

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
                self._set_code(allergen_code, allergy.code, allergy.display_name)

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
                    self._set_uncoded(reaction_val, reaction.manifestation)

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
        """Add immunizations section with structured entries.

        Refused / not-given doses are Immunization Activities with
        negationInd="true" and an Immunization Refusal Reason (4.53).
        """
        section = self._add_section(
            parent,
            "immunizations",
            "11369-6",
            "Immunizations"
        )

        text = ET.SubElement(section, "text")
        records = sorted(patient.immunization_record, key=lambda i: i.date)
        if records:
            # Narrative table
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Vaccine", "Date", "Dose", "Status", "Lot #", "Manufacturer"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for idx, imm in enumerate(records):
                tr = ET.SubElement(tbody, "tr")
                tr.set("ID", f"imm{idx}")
                td = ET.SubElement(tr, "td")
                td.text = imm.display_name
                td = ET.SubElement(tr, "td")
                td.text = str(imm.date) if imm.date else ""
                td = ET.SubElement(tr, "td")
                td.text = str(imm.dose_number) if imm.dose_number else ""
                td = ET.SubElement(tr, "td")
                if imm.status == ImmunizationStatus.COMPLETED:
                    td.text = "Given"
                elif imm.status == ImmunizationStatus.NOT_DONE:
                    td.text = "Not given" + (f": {imm.status_reason}" if imm.status_reason else "")
                else:
                    td.text = "Entered in error"
                td = ET.SubElement(tr, "td")
                td.text = imm.lot_number or ""
                td = ET.SubElement(tr, "td")
                td.text = imm.manufacturer or ""

            # Structured entries for each immunization
            for idx, imm in enumerate(records):
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")
                self._add_immunization_activity(entry, imm, idx)
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No immunization records"

    def _add_immunization_activity(self, entry: ET.Element, imm, idx: int) -> None:
        """Immunization Activity (2.16.840.1.113883.10.20.22.4.52)."""
        not_done = imm.status == ImmunizationStatus.NOT_DONE
        subst_admin = ET.SubElement(entry, "substanceAdministration")
        subst_admin.set("classCode", "SBADM")
        subst_admin.set("moodCode", "EVN")
        subst_admin.set("negationInd", "true" if not_done else "false")
        self._template(subst_admin, "2.16.840.1.113883.10.20.22.4.52", "2015-08-01")
        self._id(subst_admin, oread_oid("immunization"), imm.id)

        imm_text = ET.SubElement(subst_admin, "text")
        ref = ET.SubElement(imm_text, "reference")
        ref.set("value", f"#imm{idx}")

        # A refusal is a completed act of not giving the vaccine (negated EVN);
        # entered-in-error records are nullified.
        status = ET.SubElement(subst_admin, "statusCode")
        status.set("code", "nullified"
                   if imm.status == ImmunizationStatus.ENTERED_IN_ERROR else "completed")

        eff_time = ET.SubElement(subst_admin, "effectiveTime")
        eff_time.set("value", format_date(imm.date))

        if not not_done:
            self._set_route(subst_admin, imm.route)

        # Consumable (vaccine product)
        consumable = ET.SubElement(subst_admin, "consumable")
        manuf_product = ET.SubElement(consumable, "manufacturedProduct")
        manuf_product.set("classCode", "MANU")
        self._template(manuf_product, "2.16.840.1.113883.10.20.22.4.54", "2014-06-09")

        manuf_material = ET.SubElement(manuf_product, "manufacturedMaterial")
        code = ET.SubElement(manuf_material, "code")
        self._set_code(code, imm.vaccine_code, imm.display_name,
                       default_system="http://hl7.org/fhir/sid/cvx")

        if imm.lot_number and not not_done:
            lot = ET.SubElement(manuf_material, "lotNumberText")
            lot.text = imm.lot_number

        if imm.manufacturer and not not_done:
            manuf_org = ET.SubElement(manuf_product, "manufacturerOrganization")
            manuf_name = ET.SubElement(manuf_org, "name")
            manuf_name.text = imm.manufacturer

        # Series information (dose number in series)
        if imm.dose_number and imm.series_doses and not not_done:
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

        # Immunization Refusal Reason (2.16.840.1.113883.10.20.22.4.53)
        if not_done:
            entry_rel = ET.SubElement(subst_admin, "entryRelationship")
            entry_rel.set("typeCode", "RSON")
            obs = ET.SubElement(entry_rel, "observation")
            obs.set("classCode", "OBS")
            obs.set("moodCode", "EVN")
            self._template(obs, "2.16.840.1.113883.10.20.22.4.53")
            self._id(obs)
            reason_code = ET.SubElement(obs, "code")
            mapped = not_done_reason(imm.status_reason)
            if mapped:
                reason_code.set("code", mapped[0])
                reason_code.set("codeSystem", ACT_REASON_OID)
                reason_code.set("codeSystemName", "HL7 ActNoImmunizationReason")
                reason_code.set("displayName", mapped[1])
                if imm.status_reason:
                    orig = ET.SubElement(reason_code, "originalText")
                    orig.text = imm.status_reason
            else:
                self._set_uncoded(reason_code, imm.status_reason)
            reason_status = ET.SubElement(obs, "statusCode")
            reason_status.set("code", "completed")

    def _add_encounters_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add encounters section with structured entries and clinical notes."""
        section = self._add_section(
            parent,
            "encounters",
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
            for enc in sorted(patient.encounters, key=lambda e: e.date, reverse=True):
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
                self._template(encounter_el, "2.16.840.1.113883.10.20.22.4.49", "2015-08-01")
                self._id(encounter_el, oread_oid("encounter"), enc.id)

                # Encounter type code: CPT when the visit type determines it
                # (preventive visits by age); problem visits have no modeled
                # E/M level, so nullFlavor rather than a guessed code.
                code = ET.SubElement(encounter_el, "code")
                age_years = relativedelta(_as_date(enc.date),
                                          patient.demographics.date_of_birth).years
                cpt = encounter_cpt(enc.type.value, age_years)
                type_text = enc.type.value.replace("-", " ").title()
                if cpt:
                    code.set("code", cpt[0])
                    code.set("codeSystem", "2.16.840.1.113883.6.12")  # CPT
                    code.set("codeSystemName", "CPT")
                    code.set("displayName", cpt[1])
                else:
                    code.set("nullFlavor", "OTH")

                # Original text (visit type and chief complaint)
                orig_text = ET.SubElement(code, "originalText")
                orig_text.text = type_text + (f": {enc.chief_complaint}" if enc.chief_complaint else "")

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
                    if enc.provider.npi:
                        self._id(assigned_entity, NPI_OID, enc.provider.npi)
                    else:
                        self._id(assigned_entity, oread_oid("provider"), enc.provider.id)

                    assigned_person = ET.SubElement(assigned_entity, "assignedPerson")
                    prov_name = ET.SubElement(assigned_person, "name")
                    prov_name.text = enc.provider.name

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

        # Note Activity (4.202): code SHALL be 34109-9 "Note", with the
        # specific note type as a translation.
        self._template(act, "2.16.840.1.113883.10.20.22.4.202", "2016-11-01")
        self._id(act)

        code = ET.SubElement(act, "code")
        code.set("code", "34109-9")
        code.set("codeSystem", "2.16.840.1.113883.6.1")
        code.set("codeSystemName", "LOINC")
        code.set("displayName", "Note")
        translation = ET.SubElement(code, "translation")
        translation.set("code", loinc_code)
        translation.set("codeSystem", "2.16.840.1.113883.6.1")
        translation.set("codeSystemName", "LOINC")
        translation.set("displayName", title)

        # The actual note text (CDA schema order: text before statusCode)
        text_el = ET.SubElement(act, "text")
        text_el.text = note_text

        status = ET.SubElement(act, "statusCode")
        status.set("code", "completed")

        eff_time = ET.SubElement(act, "effectiveTime")
        eff_time.set("value", format_ts(enc_date))

    # (key, LOINC, display, UCUM). Source: C-CDA Vital Sign Result Type value
    # set (2.16.840.1.113883.3.88.12.80.62) and the US Core vital-sign /
    # pediatric percentile profiles for 59576-9 and 8289-1.
    VITAL_SPECS = [
        ("height", "8302-2", "Body height", "cm"),
        ("weight", "29463-7", "Body weight", "kg"),
        ("bmi", "39156-5", "Body mass index (BMI) [Ratio]", "kg/m2"),
        ("bmi_pct", "59576-9", "Body mass index (BMI) [Percentile] Per age and sex", "%"),
        ("hc", "9843-4", "Head Occipital-frontal circumference", "cm"),
        ("hc_pct", "8289-1", "Head Occipital-frontal circumference Percentile", "%"),
        ("temp", "8310-5", "Body temperature", "[degF]"),
        ("hr", "8867-4", "Heart rate", "/min"),
        ("rr", "9279-1", "Respiratory rate", "/min"),
        ("sbp", "8480-6", "Systolic blood pressure", "mm[Hg]"),
        ("dbp", "8462-4", "Diastolic blood pressure", "mm[Hg]"),
        ("spo2", "59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry", "%"),
    ]

    def _add_vitals_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add the Vital Signs section (encounter vitals + growth measurements)."""
        section = self._add_section(
            parent,
            "vitals",
            "8716-3",
            "Vital Signs"
        )

        text = ET.SubElement(section, "text")
        rows = vital_sessions(patient)

        if rows:
            # Narrative text
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Date", "Height", "Weight", "BMI", "HC", "Temp", "HR", "RR",
                           "BP", "SpO2", "Percentiles"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for row in rows:
                v = row.values()
                gm = row.growth
                pcts = []
                if gm is not None:
                    for label, pct in (("Wt", gm.weight_percentile), ("Ht", gm.height_percentile),
                                       ("HC", gm.hc_percentile), ("BMI", gm.bmi_percentile)):
                        if pct is not None:
                            pcts.append(f"{label} {pct:.0f}%")
                cells = [
                    str(_as_date(row.when)),
                    f"{v['height']} cm" if v["height"] else "",
                    f"{v['weight']} kg" if v["weight"] else "",
                    f"{v['bmi']}" if v["bmi"] else "",
                    f"{v['hc']} cm" if v["hc"] else "",
                    f"{v['temp']} °F" if v["temp"] else "",
                    f"{v['hr']}" if v["hr"] else "",
                    f"{v['rr']}" if v["rr"] else "",
                    f"{v['sbp']}/{v['dbp']}" if v["sbp"] and v["dbp"] else "",
                    f"{v['spo2']}%" if v["spo2"] else "",
                    ", ".join(pcts),
                ]
                tr = ET.SubElement(tbody, "tr")
                for cell in cells:
                    td = ET.SubElement(tr, "td")
                    td.text = cell

            # Structured entries: one Vital Signs Organizer per session
            for row in rows:
                values = row.values()
                if not any(values[k] is not None for k, *_ in self.VITAL_SPECS):
                    continue
                when = row.when
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                organizer = ET.SubElement(entry, "organizer")
                organizer.set("classCode", "CLUSTER")
                organizer.set("moodCode", "EVN")
                self._template(organizer, "2.16.840.1.113883.10.20.22.4.26", "2015-08-01")
                self._id(organizer)

                code = ET.SubElement(organizer, "code")
                code.set("code", "46680005")
                code.set("codeSystem", "2.16.840.1.113883.6.96")
                code.set("codeSystemName", "SNOMED CT")
                code.set("displayName", "Vital signs")
                translation = ET.SubElement(code, "translation")
                translation.set("code", "74728-7")
                translation.set("codeSystem", "2.16.840.1.113883.6.1")
                translation.set("codeSystemName", "LOINC")
                translation.set("displayName", "Vital signs, weight, height, head circumference, "
                                "oximetry, BMI, and BSA panel - HL7.CCDAr1.1")

                status = ET.SubElement(organizer, "statusCode")
                status.set("code", "completed")

                eff_time = ET.SubElement(organizer, "effectiveTime")
                eff_time.set("value", format_ts(when))

                for key, loinc, display, ucum in self.VITAL_SPECS:
                    if values[key] is not None:
                        self._add_vital_observation(organizer, when, loinc, display,
                                                    values[key], ucum)
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No vital signs recorded"

    def _add_vital_observation(self, parent: ET.Element, when: date | datetime,
                               loinc_code: str, display_name: str,
                               value: float, ucum_unit: str) -> None:
        """Add a single Vital Sign Observation (4.27) component."""
        component = ET.SubElement(parent, "component")
        obs = ET.SubElement(component, "observation")
        obs.set("classCode", "OBS")
        obs.set("moodCode", "EVN")
        self._template(obs, "2.16.840.1.113883.10.20.22.4.27", "2014-06-09")
        self._id(obs)

        code = ET.SubElement(obs, "code")
        code.set("code", loinc_code)
        code.set("codeSystem", "2.16.840.1.113883.6.1")
        code.set("codeSystemName", "LOINC")
        code.set("displayName", display_name)

        status = ET.SubElement(obs, "statusCode")
        status.set("code", "completed")

        eff_time = ET.SubElement(obs, "effectiveTime")
        eff_time.set("value", format_ts(when))

        val = ET.SubElement(obs, "value")
        val.set(f"{{{self.NS_XSI}}}type", "PQ")
        val.set("value", _num(float(value)))
        val.set("unit", ucum_unit)

    def _add_results_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add results (laboratory) section.

        Every result sits in a Result Organizer (4.1): panels keep their
        components, and a single test becomes a one-component organizer.
        """
        section = self._add_section(
            parent,
            "results",
            "30954-2",
            "Results"
        )

        text = ET.SubElement(section, "text")

        # Collect lab results from all encounters
        all_results = []
        for enc in patient.encounters:
            for result in enc.lab_results:
                all_results.append((enc.date, result))
        all_results.sort(key=lambda x: x[0], reverse=True)

        if all_results:
            # Narrative table
            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Date", "Test", "Value", "Units", "Reference Range", "Interpretation"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for enc_date, result in all_results:
                for lab in (result.results if hasattr(result, 'results') else [result]):
                    self._add_lab_result_row(tbody, enc_date, lab)

            # Structured entries
            for enc_date, result in all_results:
                is_panel = hasattr(result, 'results')
                labs = result.results if is_panel else [result]
                if not labs:
                    continue
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                organizer = ET.SubElement(entry, "organizer")
                organizer.set("classCode", "BATTERY" if is_panel else "CLUSTER")
                organizer.set("moodCode", "EVN")
                self._template(organizer, "2.16.840.1.113883.10.20.22.4.1", "2015-08-01")
                self._id(organizer)

                code = ET.SubElement(organizer, "code")
                self._set_code(code, result.code, result.display_name,
                               default_system="http://loinc.org")

                status = ET.SubElement(organizer, "statusCode")
                status.set("code", "completed")

                dates = [lab.resulted_date or enc_date for lab in labs]
                eff = ET.SubElement(organizer, "effectiveTime")
                low = ET.SubElement(eff, "low")
                low.set("value", format_ts(min(dates, key=_as_date)))
                high = ET.SubElement(eff, "high")
                high.set("value", format_ts(max(dates, key=_as_date)))

                for lab in labs:
                    self._add_lab_observation(organizer, lab, enc_date)
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No laboratory results"

    def _add_lab_result_row(self, tbody: ET.Element, enc_date, lab) -> None:
        """Add a row to the lab results narrative table."""
        tr = ET.SubElement(tbody, "tr")
        td = ET.SubElement(tr, "td")
        td.text = str(_as_date(enc_date)) if enc_date else ""
        td = ET.SubElement(tr, "td")
        td.text = lab.display_name
        td = ET.SubElement(tr, "td")
        td.text = str(lab.value) if lab.value is not None else ""
        td = ET.SubElement(tr, "td")
        td.text = lab.unit or ""
        td = ET.SubElement(tr, "td")
        rr = lab.reference_range
        if rr and (rr.low is not None or rr.high is not None):
            td.text = f"{'' if rr.low is None else rr.low}-{'' if rr.high is None else rr.high}"
        elif rr and rr.text:
            td.text = rr.text
        else:
            td.text = ""
        td = ET.SubElement(tr, "td")
        td.text = lab.interpretation.value if lab.interpretation else ""

    # Observation interpretation (HL7 v3 ObservationInterpretation)
    INTERPRETATION = {
        "normal": ("N", "Normal"),
        "abnormal": ("A", "Abnormal"),
        "high": ("H", "High"),
        "low": ("L", "Low"),
        "critical": ("AA", "Critical abnormal"),
        "positive": ("POS", "Positive"),
        "negative": ("NEG", "Negative"),
    }

    def _add_lab_observation(self, parent: ET.Element, lab, fallback_date=None) -> None:
        """Add a Result Observation (4.2) as an organizer component."""
        component = ET.SubElement(parent, "component")
        obs = ET.SubElement(component, "observation")

        obs.set("classCode", "OBS")
        obs.set("moodCode", "EVN")
        self._template(obs, "2.16.840.1.113883.10.20.22.4.2", "2015-08-01")
        self._id(obs)

        code = ET.SubElement(obs, "code")
        self._set_code(code, lab.code, lab.display_name, default_system="http://loinc.org")

        status = ET.SubElement(obs, "statusCode")
        status.set("code", "completed")

        eff_time = ET.SubElement(obs, "effectiveTime")
        when = lab.resulted_date or getattr(lab, "collected_date", None) or fallback_date
        if when:
            eff_time.set("value", format_ts(when))
        else:
            eff_time.set("nullFlavor", "UNK")

        # Value: PQ with a UCUM unit when numeric, otherwise ST
        if lab.value is not None and str(lab.value).strip() != "":
            val = ET.SubElement(obs, "value")
            number = parse_number(lab.value)
            unit = lab_ucum(lab.unit)
            if number is not None and (unit or not lab.unit):
                val.set(f"{{{self.NS_XSI}}}type", "PQ")
                val.set("value", _num(number))
                val.set("unit", unit or "1")
            else:
                val.set(f"{{{self.NS_XSI}}}type", "ST")
                val.text = f"{lab.value} {lab.unit}".strip() if number is not None else str(lab.value)

        # Interpretation
        if lab.interpretation and lab.interpretation.value in self.INTERPRETATION:
            interp = ET.SubElement(obs, "interpretationCode")
            code_val, display = self.INTERPRETATION[lab.interpretation.value]
            interp.set("code", code_val)
            interp.set("displayName", display)
            interp.set("codeSystem", "2.16.840.1.113883.5.83")
            interp.set("codeSystemName", "ObservationInterpretation")

        # Reference range
        rr = lab.reference_range
        if rr and (rr.low is not None or rr.high is not None or rr.text):
            ref_range = ET.SubElement(obs, "referenceRange")
            obs_range = ET.SubElement(ref_range, "observationRange")
            if rr.text:
                range_text = ET.SubElement(obs_range, "text")
                range_text.text = rr.text
            if rr.low is not None or rr.high is not None:
                range_unit = lab_ucum(rr.unit or lab.unit) or "1"
                range_val = ET.SubElement(obs_range, "value")
                range_val.set(f"{{{self.NS_XSI}}}type", "IVL_PQ")
                for tag, bound in (("low", rr.low), ("high", rr.high)):
                    if bound is not None:
                        el = ET.SubElement(range_val, tag)
                        el.set("value", _num(float(bound)))
                        el.set("unit", range_unit)

    def _add_procedures_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add procedures section with structured entries."""
        section = self._add_section(
            parent,
            "procedures",
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
            all_procedures.sort(
                key=lambda x: _as_date(x[1].performed_date if hasattr(x[1], 'performed_date') else x[1].date),
                reverse=True)
            for idx, (proc_type, proc) in enumerate(all_procedures):
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
                self._template(procedure, "2.16.840.1.113883.10.20.22.4.14", "2014-06-09")
                self._id(procedure, oread_oid("procedure"), proc.id)

                # Procedure code
                code = ET.SubElement(procedure, "code")
                self._set_code(code, getattr(proc, "code", None), proc.display_name,
                               default_system="http://snomed.info/sct")

                # Reference to narrative
                proc_text = ET.SubElement(procedure, "text")
                ref = ET.SubElement(proc_text, "reference")
                ref.set("value", f"#proc{idx}")

                status = ET.SubElement(procedure, "statusCode")
                proc_status = getattr(proc, "status", "completed")
                status.set("code", {"preparation": "active", "in-progress": "active",
                                    "stopped": "aborted", "not-done": "cancelled"}
                           .get(proc_status, "completed"))

                # Effective time
                eff_time = ET.SubElement(procedure, "effectiveTime")
                proc_date = proc.performed_date if hasattr(proc, 'performed_date') else proc.date
                if proc_date:
                    eff_time.set("value", format_ts(proc_date))
                else:
                    eff_time.set("nullFlavor", "UNK")

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
            "social_history",
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
            "family_history",
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

                # Deceased indicator. Age at death is an age (PQ), not a
                # timestamp, so it is not put in deceasedTime.
                if fh.deceased:
                    subj = ET.SubElement(related_subject, "subject")
                    deceased_ind = ET.SubElement(subj, f"{{{self.NS_SDTC}}}deceasedInd")
                    deceased_ind.set("value", "true")

                # Family history observation (the condition)
                component = ET.SubElement(organizer, "component")
                obs = ET.SubElement(component, "observation")
                obs.set("classCode", "OBS")
                obs.set("moodCode", "EVN")

                # Family history observation template
                obs_template = ET.SubElement(obs, "templateId")
                obs_template.set("root", "2.16.840.1.113883.10.20.22.4.46")
                obs_template.set("extension", "2015-08-01")

                self._id(obs)

                obs_code = ET.SubElement(obs, "code")
                obs_code.set("code", "64572001")
                obs_code.set("codeSystem", "2.16.840.1.113883.6.96")
                obs_code.set("codeSystemName", "SNOMED CT")
                obs_code.set("displayName", "Disease")

                obs_status = ET.SubElement(obs, "statusCode")
                obs_status.set("code", "completed")

                # The condition
                value = ET.SubElement(obs, "value")
                value.set(f"{{{self.NS_XSI}}}type", "CD")
                self._set_code(value, fh.code, fh.condition,
                               default_system="http://snomed.info/sct")

                # Age at onset: Age Observation (4.31) with a PQ in years
                if fh.onset_age is not None:
                    self._add_age_observation(obs, fh.onset_age, "445518008", "Age at onset")

                # Age at death: Family History Death Observation (4.47) + age
                if fh.deceased:
                    death_rel = ET.SubElement(obs, "entryRelationship")
                    death_rel.set("typeCode", "CAUS")
                    death_obs = ET.SubElement(death_rel, "observation")
                    death_obs.set("classCode", "OBS")
                    death_obs.set("moodCode", "EVN")
                    self._template(death_obs, "2.16.840.1.113883.10.20.22.4.47")
                    self._id(death_obs)
                    death_code = ET.SubElement(death_obs, "code")
                    death_code.set("code", "ASSERTION")
                    death_code.set("codeSystem", "2.16.840.1.113883.5.4")
                    death_status = ET.SubElement(death_obs, "statusCode")
                    death_status.set("code", "completed")
                    death_val = ET.SubElement(death_obs, "value")
                    death_val.set(f"{{{self.NS_XSI}}}type", "CD")
                    death_val.set("code", "419099009")
                    death_val.set("codeSystem", "2.16.840.1.113883.6.96")
                    death_val.set("codeSystemName", "SNOMED CT")
                    death_val.set("displayName", "Dead")
                    if fh.death_age is not None:
                        self._add_age_observation(death_obs, fh.death_age, "445518008",
                                                  "Age at death")
        else:
            para = ET.SubElement(text, "paragraph")
            para.text = "No family history recorded"

    def _add_age_observation(self, parent: ET.Element, age_years: int,
                             code_val: str, display: str) -> None:
        """Age Observation (2.16.840.1.113883.10.20.22.4.31): value is PQ in years."""
        rel = ET.SubElement(parent, "entryRelationship")
        rel.set("typeCode", "SUBJ")
        rel.set("inversionInd", "true")
        obs = ET.SubElement(rel, "observation")
        obs.set("classCode", "OBS")
        obs.set("moodCode", "EVN")
        self._template(obs, "2.16.840.1.113883.10.20.22.4.31")
        code = ET.SubElement(obs, "code")
        code.set("code", code_val)
        code.set("codeSystem", "2.16.840.1.113883.6.96")
        code.set("codeSystemName", "SNOMED CT")
        code.set("displayName", display)
        status = ET.SubElement(obs, "statusCode")
        status.set("code", "completed")
        value = ET.SubElement(obs, "value")
        value.set(f"{{{self.NS_XSI}}}type", "PQ")
        value.set("value", str(age_years))
        value.set("unit", "a")

    def _add_developmental_milestones_section(self, parent: ET.Element, patient: Patient) -> None:
        """Add developmental milestones section (pediatric-specific)."""
        # Functional Status section carries developmental screens/milestones
        section = self._add_section(parent, "functional_status", "47420-5",
                                    "Developmental Milestones")

        text = ET.SubElement(section, "text")

        # Collect developmental screens from encounters (well-child visits)
        developmental_screens = []
        for enc in patient.encounters:
            screens = enc.developmental_screens or (
                [enc.developmental_screen] if enc.developmental_screen else [])
            for screen in screens:
                developmental_screens.append((enc.date, screen))

        # Combine patient-level milestones and encounter-based screens
        has_data = patient.developmental_milestones or developmental_screens

        if developmental_screens:
            # Add developmental screening results from encounters
            para = ET.SubElement(text, "paragraph")
            para.text = "Developmental Screening History"

            table = ET.SubElement(text, "table")
            thead = ET.SubElement(table, "thead")
            tr = ET.SubElement(thead, "tr")
            for header in ["Date", "Screening Tool", "Result", "Domains", "Notes"]:
                th = ET.SubElement(tr, "th")
                th.text = header

            tbody = ET.SubElement(table, "tbody")
            for enc_date, screen in sorted(developmental_screens, key=lambda x: x[0], reverse=True):
                tr = ET.SubElement(tbody, "tr")
                td = ET.SubElement(tr, "td")
                td.text = enc_date.strftime("%Y-%m-%d")
                td = ET.SubElement(tr, "td")
                td.text = screen.tool
                td = ET.SubElement(tr, "td")
                td.text = screen.result.title()
                td = ET.SubElement(tr, "td")
                td.text = ", ".join(screen.domains_assessed) if screen.domains_assessed else "All domains"
                td = ET.SubElement(tr, "td")
                notes = []
                if screen.concerns:
                    notes.append(f"Concerns: {', '.join(screen.concerns)}")
                if screen.notes:
                    notes.append(screen.notes)
                td.text = "; ".join(notes) if notes else ""

            # Add structured entries for developmental screens
            for idx, (enc_date, screen) in enumerate(developmental_screens):
                entry = ET.SubElement(section, "entry")
                entry.set("typeCode", "DRIV")

                obs = ET.SubElement(entry, "observation")
                obs.set("classCode", "OBS")
                obs.set("moodCode", "EVN")

                obs_template = ET.SubElement(obs, "templateId")
                obs_template.set("root", "2.16.840.1.113883.10.20.22.4.67")
                obs_template.set("extension", "2014-06-09")

                obs_id = ET.SubElement(obs, "id")
                obs_id.set("root", generate_uuid())

                obs_code = ET.SubElement(obs, "code")
                obs_code.set("code", "77618-2")
                obs_code.set("codeSystem", "2.16.840.1.113883.6.1")
                obs_code.set("displayName", "Developmental screening status")

                obs_status = ET.SubElement(obs, "statusCode")
                obs_status.set("code", "completed")

                obs_eff = ET.SubElement(obs, "effectiveTime")
                obs_eff.set("value", format_date(enc_date.date() if hasattr(enc_date, 'date') else enc_date))

                value = ET.SubElement(obs, "value")
                value.set(f"{{{self.NS_XSI}}}type", "CD")
                result_codes = {
                    "normal": ("17621005", "Normal"),
                    "at-risk": ("281694009", "At risk"),
                    "delayed": ("248290002", "Developmental delay"),
                    "not-completed": ("385660001", "Not done"),
                }
                if screen.result in result_codes:
                    value.set("code", result_codes[screen.result][0])
                    value.set("displayName", result_codes[screen.result][1])
                    value.set("codeSystem", "2.16.840.1.113883.6.96")
                else:
                    self._set_uncoded(value, screen.result)

                # Add screening tool as method
                method = ET.SubElement(obs, "methodCode")
                self._set_uncoded(method, screen.tool)

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

        # Show message if no data at all
        if not has_data:
            para = ET.SubElement(text, "paragraph")
            para.text = "No developmental milestones or screenings recorded"


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
