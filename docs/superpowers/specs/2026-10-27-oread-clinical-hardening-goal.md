# Oread goal: Clinical hardening

**Status:** open
**Captured:** 2026-10-27
**Estimated effort:** ~2–3 weeks, ~30–40 commits
**Source:** 4-lane code review 2026-10-27 (server, peds engine, exporters, adult engine + integrations). Findings summarized per lane below; line numbers were accurate on `0298009` and should be re-verified before touching code.

## Goal

**Every patient Oread generates is clinically defensible, exports cleanly, and the server can't be abused.** A pediatrician reading any generated chart should find nothing that would be wrong in a real chart: correct vaccine series, weight-appropriate doses, no duplicate therapy, conditions that resolve when they should, and the same facts in JSON, FHIR, C-CDA and Markdown. Proven by an automated invariant suite, not by eyeballing.

Today the code runs and 39 tests pass, but the tests don't look at patient content. The review found the immunization engine skips dose-3 series, every otitis visit gets amoxicillin + Augmentin, adults lose all vitals silently, C-CDA marks refused vaccines as given, and `/api/generate` is open to the internet with CORS `*`.

This is a correctness goal. No new features.

---

## Repo + setup

- **Working dir:** `~/consult/MedEd/synpat` (symlink `~/consult/MedEd/oread`). GitHub `dochobbs/oread`. On `main`. Untracked: this spec and `uv.lock`.
- **Branch:** `feature/clinical-hardening` off `main`.
- **Stack:** Python 3.13 (uv venv), FastAPI, Pydantic v2, Supabase, `fhir.resources` 8.x.
- **Tests:** `uv run --extra dev pytest -q` (39 passing at start). Lint: `uv run --extra dev ruff check .`
- **CLI:** `uv run python cli.py generate --age 4 --conditions adhd --no-llm -o <dir>`
- **Server:** port 9104 (`python server.py`). Check the port first: `lsof -iTCP:9104 -sTCP:LISTEN`. Don't leave it running.
- **Consumers:** Metis portal (`~/consult/MedEd/metis`) proxies `/api/oread/*` and calls `/api/generate`, `GET /api/patients/{id}`, `/context`. Mneme, Syrinx and Echo receive Oread patients through Metis. Don't break those contracts without updating Metis.

## Read first

- `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/CONDITION_SCHEMA.md`
- `oread_qa_findings.md` (Dec 2025 findings; issues 1 and 4 are fixed, 2, 3 and 5 are partly fixed)
- `src/engines/engine.py` (PedsEngine; the immunization path starts around `_generate_immunizations`)
- `adult/adult_engine.py`, `src/models/patient.py`
- `src/exporters/ccda.py`, `src/exporters/fhir.py`
- `knowledge/immunizations/aap_schedule.yaml`, `knowledge/conditions/conditions.yaml`

---

## Decisions

- **D1: Immunization schedule — RESOLVED: AAP** (2026 AAP schedule, not CDC). Cite the AAP version in the YAML header.
- **D2: Elation demo work — RESOLVED: removed.** The untracked Elation export/import work (`demo/import_to_elation.py`, `backfill_notes.py`, `export_demo_patients.py`, `demo/fixtures/`, `demo/notes/`, the 17 new `demo/ccda_exports/*.xml`, `scripts/generate_demo_fixtures.py`) was moved out of the repo to `~/claude-work/oread-elation-demo-archive-2026-10-27/` on 2026-10-27. No Elation-specific work is in scope. Lane X targets standards conformance (C-CDA 2.1 / FHIR R4 / US Core), not one EHR's importer.
- **D5: Oread runs independently — RESOLVED: required.** Oread must run fully standalone: no Metis, no Athena, no Supabase, no Anthropic/Exa key. CLI, server and every export must work. Verified 2026-10-27 that it does today (server boots, `/api/generate` → 200 with all of those absent). Platform services are optional enrichment. See lane S "Standalone mode" and the definition of done.
- **D4: Where condition content gets fixed — follows from D5.** Oread's local `knowledge/conditions/conditions.yaml` is authoritative for Oread. Fix it there first. Mirror clinically meaningful fixes into Athena (`~/consult/MedEd/athena/knowledge/conditions/`) as a follow-up so the platform stays consistent. An Athena hit must never override a local definition with worse content (see lane I).
- **D3: Patient persistence.** `patients_store` is in-memory and loses everything on restart, while Metis holds the IDs. Options are a TTL-bounded store (in scope) or persisting to Supabase (bigger). **Default: bounded in-memory + owner tagging now; Supabase persistence is a follow-up goal.**

