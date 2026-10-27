"""
Peds RxNorm codes are pinned to RxNav-verified concepts.

Several legacy codes pointed at unrelated drugs (methylphenidate ER coded as
metoprolol, amoxicillin-clavulanate as apomorphine, levetiracetam as a
remapped fosinopril code, hydrocortisone cream as ferrous sulfate). This test
fails when a drug/code pair is added or changed without being verified.

To add a drug: look it up on RxNav (https://rxnav.nlm.nih.gov/REST/rxcui/<id>/properties.json),
confirm the name, and add it to VERIFIED. Offline; no network in tests.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

# rxcui -> RxNav concept name (IN / MIN / SCD), verified 2026-10-27.
VERIFIED: dict[str, str] = {
    "161": "acetaminophen",
    "435": "albuterol",
    "723": "amoxicillin",
    "19711": "amoxicillin / clavulanate",
    "1191": "aspirin",
    "18631": "azithromycin",
    "19831": "budesonide",
    "389132": "budesonide / formoterol",
    "2231": "cephalexin",
    "20610": "cetirizine",
    "3264": "dexamethasone",
    "3498": "diphenhydramine",
    "3992": "epinephrine",
    "4053": "erythromycin",
    "310385": "fluoxetine 20 MG Oral Capsule",
    "41126": "fluticasone",
    "4832": "glucagon",
    "5492": "hydrocortisone",
    "274783": "insulin glargine",
    "86009": "insulin lispro",
    "114477": "levetiracetam",
    "6809": "metformin",
    "6901": "methylphenidate",
    "88249": "montelukast",
    "7454": "nitrofurantoin",
    "7623": "ofloxacin",
    "26225": "ondansetron",
    "7984": "penicillin V",
    "219314": "polymyxin B / trimethoprim",
    "312938": "sertraline 100 MG Oral Tablet",
    "42316": "tacrolimus",
    "10759": "triamcinolone",
    "10831": "sulfamethoxazole / trimethoprim",
}

# First word(s) of the drug name that must appear in the verified concept.
_NAME_KEYS = {
    "insulin (multiple daily injections or pump)": "insulin",
    "trimethoprim-sulfamethoxazole": "sulfamethoxazole",
    "polymyxin b-trimethoprim ophthalmic": "polymyxin",
    "amoxicillin-clavulanate": "clavulanate",
    "budesonide-formoterol hfa": "formoterol",
    "glucagon emergency kit": "glucagon",
    "epinephrine auto-injector": "epinephrine",
}


def _pairs() -> set[tuple[str, str]]:
    pairs = set()
    yaml_text = (ROOT / "knowledge/conditions/conditions.yaml").read_text()
    for m in re.finditer(r'agent:\s*"([^"]+)"\s*\n\s*rxnorm:\s*"(\d+)"', yaml_text):
        pairs.add((m.group(1), m.group(2)))
    dosing = (ROOT / "src/engines/peds_dosing.py").read_text()
    for m in re.finditer(r'Rx\(f?"([^"]+)",\s*"(\d+)"', dosing):
        pairs.add((m.group(1), m.group(2)))
    return pairs


def test_every_peds_rxnorm_code_is_verified():
    unknown = sorted(p for p in _pairs() if p[1] not in VERIFIED)
    assert not unknown, f"unverified RxNorm codes (verify on RxNav, add to VERIFIED): {unknown}"


def test_rxnorm_codes_match_drug_names():
    mismatched = []
    for name, code in _pairs():
        key = name.lower().split(" {")[0]
        needle = _NAME_KEYS.get(key, key.split()[0].split("-")[0])
        if needle not in VERIFIED.get(code, "").lower():
            mismatched.append((name, code, VERIFIED.get(code)))
    assert not mismatched, f"drug name does not match its RxNorm concept: {mismatched}"
