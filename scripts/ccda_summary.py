#!/usr/bin/env python3
"""
Generate a Pre-Visit Executive Summary from a C-CDA file.

Usage:
  python ccda_summary.py patient.xml
  python ccda_summary.py patient.xml --visit 5  # Specific visit index
  python ccda_summary.py patient.xml --list     # List all visits

Requires ANTHROPIC_API_KEY environment variable.
"""

import argparse
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import anthropic


# C-CDA namespace
NS = {"cda": "urn:hl7-org:v3"}

# The prompt template
PROMPT_TEMPLATE = """**System Instruction:**
You are an elite Clinical Decision Support AI. Analyze the patient record to generate a high-yield "Pre-Visit Executive Summary."

**Input:** Patient Record, Target Date, Visit Reason.

**The "Hybrid" Approach:**
1.  **Structure:** Follow the strict output schema below (Alerts -> Context -> Disease/Growth -> Meds -> Plan).
2.  **The "Attending" Layer:** Within that structure, apply high-level synthesis:
    * *Context:* Don't just list dates. Identify the "Interval Narrative" (e.g., "Stable year" vs. "Frequent acute utilization").
    * *Disease Control:* For Well Checks, you MUST prioritize **Growth Velocity**, **Development**, and **Clinical Inertia** (e.g., treating abnormal results as normal because they are chronic).
    * *Safety:* actively look for contraindications between history and potential new treatments.

**Output Schema:**

# PRE-VISIT EXECUTIVE SUMMARY
**Patient:** [Name] | **Age:** [Age] | **Date:** [Date] | **Chief Complaint:** [Reason]

### 1. CRITICAL ALERTS
* **Allergies:** [List + Reaction]
* **Major Active Conditions:** [List Chronic Problems]

### 2. VISIT CONTEXT & HISTORY
* **Interval History:** [Synthesize the time since the last physical. Note frequency of acute visits or ER trips.]
* **The "Hidden" Pattern:** [Identify any subtle deterioration or lack of follow-up masked by "stable" notes].
* **Last Relevant Vitals:** [Compare Today vs. Last Year for Well Checks]

### 3. DISEASE CONTROL & MONITORING (Dynamic)
* **[Condition A/Growth]:** [Status] | **Trend:** [Velocity or Direction]
* **[Condition B]:** [Status] | **Last Metric:** [Value/Date]
* **[Screening/Dev]:** [Milestones or Surveillance status]

### 4. MEDICATION RECONCILIATION
* **Active Inventory:** [List]
* **Adherence:** [Note refill gaps or recent changes]

### 5. CLINICAL DECISION SUPPORT
* **Care Gaps:** [Vaccines/Labs Due]
* **Attending Suggestion:** [One high-impact clinical action or question to ask the patient]"""


def parse_ccda(xml_path: Path) -> dict:
  """Parse C-CDA file and extract key information."""
  tree = ET.parse(xml_path)
  root = tree.getroot()

  # Patient info
  patient_name = ""
  patient_dob = ""

  record_target = root.find(".//cda:recordTarget/cda:patientRole/cda:patient", NS)
  if record_target is not None:
    given = record_target.find("cda:name/cda:given", NS)
    family = record_target.find("cda:name/cda:family", NS)
    if given is not None and family is not None:
      patient_name = f"{given.text} {family.text}"

    birth = record_target.find("cda:birthTime", NS)
    if birth is not None:
      patient_dob = birth.get("value", "")

  # Extract encounters - find section with encounters code 46240-8
  encounters = []

  # Find all sections and look for encounters section
  for section in root.findall(".//cda:section", NS):
    code_elem = section.find("cda:code", NS)
    if code_elem is not None and code_elem.get("code") == "46240-8":
      # Found encounters section - get table rows from text
      rows = section.findall(".//cda:tbody/cda:tr", NS)
      for row in rows:
        cells = row.findall("cda:td", NS)
        if len(cells) >= 3:
          date = cells[0].text or ""
          visit_type = cells[1].text or ""
          chief_complaint = cells[2].text or ""
          provider = cells[3].text if len(cells) > 3 else ""

          encounters.append({
            "date": date.replace("-", ""),  # Convert to YYYYMMDD
            "type": visit_type,
            "text": chief_complaint,
            "provider": provider,
          })
      break

  # Extract key sections for context (not full XML - too large)
  sections_content = []

  # Section codes we want: Problems, Medications, Allergies, Immunizations, Encounters, Vitals, Results
  section_codes = ["11450-4", "10160-0", "48765-2", "11369-6", "46240-8", "8716-3", "30954-2"]

  for section in root.findall(".//cda:section", NS):
    code_elem = section.find("cda:code", NS)
    if code_elem is not None and code_elem.get("code") in section_codes:
      title_elem = section.find("cda:title", NS)
      title = title_elem.text if title_elem is not None else "Section"
      text_elem = section.find("cda:text", NS)
      if text_elem is not None:
        text_content = ET.tostring(text_elem, encoding="unicode", method="text")
        # Limit each section to ~5000 chars
        if len(text_content) > 5000:
          text_content = text_content[:5000] + "\n... [truncated]"
        sections_content.append(f"### {title}\n{text_content.strip()}")

  xml_content = "\n\n".join(sections_content)

  return {
    "patient_name": patient_name,
    "patient_dob": patient_dob,
    "encounters": encounters,
    "xml_content": xml_content,
  }


