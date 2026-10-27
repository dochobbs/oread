"""
Peds patient-generation stress test.

Randomized sweep over age, condition sets (incl. below-gate requests),
messiness, complexity tier and seed. Each patient gets: the repo's clinical
invariants, extra plausibility checks below, all exports, round-trip, timeline,
and single-encounter generation. Prints a fault table grouped by signature.

Run from the repo root:  uv run python scripts/stress_peds.py [N] [seed]

Faults are grouped by signature, split into clean (messiness 0) vs messy
charts; messiness >= 1 injects some errors on purpose. The exact seed behind
each fault is written to output/stress/fault_seeds.json for replay:
GenerationSeed(**seed) with PedsEngine(use_llm=False, messiness_level=...).
"""

from __future__ import annotations

import os
import random
import re
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import date

os.environ.setdefault("OREAD_DISABLE_LLM", "1")
os.environ.setdefault("OREAD_DISABLE_ATHENA", "1")
ROOT = os.getcwd()
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import io, contextlib  # noqa: E401

import yaml

import invariants as inv
from src.engines import PedsEngine
from src.exporters import export_json, export_markdown
from src.exporters.ccda import export_to_ccda
from src.exporters.fhir import export_to_fhir
from src.models import ComplexityTier, GenerationSeed, Patient

N = int(sys.argv[1]) if len(sys.argv) > 1 else 600
MASTER = int(sys.argv[2]) if len(sys.argv) > 2 else 20261027
rnd = random.Random(MASTER)
loop_rnd = random.Random(MASTER + 1)  # in-loop sampling, so seeds replay

COND_KEYS = [k for k in yaml.safe_load(open("knowledge/conditions/conditions.yaml"))
             if not k.startswith("_")]
TIERS = list(ComplexityTier)

# --------------------------------------------------------------------------- #
# Extra checks (not in tests/invariants.py)                                   #
# --------------------------------------------------------------------------- #

# Normal-ish vital limits by age (months): (hr_lo, hr_hi, rr_lo, rr_hi). Wide on
# purpose: flag only clearly impossible values. Source: PALS / Fleming 2011.
VITAL_LIMITS = [(0, 70, 220, 15, 80), (12, 60, 200, 12, 60), (36, 50, 180, 10, 50),
                (72, 45, 160, 8, 40), (144, 40, 150, 8, 35)]


def _age_mo(p: Patient, d) -> float:
    from datetime import datetime as _dt
    d = d.date() if isinstance(d, _dt) else d
    return (d - p.demographics.date_of_birth).days / 30.4375


def x_vitals(p):
    out = []
    for e in p.encounters:
        v = e.vital_signs
        if not v:
            continue
        a = _age_mo(p, e.date)
        lim = [x for x in VITAL_LIMITS if a >= x[0]][-1]
        if v.heart_rate is not None and not lim[1] <= v.heart_rate <= lim[2]:
            out.append(f"HR {v.heart_rate} at {a:.0f}mo")
        if v.respiratory_rate is not None and not lim[3] <= v.respiratory_rate <= lim[4]:
            out.append(f"RR {v.respiratory_rate} at {a:.0f}mo")
        if v.temperature_f is not None and not 95 <= v.temperature_f <= 106:
            out.append(f"temp {v.temperature_f}")
        if v.oxygen_saturation is not None and not 80 <= v.oxygen_saturation <= 100:
            out.append(f"SpO2 {v.oxygen_saturation}")
        if v.weight_kg is not None and v.weight_kg <= 0.3:
            out.append(f"weight {v.weight_kg}")
        # AAP: BP at every well visit from age 3
        if e.type.value == "well-child" and a >= 37 and v.blood_pressure_systolic is None:
            out.append(f"no BP at well visit {a:.0f}mo")
    return out[:5]


def x_growth(p):
    out = []
    g = sorted([m for m in p.growth_data], key=lambda m: m.date)
    for a, b in zip(g, g[1:]):
        if a.height_cm and b.height_cm and b.height_cm < a.height_cm - 1.0:
            out.append(f"height fell {a.height_cm}->{b.height_cm} ({a.date}->{b.date})")
        if a.head_circumference_cm and b.head_circumference_cm and \
                b.head_circumference_cm < a.head_circumference_cm - 0.7:
            out.append(f"HC fell {a.head_circumference_cm}->{b.head_circumference_cm}")
        days = (b.date - a.date).days
        if a.weight_kg and b.weight_kg and 0 < days < 120 and b.weight_kg < a.weight_kg * 0.85:
            out.append(f"weight -{(1 - b.weight_kg / a.weight_kg) * 100:.0f}% in {days}d")
    return out[:4]