---

## Lanes

### T — Clinical invariant suite (foundation; build first)

A test module that generates a seeded cohort and asserts content. Every later lane adds invariants here **before** fixing the bug (red → green).

- Cohort: about 120 seeded peds patients (ages 0–21y, ±conditions, messiness 0) and about 40 adults (19–90y), `--no-llm`. Mark the full cohort `slow`; a 15-patient smoke subset runs by default.
- Starting invariants (each maps to a review finding):
  - No date before DOB or after today. Patient age matches the requested age (adult leap-year bug).
  - Requested condition at exactly its minimum gate age generates without raising.
  - Vaccine series: by age 8y, HepB ×3, DTaP ×5, IPV ×4 etc. unless a hesitancy/exemption flag is set. No dose given before the minimum age or after the maximum age (rotavirus ≤ 8m0d). Minimum intervals respected.
  - No two drugs from the same class active at once (SSRI, statin, aminopenicillin).
  - Chronic doses fall within the mg/kg/day range for the patient's weight at prescribing.
  - Every chronic condition with a standard med has a med (T1D: basal + rapid + glucagon; T2D: metformin; food allergy: epinephrine auto-injector).
  - Diabetes patients have A1c results.
  - Acute conditions (bronchiolitis, AOM, URI, Kawasaki) have abatement dates.
  - Adult encounters have vital signs and an HPI.
  - No RxNorm `000000`, and no ICD-10 R69 for a condition that exists in the YAML.
  - Cross-format parity: problem, med and immunization counts and statuses match across JSON, FHIR, C-CDA and Markdown.
  - FHIR bundle validates under `fhir.resources.R4B` with 0 errors. C-CDA is well-formed and passes the structural checks in lane X.

### S — Server security + robustness (`server.py`, `src/db/`, `src/auth/`)

- **Standalone mode (D5):** one config switch, `OREAD_MODE=standalone|platform`.
  - Default is `standalone` when Supabase isn't configured.
  - Standalone: bind 127.0.0.1, no auth required, CORS limited to localhost origins, in-memory store, panels/auth endpoints return a clear 501 "requires platform mode" instead of crashing.
  - Platform: everything below.
  - Never fall into "no auth" because a Supabase env var is missing while bound to a non-local interface. Refuse to start instead.
- **Platform mode:** require auth on `/api/generate`, `/api/generate/quick` and the `/api/patients*` routes. Metis already sends a bearer token; confirm with the Metis proxy before flipping.
- CORS (platform): replace `*` + credentials with an explicit origin list from config.
- Supabase: stop calling `sign_in` on the shared singleton client. Use a per-request client or `postgrest.auth(token)`. Logout must not affect other users.
- Sync work out of the event loop: convert generation handlers to `def` (threadpool) or `run_in_threadpool`. Cap concurrent generations. Remove the unbounded daemon thread in `/quick`.
- Input validation via Pydantic: `age_months` bounds, panel `count` bound, min ≤ max age, `sex` and `complexity_tier` enums. Bad input returns 4xx, not 500.
- `/timeline/at/{age}` reads `m.name`/`m.dosage`, which don't exist. Use `display_name` and dose fields.
- Bounded `patients_store` and `generation_jobs` (TTL/LRU), tagged with owner user ID; list, read and delete only your own (D3).
- Stop returning raw exception strings to clients. Log them server-side.
- Remove the dead `python-jose` dependency and the unused `SUPABASE_JWT_SECRET` path, or use them. Rename the duplicate `get_encounter`.

### P — Peds clinical correctness (`src/engines/engine.py`, `src/validators/`, `knowledge/conditions/`)

- **Immunizations (highest priority):**
  - Pass prior doses into `_generate_immunizations`.
  - Replace nearest-age matching with "due as of this visit" logic against the schedule: dose number, minimum age, minimum interval, maximum age.
  - Use real age (days), not `days // 30`.
  - Catch-up follows the CDC/AAP catch-up table and respects maximum ages.
  - Annual influenza each season from 6 months on.
