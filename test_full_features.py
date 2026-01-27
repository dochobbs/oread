#!/usr/bin/env python3
"""
Comprehensive Oread Feature Test

Generates 10 patients testing all major features:
1. Age ranges (infant to adolescent)
2. Chronic conditions with monitoring labs
3. Acute illness encounters
4. Disease arcs / Time Travel
5. Validation system
6. Narrative reconciliation
7. Exa web search for unknown conditions
8. Messiness levels
9. LLM narratives
10. Complex multi-condition patients

Exports all to a single markdown file for evaluation.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Ensure we can import from src
sys.path.insert(0, str(Path(__file__).parent))

from src.engines import PedsEngine
from src.models import GenerationSeed
from src.exporters.markdown import export_markdown


def generate_test_patients():
  """Generate 10 diverse test patients."""

  # Initialize engine with LLM enabled
  print("Initializing Oread engine with LLM...")
  engine = PedsEngine(use_llm=True, messiness_level=0)

  patients = []
  test_cases = [
    # 1. Newborn with routine well visits
    {
      "name": "Newborn Well Child",
      "seed": GenerationSeed(
        age_months=2,
        conditions=[],
        description="Healthy 2-month-old for routine well child care",
      ),
      "messiness": 0,
      "features": ["Well child visits", "Immunizations", "Growth tracking"],
    },

    # 2. Infant with bronchiolitis (acute respiratory)
    {
      "name": "Infant Bronchiolitis",
      "seed": GenerationSeed(
        age_months=6,
        conditions=["bronchiolitis"],
        description="6-month-old with RSV bronchiolitis, first winter illness",
      ),
      "messiness": 1,
      "features": ["Acute illness", "Respiratory condition", "Low SpO2 vitals"],
    },

    # 3. Toddler with atopic march (disease arc)
    {
      "name": "Atopic March - Eczema to Asthma",
      "seed": GenerationSeed(
        age_months=36,
        conditions=["eczema", "asthma"],
        description="3-year-old with eczema since infancy, now developing asthma - classic atopic march",
      ),
      "messiness": 1,
      "features": ["Disease arc", "Atopic march", "Chronic + acute", "Time travel"],
    },

    # 4. School-age child with Type 1 Diabetes (chronic monitoring)
    {
      "name": "Type 1 Diabetes Management",
      "seed": GenerationSeed(
        age_months=96,  # 8 years
        conditions=["type_1_diabetes"],
        description="8-year-old with Type 1 diabetes diagnosed at age 5, on insulin pump",
      ),
      "messiness": 2,
      "features": ["Chronic condition", "Monitoring labs (HbA1c)", "Specialty care"],
    },

    # 5. Child with ADHD + anxiety (mental health)
    {
      "name": "ADHD with Comorbid Anxiety",
      "seed": GenerationSeed(
        age_months=108,  # 9 years
        conditions=["adhd", "anxiety"],
        description="9-year-old with ADHD on stimulants, developed anxiety requiring additional treatment",
      ),
      "messiness": 2,
      "features": ["Mental health", "Multiple medications", "Behavioral conditions"],
    },

    # 6. Adolescent with obesity cascade (disease progression)
    {
      "name": "Obesity to Prediabetes",
      "seed": GenerationSeed(
        age_months=156,  # 13 years
        conditions=["obesity", "prediabetes"],
        description="13-year-old with progressive obesity now showing prediabetes on labs",
      ),
      "messiness": 1,
      "features": ["Disease progression", "Metabolic syndrome", "Lab abnormalities"],
    },

    # 7. Complex patient - Leukemia (oncology, monitoring labs)
    {
      "name": "ALL Maintenance Phase",
      "seed": GenerationSeed(
        age_months=72,  # 6 years
        conditions=["acute_lymphoblastic_leukemia"],
        description="6-year-old with ALL in maintenance phase, on 6-MP and methotrexate",
      ),
      "messiness": 0,  # Oncology charts are precise
      "features": ["Oncology", "Chemotherapy", "Monitoring labs (CBC, CMP)", "Specialty care"],
    },

    # 8. Recurrent ear infections (surgical history)
    {
      "name": "Recurrent AOM with Tubes",
      "seed": GenerationSeed(
        age_months=24,
        conditions=["recurrent_otitis_media"],
        description="2-year-old with history of 6+ ear infections, now has PE tubes",
      ),
      "messiness": 3,
      "features": ["Recurrent infections", "Surgical procedure", "ENT referral"],
    },

    # 9. Adolescent with multiple acute issues (messier chart)
    {
      "name": "Busy Adolescent Chart",
      "seed": GenerationSeed(
        age_months=180,  # 15 years
        conditions=["pharyngitis", "sports_physical"],
        description="15-year-old athlete with strep throat, also needs sports physical",
      ),
      "messiness": 4,  # Messy chart
      "features": ["Acute + preventive", "Sports clearance", "High messiness"],
    },

    # 10. Uncommon condition (test Exa/knowledge service)
    {
      "name": "Kawasaki Disease",
      "seed": GenerationSeed(
        age_months=30,
        conditions=["kawasaki_disease"],
        description="2.5-year-old with Kawasaki disease, completed IVIG treatment",
      ),
      "messiness": 1,
      "features": ["Uncommon condition", "Knowledge service test", "Cardiac follow-up"],
    },
  ]

  for i, case in enumerate(test_cases, 1):
    print(f"\n{'='*60}")
    print(f"Patient {i}/10: {case['name']}")
    print(f"Features being tested: {', '.join(case['features'])}")
    print(f"{'='*60}")

    # Update messiness level for this patient
    engine.messiness_level = case["messiness"]
    engine.messiness.level = case["messiness"]

    try:
      patient = engine.generate(case["seed"])

      # Get validation result
      validation = engine.validator.validate(patient)

      patients.append({
        "case": case,
        "patient": patient,
        "validation": validation,
      })

      print(f"✓ Generated: {patient.demographics.given_names} {patient.demographics.family_name}")
      print(f"  Encounters: {len(patient.encounters)}")
      print(f"  Conditions: {[c.display_name for c in patient.problem_list]}")
      print(f"  Medications: {[m.display_name for m in patient.medication_list]}")
      print(f"  Validation: {'PASS' if validation.valid else 'ISSUES'} ({len(validation.issues)} issues)")

    except Exception as e:
      print(f"✗ ERROR: {e}")
      import traceback
      traceback.print_exc()

  return patients


def export_all_to_markdown(patients: list, output_path: Path):
  """Export all patients to a single markdown file."""

  lines = [
    "# Oread Feature Test Results",
    f"**Generated:** {date.today()}",
    f"**Patients:** {len(patients)}",
    "",
    "---",
    "",
    "## Summary",
    "",
    "| # | Patient | Age | Conditions | Encounters | Validation |",
    "|---|---------|-----|------------|------------|------------|",
  ]

  for i, p in enumerate(patients, 1):
    patient = p["patient"]
    case = p["case"]
    validation = p["validation"]

    age_months = (date.today() - patient.demographics.date_of_birth).days // 30
    age_str = f"{age_months}mo" if age_months < 24 else f"{age_months // 12}y"
    conditions = ", ".join([c.display_name for c in patient.problem_list[:2]])
    if len(patient.problem_list) > 2:
      conditions += f" (+{len(patient.problem_list) - 2})"

    val_status = "✓" if validation.valid else f"⚠ {len(validation.issues)}"

    lines.append(f"| {i} | {case['name']} | {age_str} | {conditions} | {len(patient.encounters)} | {val_status} |")

  lines.extend([
    "",
    "---",
    "",
  ])

  # Add each patient's full record
  for i, p in enumerate(patients, 1):
    patient = p["patient"]
    case = p["case"]
    validation = p["validation"]

    lines.extend([
      f"## Patient {i}: {case['name']}",
      "",
      f"**Features Tested:** {', '.join(case['features'])}",
      "",
      f"**Messiness Level:** {case['messiness']}/5",
      "",
    ])

    # Validation status
    if validation.valid:
      lines.append("**Validation:** ✓ PASSED")
    else:
      lines.append(f"**Validation:** ⚠ {len(validation.issues)} issues")
      for issue in validation.issues:
        lines.append(f"- [{issue.severity.value}] {issue.message}")

    lines.append("")

    # Use the markdown exporter for the patient record
    patient_md = export_markdown(patient)
    lines.append(patient_md)

    lines.extend([
      "",
      "---",
      "",
    ])

  # Write to file
  output_path.write_text("\n".join(lines))
  print(f"\n✓ Exported all patients to: {output_path}")


def main():
  print("=" * 60)
  print("OREAD COMPREHENSIVE FEATURE TEST")
  print("=" * 60)

  # Generate patients
  patients = generate_test_patients()

  # Export to markdown
  output_path = Path("output/feature_test_results.md")
  output_path.parent.mkdir(exist_ok=True)
  export_all_to_markdown(patients, output_path)

  # Summary
  print("\n" + "=" * 60)
  print("TEST COMPLETE")
  print("=" * 60)
  print(f"Patients generated: {len(patients)}")

  valid_count = sum(1 for p in patients if p["validation"].valid)
  print(f"Validation passed: {valid_count}/{len(patients)}")

  total_encounters = sum(len(p["patient"].encounters) for p in patients)
  print(f"Total encounters: {total_encounters}")

  print(f"\nResults saved to: {output_path.absolute()}")


if __name__ == "__main__":
  main()