def x_obesity_bmi(p):
    if not any("obesity" in c.display_name.lower() and c.clinical_status.value == "active"
               for c in p.problem_list):
        return []
    g = [m for m in p.growth_data if m.bmi_percentile is not None]
    if not g:
        return ["obesity with no BMI percentile"]
    last = max(g, key=lambda m: m.date)
    return [] if last.bmi_percentile >= 95 else [f"obesity but latest BMI {last.bmi_percentile:.0f}th"]


def x_conditions(p):
    out, seen = [], Counter()
    gates = PedsEngine(use_llm=False)._hardcoded_age_gates
    for c in p.problem_list:
        if c.abatement_date and c.abatement_date < c.onset_date:
            out.append(f"'{c.display_name}' abates before onset")
        if c.clinical_status.value == "active":
            seen[c.display_name.lower()] += 1
        key = c.display_name.lower().replace(" ", "_")
        for gk, (lo, hi) in gates.items():
            if gk == key or gk.replace("_", " ") in c.display_name.lower():
                onset = _age_mo(p, c.onset_date)
                if onset < lo - 0.5 or (hi is not None and onset > hi + 0.5):
                    out.append(f"'{c.display_name}' onset {onset:.1f}mo outside gate {lo}-{hi}")
    out += [f"'{n}' active x{k}" for n, k in seen.items() if k > 1]
    return out


def x_meds(p):
    out = []
    for m in inv._all_meds(p):
        if m.end_date and m.end_date < m.start_date:
            out.append(f"{m.display_name} ends before start")
        if m.dose_unit in ("mg", "mcg", "mL", "units"):
            try:
                if float(m.dose_quantity) <= 0:
                    out.append(f"{m.display_name} dose {m.dose_quantity}")
            except (TypeError, ValueError):
                # Bolus insulin is ordered as a carb ratio; that's correct
                if "per" not in str(m.dose_quantity) or m.dose_unit != "units":
                    out.append(f"{m.display_name} non-numeric {m.dose_unit} dose '{m.dose_quantity}'")
        if m.status.value == "active" and m.end_date and m.end_date < date.today():
            out.append(f"{m.display_name} active but ended {m.end_date}")
    return out[:5]


def x_allergy_conflict(p):
    """Prescribed a drug the patient is allergic to."""
    allergens = [a.display_name.lower() for a in p.allergy_list
                 if a.clinical_status == "active" and a.category.value == "medication"]
    out = []
    for m in inv._all_meds(p):
        n = m.display_name.lower()
        for al in allergens:
            if al.split()[0] in n or ("penicillin" in al and "amoxicillin" in n) or \
                    ("sulfa" in al and "sulfamethoxazole" in n) or \
                    ("cephalosporin" in al and n.startswith("ceph")):
                known = [a for a in p.allergy_list if a.display_name.lower() == al]
                # Only a fault if the allergy was on record when the drug started
                if any((a.recorded_date or a.onset_date) and
                       (a.recorded_date or a.onset_date) < m.start_date for a in known):
                    out.append(f"{m.display_name} ({m.start_date}) despite {al} allergy")
    return sorted(set(out))[:4]


def x_food_allergy(p):
    if any("food allerg" in c.display_name.lower() for c in p.problem_list):
        if not any(a.category.value == "food" for a in p.allergy_list):
            return ["food allergy problem, no food allergen in allergy_list"]
    return []


def x_text(p):
    out = []
    for e in p.encounters:
        if re.search(r"\b[a-z]+_[a-z_]+\b", e.chief_complaint or ""):
            out.append(f"snake_case chief complaint '{e.chief_complaint}'")
            break
    for c in p.problem_list:
        if "_" in c.display_name:
            out.append(f"snake_case problem '{c.display_name}'")
            break
    return out


# AAP Bright Futures periodicity (months) for kids we observe from birth.
PERIODICITY = [0, 1, 2, 4, 6, 9, 12, 15, 18, 24, 30, 36, 48, 60]


