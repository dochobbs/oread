# Oread Learning Platform - Roadmap

**Last Updated:** January 21, 2026
**Full Plan:** `docs/LEARNING_PLATFORM_PLAN.md`

## Current Status: Adult Engine Enhanced

### Recently Completed (January 21, 2026)
- [x] **Adult Engine Condition-Aware Generation**
  - Vitals impact for 17+ conditions
  - Probabilistic symptom selection
  - Condition-specific physical exam findings
  - LOINC-coded lab generation
- [x] **Adult Immunizations**
  - 8 vaccines (flu, Tdap, Td, shingles, pneumococcal, COVID-19, Hep B)
  - CVX codes and realistic uptake rates
  - Age-appropriate scheduling
- [x] **Adult Disease Arcs**
  - 6 longitudinal arcs for Time Travel
  - Metabolic, cardiovascular, CKD, COPD, depression, chronic pain
  - Clinical pearls and references

### Previously Completed (December 2025)
- [x] Database + Auth (Supabase)
- [x] Mass Generate (Panels)
- [x] Single Case Generation
- [x] Time Travel (Disease Arcs) - Pediatric
- [x] Enhanced Messiness System
- [x] First-Class Vaccine Engine - Pediatric
- [x] Growth/Development Variations
- [x] Results Expansion (Labs/Imaging)

---

## Learning Platform Roadmap (15 Features)

### Phase 1: Foundation - **COMPLETE**
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 11 | Database + Auth (Supabase) | **Complete** | **Critical** |
| 5 | Mass Generate (Panels) | **Complete** | High |
| 6 | Single Case Generation | **Complete** | High |

### Phase 2: Clinical Depth - **COMPLETE**
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 3 | Time Travel (Disease Arcs) | **Complete** (Peds + Adult) | **Critical** |
| - | Enhanced Messiness System | **Complete** | High |
| 9 | First-Class Vaccine Engine | **Complete** (Peds + Adult) | High |
| 8 | Growth/Development Variations | **Complete** | High |
| 4 | Results Expansion (Labs/Imaging) | **Complete** | High |
| - | Adult Engine Parity | **Complete** | High |

### Phase 3: Learning Experience (Next)
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 7 | Echo (AI Attending) | Not started | **Critical** |
| 13 | Documentation Practice | Not started | **Critical** |
| 14 | Billing/Coding Practice | Not started | High |
| 10 | Edge Case Creation | Not started | High |

### Phase 4: Retention & Polish
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 12 | Competency Mapping (ACGME) | Not started | High |
| 15 | Spaced Repetition | Not started | High |
| 1 | Artifacts (Forms, Orders) | Not started | Medium |
| - | Analytics Dashboard | Not started | Medium |

---

## What's Working Now

### Both Engines (Pediatric + Adult)
- Condition-aware generation (vitals, symptoms, PE, labs)
- Disease arcs for Time Travel
- Immunizations with CVX codes
- FHIR R4, C-CDA 2.1, JSON, Markdown exports
- Messiness levels 0-5

### Pediatric Engine
- AAP vaccine schedule from YAML
- Catch-up vaccine logic
- Vaccine hesitancy scenarios
- Growth pattern variations (FTT, obesity, preterm)
- Age-specific pediatric lab ranges

### Adult Engine (NEW)
- 8 adult vaccines with realistic uptake
- 6 disease arcs (metabolic, CV, CKD, COPD, depression, pain)
- 17+ condition defaults for vitals impact
- Timeline generation with auto-detection

### Authentication & Panels
- Supabase auth (signup, login, JWT tokens)
- Panel creation, viewing, deletion
- Panel patient generation with natural language config

### Chart Messiness
- 6 difficulty levels (0=Pristine → 5=Chart From Hell)
- 43 specific error types across 5 categories
- Timeline-aware error placement

---

## Immediate Next Steps

1. **Integrate Adult Engine with Web UI** - Timeline feature for adults
2. **Add More Adult Disease Arcs** - Cancer progressions, autoimmune
3. **Test Messiness with Adult Engine** - Verify levels 1-5 work
4. **Start Echo (AI Attending)** - Phase 3 priority

---

## Known Issues / Tech Debt
- [ ] Web UI timeline needs adult engine integration
- [ ] Server requires manual restart after code changes
- [ ] Some UI polish needed for dark mode
- [ ] Duplicate files need cleanup

---

## Key Decisions
- **Audience**: All learners (NP students → senior residents)
- **Images**: Skip for now, focus on text
- **Echo**: Full attending simulation (not MVP)
- **Validation**: Self-review + user flagging
- **Messiness**: Difficulty-based, not count-based
- **Labs**: Both binary and numeric supported
- **Adult/Peds**: Unified CLI with --engine flag
