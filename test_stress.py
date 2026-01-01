#!/usr/bin/env python3
"""
Oread Stress Test Runner
Runs 12 scenarios from the stress test batch and validates results.
"""

import json
import sys
from datetime import date
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.engines.engine import PedsEngine
from src.models.patient import GenerationSeed

# Load batch scenarios
batch_file = Path("output/files/oread_stress_test_batch.json")
scenarios = json.loads(batch_file.read_text())

engine = PedsEngine()
engine.use_llm = False  # Disable LLM for fast testing
results = []

print("=" * 70, flush=True)
print("OREAD STRESS TEST - 12 Scenarios", flush=True)
print("=" * 70, flush=True)

for i, scenario in enumerate(scenarios, 1):
  print(f"\n[{i}/12] {scenario.get('scenario', 'Unknown')}...")

  try:
    seed = GenerationSeed(
      age_months=scenario['age_months'],
      conditions=scenario.get('conditions', []),
      messiness_level=scenario.get('messiness_level', 0),
    )
    patient = engine.generate(seed)

    # Extract key data
    dob = patient.demographics.date_of_birth
    today = date.today()

    # Get active conditions
    active_conditions = [
      c.display_name for c in patient.problem_list
      if c.clinical_status.value == 'active'
    ]

    # Get medications
    active_meds = [
      m.display_name.lower() for m in patient.medication_list
      if m.status.value == 'active'
    ]

    # Check for specific meds
    has_insulin = any('insulin' in m for m in active_meds)
    has_stimulant = any(x in ' '.join(active_meds) for x in ['methylphenidate', 'adderall', 'vyvanse', 'amphetamine'])
    has_anticonvulsant = any(x in ' '.join(active_meds) for x in ['levetiracetam', 'keppra', 'valproic', 'lamotrigine'])
    has_bronchodilator = any('albuterol' in m for m in active_meds)
    has_aspirin = any('aspirin' in m for m in active_meds)

    # Check onset ages for specific conditions
    onset_ages = {}
    for c in patient.problem_list:
      if c.onset_date:
        age_at_onset = (c.onset_date - dob).days / 30.44
        onset_ages[c.display_name.lower()] = round(age_at_onset, 1)

    # Check for date violations
    bad_dates = []
    for enc in patient.encounters:
      enc_date = enc.date.date() if hasattr(enc.date, 'date') else enc.date
      if enc_date < dob:
        bad_dates.append(f"Encounter {enc_date} < DOB")
      if enc_date > today:
        bad_dates.append(f"Encounter {enc_date} > today")

    for c in patient.problem_list:
      if c.onset_date:
        if c.onset_date < dob:
          bad_dates.append(f"{c.display_name} onset {c.onset_date} < DOB")
        if c.onset_date > today:
          bad_dates.append(f"{c.display_name} onset {c.onset_date} > today")

    # Check for R69 codes
    r69_codes = [c.display_name for c in patient.problem_list
                 if c.code and c.code.code == 'R69']

    # Determine pass/fail
    issues = []
    scenario_name = scenario.get('scenario', '')

    # Test-specific validations
    if 'ADHD boundary pass' in scenario_name:
      if 'ADHD' not in ' '.join(active_conditions) and 'adhd' not in ' '.join(active_conditions).lower():
        issues.append("ADHD not present (expected)")
      adhd_onset = onset_ages.get('adhd', onset_ages.get('attention deficit hyperactivity disorder', None))
      if adhd_onset and adhd_onset < 48:
        issues.append(f"ADHD onset {adhd_onset}mo < 48mo")
      if not has_stimulant:
        issues.append("No stimulant medication")

    elif 'ADHD boundary reject' in scenario_name:
      if any('adhd' in c.lower() for c in active_conditions):
        issues.append("ADHD present (should be rejected at 47mo)")

    elif 'Bronchiolitis' in scenario_name:
      if not any('bronchiolitis' in c.lower() for c in active_conditions):
        issues.append("Bronchiolitis not present")
      bronch_onset = onset_ages.get('bronchiolitis', None)
      if bronch_onset and bronch_onset > 24:
        issues.append(f"Bronchiolitis onset {bronch_onset}mo > 24mo")

    elif 'Anxiety boundary' in scenario_name:
      if not any('anxiety' in c.lower() for c in active_conditions):
        issues.append("Anxiety not present")
      anx_onset = onset_ages.get('anxiety', onset_ages.get('generalized anxiety disorder', None))
      if anx_onset and anx_onset < 36:
        issues.append(f"Anxiety onset {anx_onset}mo < 36mo")

    elif 'Triple chronic' in scenario_name:
      if not has_insulin:
        issues.append("Missing insulin for T1D")
      if not has_anticonvulsant:
        issues.append("Missing anticonvulsant for Epilepsy")
      if not has_bronchodilator:
        issues.append("Missing bronchodilator for Asthma")

    elif 'Kawasaki' in scenario_name:
      if not any('kawasaki' in c.lower() for c in active_conditions):
        issues.append("Kawasaki not present")
      if not has_aspirin:
        issues.append("Missing aspirin for Kawasaki")

    elif 'Neurodevelopmental' in scenario_name:
      adhd_onset = onset_ages.get('adhd', onset_ages.get('attention deficit hyperactivity disorder', None))
      if adhd_onset and adhd_onset < 48:
        issues.append(f"ADHD onset {adhd_onset}mo < 48mo")
      if not has_stimulant:
        issues.append("No stimulant medication")

    elif 'Atopic march' in scenario_name:
      asthma_onset = onset_ages.get('asthma', None)
      if asthma_onset and asthma_onset < 12:
        issues.append(f"Asthma onset {asthma_onset}mo < 12mo")
      if not has_bronchodilator:
        issues.append("Missing bronchodilator for Asthma")

    elif 'Teen mental health' in scenario_name:
      adhd_onset = onset_ages.get('adhd', onset_ages.get('attention deficit hyperactivity disorder', None))
      if adhd_onset and adhd_onset < 48:
        issues.append(f"ADHD onset {adhd_onset}mo < 48mo")
      anx_onset = onset_ages.get('anxiety', onset_ages.get('generalized anxiety disorder', None))
      if anx_onset and anx_onset < 36:
        issues.append(f"Anxiety onset {anx_onset}mo < 36mo")

    elif 'Max history' in scenario_name:
      if bad_dates:
        issues.extend(bad_dates[:3])  # Show up to 3

    elif 'Impossible newborn' in scenario_name:
      if any('adhd' in c.lower() for c in active_conditions):
        issues.append("ADHD present (should be rejected at 0mo)")
      if any('asthma' in c.lower() for c in active_conditions):
        issues.append("Asthma present (should be rejected at 0mo)")

    elif 'Metabolic' in scenario_name:
      if not any('prediabetes' in c.lower() for c in active_conditions):
        issues.append("Prediabetes not present")
      if not any('obesity' in c.lower() for c in active_conditions):
        issues.append("Obesity not present")

    # Universal checks
    if r69_codes:
      issues.append(f"R69 codes: {r69_codes}")
    if bad_dates and 'Max history' not in scenario_name:
      issues.append(f"Date violations: {bad_dates}")

    # Report
    status = "✅ PASS" if not issues else "❌ FAIL"
    print(f"  {status}")
    print(f"  Age: {scenario['age_months']}mo | Conditions: {active_conditions}")
    print(f"  Meds: insulin={has_insulin}, stim={has_stimulant}, bronch={has_bronchodilator}, aspirin={has_aspirin}")
    if onset_ages:
      print(f"  Onset ages: {onset_ages}")
    if issues:
      for iss in issues:
        print(f"  ⚠️  {iss}")

    results.append({
      'scenario': scenario.get('scenario'),
      'passed': len(issues) == 0,
      'issues': issues,
      'conditions': active_conditions,
      'onset_ages': onset_ages,
    })

  except Exception as e:
    print(f"  ❌ ERROR: {e}")
    results.append({
      'scenario': scenario.get('scenario'),
      'passed': False,
      'issues': [str(e)],
    })

# Summary
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
passed = sum(1 for r in results if r['passed'])
print(f"Passed: {passed}/12")
print(f"Failed: {12 - passed}/12")

if passed < 12:
  print("\nFailed scenarios:")
  for r in results:
    if not r['passed']:
      print(f"  - {r['scenario']}: {r['issues']}")

# Save results
output_file = Path("output/stress_test_results.json")
output_file.write_text(json.dumps(results, indent=2, default=str))
print(f"\nResults saved to: {output_file}")