- **Gate boundary crash:** make the onset clamp and the validator use the same age arithmetic, and add one test per gate.
- **Duplicate therapy:** dedupe meds by drug class, not indication text. AOM gets amoxicillin first-line; Augmentin only for treatment failure or recent amoxicillin. Anxiety + depression gets one SSRI.
- **Weight-based dosing for chronic meds:** levetiracetam, cetirizine (age-banded), stimulants (start low, titrate), plus any other med in `chronic_meds`. Dose from the patient's weight on the prescribing date, rounded to available strengths, capped at the adult maximum.
- **Disease-specific medication fixes:**
  - T1D: basal + rapid-acting (carb ratio / correction) + glucagon.
  - T2D: metformin. Fix the `_fix_medication_issue` read of `treatment.medications` vs the YAML's `treatment.items`.
  - Food allergy: epinephrine auto-injector, dose by weight (0.1/0.15/0.3 mg).
  - Ondansetron: add `age_min_months: 6`.
- **Validator:** add fix handlers for lab gaps (A1c for diabetes, etc.). Decide which ERRORs block output; at minimum an R69 for a known condition and a missing standard med should block or auto-fix.
- **Resolution:** acute conditions get abatement dates. Kawasaki aspirin stops at 6–8 weeks if there are no coronary findings.
- **Fall-back coding:** remove the R69 fallback for anything resolvable from the YAML or Athena, and map unknowns to a specific code or fail loudly. R05 → R05.9.
- **Growth:** use exact age in months (fractional) for percentile lookup. Re-check the CDC 2000 weight-for-age male L value against the source table.
- **Seeding:** use a per-engine `random.Random(seed)` instead of the global `random.seed`. Anchor DOB to an injectable "today" so a seed is reproducible across days. Seed the `MessinessInjector`.
- **Found by slice 0:**
  - **Future dates.** Peds abatement dates and adult encounter/med-start dates land after today (`test_no_future_dates_sweep`).
  - **No HPI without the LLM.** The no-LLM path writes no HPI on problem visits. Standalone mode (D5) needs a template HPI.
  - **Seed messiness ignored for hesitancy.** `GenerationSeed.messiness_level` doesn't drive vaccine hesitancy; only the engine constructor value does. CLI/API callers that set only the seed get no refusals.
- **Exam findings:** add a deterministic age filter on exam text (no fontanelles after 24 months) instead of relying on the LLM prompt.

### A — Adult engine (`adult/adult_engine.py`, `src/models/patient.py`)

- **Silent field loss:** set `model_config = ConfigDict(extra="forbid")` on clinical models (or at least `Encounter`, `SocialHistory`, `PhysicalExam`, `Medication`). Fix the kwargs: `vitals` → `vital_signs`, `history_of_present_illness` → `hpi`, `tobacco_use`/`alcohol_use`, `extremities`. Run the peds engine too; `forbid` will surface any peds-side drops.
- **Medications:**
  - Real RxNorm codes from a lookup table; frequency and route per drug.
  - Methotrexate weekly, infliximab IV q8wk, insulin SC, inhalers inhaled.
  - Dose regex handles combination strengths (`49/51 mg`, `250/50 mcg`).
- **Labs:** panels become component results (CMP, lipid, CBC), each with a LOINC code and reference range. Imaging reads aren't scored as numeric labs; "no acute disease" is normal.
- **Condition gating:** validate seeded conditions against sex and age (no PCOS in males). Unknown condition keys raise or map via aliases (`ckd` → `ckd_stage3`). Fix the ASCVD `ckd` check.
- **Plausibility:**
  - DOB uses `relativedelta(years=age)`.
  - No duplicate statins or methotrexate.
  - Follow-up density capped (no 551 chronic visits, no 216 TSH draws).
  - Onset dates spread realistically.
  - USPSTF screenings generate actual procedures and results, not just plan text.
- **Routing:** a description-only request with an adult age cue goes to AdultEngine. `use_llm` is honored or documented as unsupported for adults.

### X — Exporters (`src/exporters/ccda.py`, `fhir.py`, `markdown.py`)

Order these by clinical impact, then conformance:

- **Clinically wrong or SHALL-level C-CDA fixes:**
  - Immunization `negationInd` + `statusCode` from `imm.status`, with the refusal reason as a code. Markdown shows refused/not-given doses as such. FHIR adds `statusReason`.
  - Med frequency → PIVL mapping: normalize case and cover BID/TID/QID/q4–6h/PRN. PRN meds get `precondition` / `asNeeded`, not a fixed period.
  - Problem `effectiveTime/high` from `abatement_date`.
  - Single lab results wrapped in a Result Organizer.
  - Height UCUM `cm` (or `[in_i]`).
  - Growth data goes into the Vitals section, not a second section with the same templateId. Growth observations get `effectiveTime`.
- **C-CDA conformance:**
  - Add RR, SpO2 and head circumference to vitals.
  - Add the US Realm header templateId and the `2015-08-01` extensions on section templates.
  - OID/UUID `id` roots instead of `urn:oread:*`.
  - Allergen codeSystem as an OID.
  - Numeric `doseQuantity` only; free text goes in the sig.
  - Encounter code: a CPT value or nullFlavor.
  - `languageCode="en"`, race/ethnicity CDC codes.
  - Family history ages as `pq` values, not timestamps.
  - Strip control characters before the minidom reparse.
  - Remove the 20-row encounter narrative cap.
- **FHIR:**
  - Timezone-aware datetimes and an `instant` timestamp on the Bundle.
  - No `null`/`""` values (omit, or use a dataAbsentReason extension).
  - Condition `category`; BP as the 85354-9 panel; one weight observation per measurement.
  - `meta.profile` for US Core where the shape conforms.
  - Tests import `fhir.resources.R4B`.

### C — Clinical content currency (`knowledge/`)

- **Immunization schedule:** update to the 2026 AAP schedule (D1).
  - RSV: nirsevimab/clesrovimab, plus the maternal-vaccine branch.
  - PCV20 (CVX 216) / PCV15 (215) instead of 152.
  - Trivalent flu CVX codes.
  - COVID per the chosen schedule.
  - MenABCWY; drop Menactra.
  - HPV start at 9 allowed.
  - Cite the source and version in the YAML header.
- **Growth:** WHO for 0–24 months (CDC recommendation since 2010), CDC 2000 for 2–20y, CDC extended BMI-for-age (2022) above the 95th percentile. Remove the 3–97 clamp so severe obesity can be generated.
- **Asthma:** step therapy with SMART (ICS-formoterol) where age-appropriate, and a controller for persistent asthma.
- **ADHD:** behavioral therapy first for age 4–5, then methylphenidate at a low starting dose.
- Re-check any other `conditions.yaml` treatment against current AAP guidance as you go, citing sources in the YAML.

### I — Integrations (`src/llm/`, `src/knowledge/`, context export)

- **LLM client:**
  - Validate before caching. Never cache an invalid response.
  - Cache key includes temperature and max_tokens. Check `stop_reason` before using the text.
  - Model id `claude-haiku-5-5`, configurable via env.
- **Condition service:**
  - Athena is the source of truth. An LLM-only definition is cached with a short TTL and `needs_verification`, and never overrides a later Athena hit.
  - Something reads `needs_verification`, or it gets removed.
- **Context export:** `context_export.py` output validates against the shared `PatientContext` schema from Metis `shared/models`. Import the generated model in Oread so `sync.py --validate` checks the real thing.
- **Metis contract:**
  - Fix the EXPORT link 404 (`/apps/oread/patients/{id}`), either by adding the route in Oread or by changing the Metis link.
  - Make the Oread UI's API calls relative so they work under `/apps/oread/`.
  - Note the missing Oread Dockerfile for `metis/deploy/Caddyfile`. Building one is out of scope unless trivial.

### Cleanup (last)

- `ruff check --fix` safe fixes. Manually fix the remaining F-class issues (F841, F811, B023, B904). Don't chase style rules.
- Move `test_full_features.py` and `test_stress.py` into `tests/` with assertions, or delete them.
- Update `CLAUDE.md` and `docs/ARCHITECTURE.md` to match reality (conditions are ICD-10-only, not SNOMED; port 9104).

---

## Slice order (re-plan if you find better)