def x_wellchild_schedule(p):
    age = p.demographics.age_months
    well = [_age_mo(p, e.date) for e in p.encounters if e.type.value in ("well-child", "newborn")]
    missing = [m for m in PERIODICITY if m + 1 <= age and not any(abs(w - m) <= 1.5 for w in well)]
    return [f"no well visit near {missing}"] if len(missing) > 2 else []


def x_dev_screens(p):
    """AAP: ASQ/general screen at 9, 18, 30 mo; M-CHAT at 18 and 24 mo."""
    age = p.demographics.age_months
    screens = [(_age_mo(p, sc.date), sc.tool) for e in p.encounters
               for sc in (e.developmental_screens or ([e.developmental_screen] if e.developmental_screen else []))]
    out = []
    for target in (9, 18, 30):
        if age >= target + 2 and not any(abs(a - target) <= 2 for a, _ in screens):
            out.append(f"no dev screen near {target}mo")
    for target in (18, 24):
        if age >= target + 2 and not any(abs(a - target) <= 2 and "chat" in t.lower()
                                          for a, t in screens):
            out.append(f"no M-CHAT near {target}mo")
    return out


def x_encounters(p):
    out = []
    days = Counter((e.date.date(), e.type.value) for e in p.encounters)
    out += [f"{n} {t} visits on {d}" for (d, t), n in days.items() if n > 1][:2]
    for e in p.encounters:
        if e.immunizations_given and e.type.value not in ("well-child", "newborn", "nurse-visit",
                                                          "immunization-only"):
            out.append(f"vaccines at {e.type.value} visit")
            break
    return out


EXTRA = [x_vitals, x_growth, x_obesity_bmi, x_conditions, x_meds, x_allergy_conflict,
         x_food_allergy, x_text, x_wellchild_schedule, x_dev_screens, x_encounters]

CORE = [inv.check_dates, inv.check_vaccine_series, inv.check_vaccine_ages,
        inv.check_vaccine_dose_numbers, inv.check_annual_flu, inv.check_duplicate_therapy,
        inv.check_chronic_doses, inv.check_med_min_age, inv.check_required_meds,
        inv.check_sliding_scale_basal, inv.check_self_limited_resolve,
        inv.check_no_placeholder_codes, inv.check_diabetes_a1c, inv.check_encounter_vitals,
        inv.check_encounter_hpi, inv.check_exam_age_appropriate]


# --------------------------------------------------------------------------- #
# Sweep                                                                       #
# --------------------------------------------------------------------------- #

def make_seed(i: int) -> tuple[GenerationSeed, int, str]:
    mode = rnd.random()
    age = rnd.choice([rnd.randint(0, 24), rnd.randint(0, 252), rnd.randint(0, 252),
                      rnd.choice([0, 1, 2, 6, 12, 24, 36, 48, 60, 72, 120, 216, 251])])
    conds = None
    if mode < 0.35:
        conds = [rnd.choice(COND_KEYS)]
    elif mode < 0.6:
        conds = rnd.sample(COND_KEYS, rnd.randint(2, 4))
    mess = rnd.choice([0, 0, 0, 1, 2, 3, 4, 5])
    tier = rnd.choice(TIERS) if rnd.random() < 0.2 and not conds else None
    seed = GenerationSeed(age_months=age, conditions=conds, messiness_level=mess,
                          complexity_tier=tier, random_seed=MASTER + i)
    label = f"#{i} {age}mo m{mess} {tier.value if tier else ''} {conds or ''}"
    return seed, mess, label


SEEDS: dict[str, dict] = {}
faults: dict[str, list[str]] = defaultdict(list)
crashes: dict[str, list[str]] = defaultdict(list)
times = []
engine = PedsEngine(use_llm=False)


def sig(msg: str) -> str:
    return re.sub(r"\d+(\.\d+)?", "#", msg)[:110]