def format_date(date_str: str) -> str:
  """Format YYYYMMDD to readable date."""
  if len(date_str) >= 8:
    try:
      dt = datetime.strptime(date_str[:8], "%Y%m%d")
      return dt.strftime("%Y-%m-%d")
    except ValueError:
      pass
  return date_str


def calculate_age(dob_str: str) -> str:
  """Calculate age from DOB string."""
  if len(dob_str) >= 8:
    try:
      dob = datetime.strptime(dob_str[:8], "%Y%m%d")
      today = datetime.now()
      years = today.year - dob.year
      if today.month < dob.month or (today.month == dob.month and today.day < dob.day):
        years -= 1
      return f"{years} years"
    except ValueError:
      pass
  return "Unknown"


def list_encounters(data: dict) -> None:
  """Print list of encounters."""
  print(f"\nPatient: {data['patient_name']}")
  print(f"DOB: {format_date(data['patient_dob'])} (Age: {calculate_age(data['patient_dob'])})")
  print(f"\nEncounters ({len(data['encounters'])}):\n")

  for i, enc in enumerate(data['encounters']):
    date = format_date(enc.get('date', ''))
    text = enc.get('text', '')[:60]
    print(f"  [{i:2d}] {date:12} {text}")


def generate_summary(data: dict, visit_idx: int) -> str:
  """Generate pre-visit summary using Claude API."""
  api_key = os.environ.get("ANTHROPIC_API_KEY")
  if not api_key:
    print("Error: ANTHROPIC_API_KEY not set", file=sys.stderr)
    sys.exit(1)

  client = anthropic.Anthropic(api_key=api_key)

  # Get the selected encounter
  encounter = data['encounters'][visit_idx]
  visit_date = format_date(encounter.get('date', datetime.now().strftime('%Y%m%d')))
  visit_reason = encounter.get('text', 'Follow-up visit')

  # Build the user prompt
  user_prompt = f"""Here is the patient's medical record summary:

**Patient:** {data['patient_name']}
**DOB:** {format_date(data['patient_dob'])} (Age: {calculate_age(data['patient_dob'])})

{data['xml_content']}

---

**Target Visit Date:** {visit_date}
**Visit Reason:** {visit_reason}

Please generate the Pre-Visit Executive Summary for this visit."""

  print(f"\nGenerating summary for visit on {visit_date}...")
  print(f"Visit reason: {visit_reason}\n")
  print("-" * 60)

  # Call Claude
  response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=2000,
    system=PROMPT_TEMPLATE,
    messages=[{"role": "user", "content": user_prompt}],
  )

  return response.content[0].text


def main():
  parser = argparse.ArgumentParser(
    description="Generate Pre-Visit Executive Summary from C-CDA file"
  )
  parser.add_argument("ccda_file", help="Path to C-CDA XML file")
  parser.add_argument("--visit", "-v", type=int, help="Visit index (default: random)")
  parser.add_argument("--list", "-l", action="store_true", help="List all visits")

  args = parser.parse_args()

  ccda_path = Path(args.ccda_file)
  if not ccda_path.exists():
    print(f"Error: File not found: {ccda_path}", file=sys.stderr)
    sys.exit(1)

  # Parse the C-CDA
  data = parse_ccda(ccda_path)

  if not data['encounters']:
    print("Error: No encounters found in C-CDA file", file=sys.stderr)
    sys.exit(1)

  # List mode
  if args.list:
    list_encounters(data)
    return

  # Select visit
  if args.visit is not None:
    if args.visit < 0 or args.visit >= len(data['encounters']):
      print(f"Error: Visit index must be 0-{len(data['encounters'])-1}", file=sys.stderr)
      sys.exit(1)
    visit_idx = args.visit
  else:
    visit_idx = random.randint(0, len(data['encounters']) - 1)

  # Generate summary
  summary = generate_summary(data, visit_idx)
  print(summary)


if __name__ == "__main__":
  main()