0. **T scaffold.** Invariant harness + cohort fixture + the first 6 invariants. Expected: most fail. Commit the failing tests marked `xfail(strict=True)` with the finding ID, so fixing one flips it.
1. **S security.** Auth, CORS, Supabase per-request client, threadpool, input validation, timeline crash. Small and urgent, because the endpoint is open with LLM spend.
2. **P immunizations.** The biggest clinical bug. Includes annual flu and catch-up max ages.
3. **P medication safety.** Class dedupe (AOM, SSRI), weight-based chronic dosing, T1D/T2D/epi/ondansetron, gate-boundary crash.
4. **A field loss.** `extra="forbid"` + kwarg fixes, then adult meds/labs/gating/DOB.
5. **X clinically wrong / SHALL-level.** Immunization negation, frequency, abatement, Result Organizer, UCUM, duplicate vitals, FHIR timezone. Then the rest of X.
6. **P remainder + C content.** Resolution, R69 removal, growth fractional months and WHO 0–2, seeding, the 2026 AAP vaccine schedule, asthma SMART, ADHD.
7. **I integrations + cleanup.** Mirror condition fixes into Athena (D4). Regenerate the tracked `demo/` samples with the fixed engine.

Each slice: invariants first (red), fix (green), full suite green, ruff F-class clean on touched files. Show Michael the diff before committing.

---

## Definition of done

- The invariant suite passes on the full seeded cohort (about 120 peds + 40 adults), with no `xfail` left except items explicitly deferred with a reason.
- Every FHIR bundle in the cohort validates under `fhir.resources.R4B` with 0 errors.
- Every C-CDA in the cohort is well-formed and passes the structural checks: Result Organizers, no duplicate Vitals section, valid UCUM, PIVL periods match source frequencies, refused vaccines negated, resolved problems have a high date.
- Cross-format parity holds for every cohort patient.
- Unauthenticated `POST /api/generate` → 401. CORS rejects a non-allowlisted origin. Two concurrent logged-in users each see only their own panels. A 50-patient panel request doesn't stall `/api/health`.
- **Standalone (D5):** with `SUPABASE_*`, `ANTHROPIC_API_KEY` and `EXA_API_KEY` unset and Athena unreachable:
  - the full test suite passes
  - `cli.py generate` works for peds and adult ages, in all formats
  - the server boots in standalone mode and generates and exports a patient
  - an automated test covers this (env cleared, Athena URL pointed at a dead port)
  - generated content is identical with Athena up or down for any condition defined locally
- Metis end-to-end still works: generate → Open in Mneme → Send to Syrinx → Echo chat (`metis/scripts/e2e-smoke.sh` or a manual run).
- The 2026 immunization schedule source is cited in the YAML. The growth source is cited per age band.
- `oread_qa_findings.md` is updated with the closing status of every item, or superseded by a short closing note in this spec.
- Ruff F-class errors: 0.

---

## Safety + conventions

- Synthetic data only. Never import real patient data as fixtures. No EHR-specific import work in this goal.
- No new hard dependency on Metis, Athena, Supabase or any API key (D5). Platform integrations stay optional, with timeouts and local fallbacks.
- No push, deploy or production Supabase writes without asking. Use a local/dev Supabase project or mocks in tests.
- No commits without Michael's review of the slice diff. Commit messages follow the repo's `FIX:`/`FEATURE:`/`CHORE:` prefix style.
- Clinical rules carry a source comment (AAP, CDC, NAEPP, Lexicomp-style dose range) next to the YAML entry or constant. No uncited dose numbers.
- Code style per `CLAUDE.md`: 2-space indentation, type hints, Google-style docstrings. Match the surrounding file when it differs.
- Athena has its own test suite (`PYTHONPATH=. pytest athena/tests/` from `~/consult/MedEd`). Run it after any Athena condition edit.
- Keep the PedsEngine public API (`generate`, the `/api/generate` request/response shape) stable for Metis.

## Stop conditions

- A fix requires changing the `/api/generate` or `/context` contract: stop, write the Metis-side change, ask.
- Weight-based dosing needs a drug-dosing source we don't have encoded: list the drugs and ask rather than inventing ranges.
- The invariant suite on the full cohort takes > 5 min: stop and shrink the cohort or parallelize before adding more.
- A finding turns out to be wrong on re-verification: note it here and move on.

## Kickoff

Re-run the suite and confirm the 39-test baseline. Re-verify the line numbers for slice 0 and slice 1 findings. Propose the concrete invariant list and fixture design back to Michael before writing code.