t_start = time.time()
for i in range(N):
    seed, mess, label = make_seed(i)
    SEEDS[label] = seed.model_dump(mode='json')
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            p = engine.generate(seed)
    except Exception as exc:  # noqa: BLE001
        tb = traceback.extract_tb(exc.__traceback__)[-1]
        crashes[f"generate: {type(exc).__name__}: {sig(str(exc))} @ {os.path.basename(tb.filename)}:{tb.lineno}"].append(label)
        continue
    times.append((time.time() - t0, label))

    for chk in CORE + EXTRA:
        try:
            for v in chk(p):
                faults[f"{chk.__name__}: {sig(v)}"].append(f"{label} :: {v}")
        except Exception as exc:  # noqa: BLE001
            crashes[f"check {chk.__name__}: {type(exc).__name__}: {sig(str(exc))}"].append(label)

    # Exports + structural checks
    try:
        fhir = export_to_fhir(p)
        xml = export_to_ccda(p)
        md = export_markdown(p)
        export_json(p)
        for chk, args in ((inv.check_fhir_valid, (fhir,)), (inv.check_ccda_structure, (xml, p)),
                          (inv.check_format_parity, (p, fhir, xml, md))):
            for v in chk(*args):
                faults[f"{chk.__name__}: {sig(v)}"].append(f"{label} :: {v}")
    except Exception as exc:  # noqa: BLE001
        tb = traceback.extract_tb(exc.__traceback__)[-1]
        crashes[f"export: {type(exc).__name__}: {sig(str(exc))} @ {os.path.basename(tb.filename)}:{tb.lineno}"].append(label)

    # Round trip
    try:
        Patient.from_stored(p.model_dump(mode="json"))
    except Exception as exc:  # noqa: BLE001
        crashes[f"roundtrip: {type(exc).__name__}: {sig(str(exc))[:80]}"].append(label)

    # Timeline + single-encounter generation (every 5th patient: slower paths)
    if i % 5 == 0:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                engine.generate_timeline(patient=p)
                if p.demographics.age_months > 0:
                    engine.get_snapshot_at_age(patient=p, age_months=loop_rnd.randint(0, p.demographics.age_months))
        except Exception as exc:  # noqa: BLE001
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            crashes[f"timeline: {type(exc).__name__}: {sig(str(exc))} @ {os.path.basename(tb.filename)}:{tb.lineno}"].append(label)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                engine.generate_encounter_for_patient(patient=p, difficulty_level=loop_rnd.randint(1, 5))
        except Exception as exc:  # noqa: BLE001
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            crashes[f"single-encounter: {type(exc).__name__}: {sig(str(exc))} @ {os.path.basename(tb.filename)}:{tb.lineno}"].append(label)

# --------------------------------------------------------------------------- #
# Report                                                                      #
# --------------------------------------------------------------------------- #
gen_ok = len(times)
print(f"\n=== {N} requested, {gen_ok} generated, {time.time() - t_start:.0f}s total ===")
if times:
    ts = sorted(t for t, _ in times)
    print(f"gen time p50 {ts[len(ts) // 2]:.2f}s  p95 {ts[int(len(ts) * .95)]:.2f}s  max {ts[-1]:.2f}s ({max(times)[1]})")

print(f"\n=== CRASHES ({sum(len(v) for v in crashes.values())}) ===")
for k, v in sorted(crashes.items(), key=lambda kv: -len(kv[1])):
    print(f"[{len(v):>4}] {k}\n        e.g. {v[0]}")

print(f"\n=== FAULTS by signature ({sum(len(v) for v in faults.values())}) ===")
by_check = defaultdict(int)
for k, v in faults.items():
    by_check[k.split(":")[0]] += len(v)
for c, n in sorted(by_check.items(), key=lambda kv: -kv[1]):
    print(f"  {c:<32} {n}")
print()
for k, v in sorted(faults.items(), key=lambda kv: -len(kv[1]))[:60]:
    clean = [x for x in v if " m0 " in x]
    print(f"[{len(v):>4} | m0 {len(clean):>4}] {k}\n        e.g. {(clean or v)[0][:220]}")

import json as _json
dump = {}
for k, v in list(crashes.items()) + list(faults.items()):
    ex = [x for x in v if " m0 " in x] or v
    dump[k] = [{"label": x, "seed": SEEDS.get(x.split(" :: ")[0])} for x in ex[:3]]
os.makedirs(os.path.join(ROOT, "output", "stress"), exist_ok=True)
_json.dump(dump, open(os.path.join(ROOT, "output", "stress", "fault_seeds.json"), "w"), indent=1, default=str)
print("\nfault seeds: output/stress/fault_seeds.json")
