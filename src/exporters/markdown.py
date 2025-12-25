"""
Markdown exporter for SynthPatient.

Exports patient data as human-readable Markdown documentation.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.models import Patient, Encounter, EncounterType


def export_markdown(
    patient: Patient,
    output_path: Path | None = None,
    include_full_notes: bool = True,
) -> str:
    """
    Export a patient to Markdown format.
    
    Args:
        patient: The patient to export
        output_path: Optional path to write the Markdown file
        include_full_notes: Whether to include full narrative notes
    
    Returns:
        Markdown string representation of the patient
    """
    lines = []
    d = patient.demographics
    
    # Header
    lines.append(f"# Patient Record: {d.full_name}")
    lines.append("")
    lines.append(f"**Generated:** {patient.generated_at.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Complexity:** {patient.complexity_tier.value.replace('-', ' ').title()}")
    lines.append(f"**Patient ID:** {patient.id}")
    lines.append("")
    
    # Demographics
    lines.append("## Demographics")
    lines.append("")
    lines.append(f"- **Name:** {d.full_name}")
    lines.append(f"- **Date of Birth:** {d.date_of_birth.strftime('%B %d, %Y')}")
    lines.append(f"- **Age:** {_format_age(d.age_years, d.age_months)}")
    lines.append(f"- **Sex at Birth:** {d.sex_at_birth.value.title()}")
    if d.gender_identity and d.gender_identity != d.sex_at_birth.value:
        lines.append(f"- **Gender Identity:** {d.gender_identity}")
    lines.append(f"- **Race:** {', '.join(d.race)}")
    lines.append(f"- **Ethnicity:** {d.ethnicity or 'Not specified'}")
    lines.append(f"- **Language:** {d.preferred_language}")
    lines.append("")
    
    # Contact Info
    lines.append("### Contact Information")
    lines.append("")
    lines.append(f"- **Phone:** {d.phone}")
    if d.email:
        lines.append(f"- **Email:** {d.email}")
    lines.append(f"- **Address:**")
    lines.append(f"  {d.address.line1}")
    if d.address.line2:
        lines.append(f"  {d.address.line2}")
    lines.append(f"  {d.address.city}, {d.address.state} {d.address.postal_code}")
    lines.append("")
    
    # Emergency Contact
    lines.append("### Emergency Contact")
    lines.append("")
    ec = d.emergency_contact
    lines.append(f"- **Name:** {ec.name}")
    lines.append(f"- **Relationship:** {ec.relationship}")
    if ec.phone:
        lines.append(f"- **Phone:** {ec.phone}")
    lines.append("")
    
    # Social History
    if patient.social_history:
        lines.append("## Social History")
        lines.append("")
        sh = patient.social_history
        lines.append(f"- **Living Situation:** {sh.living_situation}")
        if sh.household_members:
            lines.append(f"- **Household Members:** {len(sh.household_members)}")
            for member in sh.household_members:
                age_str = f", age {member.age}" if member.age else ""
                lines.append(f"  - {member.relationship}{age_str}")
        if sh.school_name:
            lines.append(f"- **School:** {sh.school_name}")
        if sh.grade_level:
            lines.append(f"- **Grade:** {sh.grade_level}")
        if sh.school_performance:
            lines.append(f"- **School Performance:** {sh.school_performance}")
        if sh.employment_status:
            lines.append(f"- **Employment:** {sh.employment_status}")
        if sh.occupation:
            lines.append(f"- **Occupation:** {sh.occupation}")
        lines.append(f"- **Food Security:** {sh.food_security.title()}")
        lines.append(f"- **Housing:** {sh.housing_stability.title()}")
        if sh.firearms_in_home is not None:
            lines.append(f"- **Firearms in Home:** {'Yes' if sh.firearms_in_home else 'No'}")
        lines.append("")
    
    # Problem List
    lines.append("## Problem List")
    lines.append("")
    if patient.problem_list:
        for condition in patient.problem_list:
            status = condition.clinical_status.value.title()
            severity = f" ({condition.severity.value})" if condition.severity else ""
            lines.append(f"- **{condition.display_name}**{severity} - {status}")
            # Display code with appropriate system name
            if condition.code:
                code_system = "SNOMED" if "snomed" in condition.code.system.lower() else "ICD-10"
                lines.append(f"  - {code_system}: {condition.code.code}")
            lines.append(f"  - Onset: {condition.onset_date.strftime('%Y-%m-%d')}")
            if condition.notes:
                lines.append(f"  - Notes: {condition.notes}")
    else:
        lines.append("*No active problems*")
    lines.append("")
    
    # Medications
    lines.append("## Medications")
    lines.append("")
    if patient.medication_list:
        active_meds = [m for m in patient.medication_list if m.status.value == "active"]
        inactive_meds = [m for m in patient.medication_list if m.status.value != "active"]
        
        if active_meds:
            lines.append("### Active Medications")
            lines.append("")
            for med in active_meds:
                # Include RxNorm code if available
                rxnorm_str = ""
                if med.code and "rxnorm" in med.code.system.lower():
                    rxnorm_str = f" (RxNorm: {med.code.code})"
                lines.append(f"- **{med.display_name}**{rxnorm_str} {med.dose_quantity} {med.dose_unit} {med.frequency}")
                if med.indication:
                    lines.append(f"  - Indication: {med.indication}")
                lines.append(f"  - Started: {med.start_date.strftime('%Y-%m-%d')}")
            lines.append("")
        
        if inactive_meds:
            lines.append("### Past Medications")
            lines.append("")
            for med in inactive_meds:
                lines.append(f"- {med.display_name} ({med.status.value})")
                if med.discontinuation_reason:
                    lines.append(f"  - Reason stopped: {med.discontinuation_reason}")
            lines.append("")
    else:
        lines.append("*No medications*")
        lines.append("")
    
    # Allergies
    lines.append("## Allergies")
    lines.append("")
    if patient.allergy_list:
        for allergy in patient.allergy_list:
            reactions = ", ".join(r.manifestation for r in allergy.reactions) if allergy.reactions else "Unknown reaction"
            lines.append(f"- **{allergy.display_name}** ({allergy.category.value})")
            lines.append(f"  - Reactions: {reactions}")
            lines.append(f"  - Criticality: {allergy.criticality}")
    else:
        lines.append("*No known allergies (NKDA)*")
    lines.append("")
    
    # Immunizations
    lines.append("## Immunization Record")
    lines.append("")
    if patient.immunization_record:
        # Group by vaccine
        by_vaccine: dict[str, list] = {}
        for imm in patient.immunization_record:
            name = imm.display_name
            if name not in by_vaccine:
                by_vaccine[name] = []
            by_vaccine[name].append(imm)
        
        for vaccine, doses in sorted(by_vaccine.items()):
            dose_dates = [f"#{d.dose_number or i+1}: {d.date.strftime('%Y-%m-%d')}" for i, d in enumerate(sorted(doses, key=lambda x: x.date))]
            lines.append(f"- **{vaccine}**: {', '.join(dose_dates)}")
    else:
        lines.append("*No immunization records*")
    lines.append("")
    
    # Growth Data (for pediatric patients)
    if patient.growth_data:
        lines.append("## Growth History")
        lines.append("")
        lines.append("| Date | Age | Weight (kg) | Height (cm) | HC (cm) | BMI |")
        lines.append("|------|-----|-------------|-------------|---------|-----|")
        for growth in sorted(patient.growth_data, key=lambda x: x.date)[-10:]:  # Last 10
            age_str = _format_age_from_days(growth.age_in_days)
            weight = f"{growth.weight_kg:.1f}" if growth.weight_kg else "-"
            height = f"{growth.height_cm:.1f}" if growth.height_cm else "-"
            hc = f"{growth.head_circumference_cm:.1f}" if growth.head_circumference_cm else "-"
            bmi = f"{growth.bmi:.1f}" if growth.bmi else "-"
            lines.append(f"| {growth.date.strftime('%Y-%m-%d')} | {age_str} | {weight} | {height} | {hc} | {bmi} |")
        lines.append("")
    
    # Family History
    if patient.family_history:
        lines.append("## Family History")
        lines.append("")
        for entry in patient.family_history:
            lines.append(f"- **{entry.relationship.title()}**: {entry.condition}")
            if entry.onset_age:
                lines.append(f"  - Onset age: {entry.onset_age}")
            if entry.deceased:
                death_info = f" (deceased at age {entry.death_age})" if entry.death_age else " (deceased)"
                lines.append(f"  - {death_info}")
        lines.append("")
    
    # Encounters
    lines.append("## Encounter History")
    lines.append("")
    lines.append(f"*Total encounters: {len(patient.encounters)}*")
    lines.append("")
    
    # Sort encounters by date (most recent first for summary, chronological for full)
    sorted_encounters = sorted(patient.encounters, key=lambda x: x.date, reverse=True)
    
    for enc in sorted_encounters:
        lines.append(f"### {enc.date.strftime('%Y-%m-%d')} - {_format_encounter_type(enc.type)}")
        lines.append("")
        lines.append(f"**Chief Complaint:** {enc.chief_complaint}")
        lines.append("")
        
        # Provider and location
        lines.append(f"**Provider:** {enc.provider.name}, {enc.provider.credentials or ''}")
        lines.append(f"**Location:** {enc.location.name}")
        lines.append("")
        
        # Vitals
        if enc.vital_signs:
            vs = enc.vital_signs
            vitals_parts = []
            if vs.temperature_f:
                vitals_parts.append(f"Temp {vs.temperature_f}°F")
            if vs.heart_rate:
                vitals_parts.append(f"HR {vs.heart_rate}")
            if vs.respiratory_rate:
                vitals_parts.append(f"RR {vs.respiratory_rate}")
            if vs.blood_pressure_systolic:
                vitals_parts.append(f"BP {vs.blood_pressure_systolic}/{vs.blood_pressure_diastolic}")
            if vs.oxygen_saturation:
                vitals_parts.append(f"SpO2 {vs.oxygen_saturation}%")
            if vs.weight_kg:
                vitals_parts.append(f"Wt {vs.weight_kg} kg")
            if vs.height_cm:
                vitals_parts.append(f"Ht {vs.height_cm} cm")
            
            if vitals_parts:
                lines.append(f"**Vitals:** {' | '.join(vitals_parts)}")
                lines.append("")
        
        # Growth percentiles
        if enc.growth_percentiles:
            gp = enc.growth_percentiles
            pct_parts = []
            if gp.weight_percentile:
                pct_parts.append(f"Weight {gp.weight_percentile}%ile")
            if gp.height_percentile:
                pct_parts.append(f"Height {gp.height_percentile}%ile")
            if gp.hc_percentile:
                pct_parts.append(f"HC {gp.hc_percentile}%ile")
            if gp.bmi_percentile:
                pct_parts.append(f"BMI {gp.bmi_percentile}%ile")
            if pct_parts:
                lines.append(f"**Growth:** {' | '.join(pct_parts)}")
                lines.append("")
        
        # Assessment
        if enc.assessment:
            lines.append("**Assessment:**")
            for i, a in enumerate(enc.assessment, 1):
                lines.append(f"{i}. {a.diagnosis}")
            lines.append("")
        
        # Plan
        if enc.plan:
            lines.append("**Plan:**")
            for p in enc.plan:
                detail = f": {p.details}" if p.details else ""
                lines.append(f"- {p.description}{detail}")
            lines.append("")
        
        # Immunizations
        if enc.immunizations_given:
            imm_names = [i.display_name for i in enc.immunizations_given]
            lines.append(f"**Immunizations Given:** {', '.join(imm_names)}")
            lines.append("")
        
        # Full narrative note
        if include_full_notes and enc.narrative_note:
            lines.append("<details>")
            lines.append("<summary>Full Narrative Note</summary>")
            lines.append("")
            lines.append("```")
            lines.append(enc.narrative_note)
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    # Care Team
    if patient.care_team:
        lines.append("## Care Team")
        lines.append("")
        for member in patient.care_team:
            role = f" ({member.role})" if member.role else ""
            pcp = " ⭐ PCP" if member.is_pcp else ""
            lines.append(f"- **{member.name}**{role}{pcp}")
            if member.specialty:
                lines.append(f"  - Specialty: {member.specialty}")
            if member.organization:
                lines.append(f"  - Organization: {member.organization}")
            if member.phone:
                lines.append(f"  - Phone: {member.phone}")
        lines.append("")
    
    # Insurance
    if patient.insurance:
        lines.append("## Insurance")
        lines.append("")
        for coverage in patient.insurance:
            lines.append(f"- **{coverage.payer}** ({coverage.type})")
            lines.append(f"  - Member ID: {coverage.member_id}")
            if coverage.plan_name:
                lines.append(f"  - Plan: {coverage.plan_name}")
            if coverage.group_number:
                lines.append(f"  - Group: {coverage.group_number}")
        lines.append("")
    
    # Footer
    lines.append("---")
    lines.append("")
    lines.append(f"*This is a synthetic patient record generated by Oread v{patient.engine_version}*")
    lines.append(f"*Generation seed: {patient.id}*")
    
    # Join and write
    markdown = "\n".join(lines)
    
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown)
    
    return markdown


def _format_age(years: int, months: int) -> str:
    """Format age as human-readable string."""
    if years == 0:
        return f"{months} months"
    elif years < 2:
        remaining_months = months - (years * 12)
        if remaining_months > 0:
            return f"{years} year, {remaining_months} months"
        return f"{years} year"
    else:
        return f"{years} years"


def _format_age_from_days(days: int) -> str:
    """Format age from days."""
    months = days // 30
    years = months // 12
    if years == 0:
        return f"{months}mo"
    elif years < 2:
        remaining = months % 12
        return f"{years}y {remaining}mo"
    else:
        return f"{years}y"


def _format_encounter_type(enc_type: EncounterType) -> str:
    """Format encounter type for display."""
    type_names = {
        EncounterType.NEWBORN: "Newborn Visit",
        EncounterType.WELL_CHILD: "Well-Child Visit",
        EncounterType.ANNUAL_PHYSICAL: "Annual Physical",
        EncounterType.ACUTE_ILLNESS: "Acute Illness",
        EncounterType.ACUTE_INJURY: "Acute Injury",
        EncounterType.CHRONIC_FOLLOWUP: "Chronic Care Follow-up",
        EncounterType.MEDICATION_CHECK: "Medication Check",
        EncounterType.MENTAL_HEALTH: "Mental Health Visit",
        EncounterType.URGENT_CARE: "Urgent Care",
        EncounterType.ED_VISIT: "Emergency Department",
        EncounterType.HOSPITAL_ADMISSION: "Hospital Admission",
        EncounterType.TELEHEALTH: "Telehealth Visit",
        EncounterType.SPECIALIST_CONSULT: "Specialist Consultation",
    }
    return type_names.get(enc_type, enc_type.value.replace("-", " ").title())
