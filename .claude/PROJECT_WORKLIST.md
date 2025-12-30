# Oread Learning Platform - Roadmap

**Last Updated:** December 29, 2025
**Full Plan:** `docs/LEARNING_PLATFORM_PLAN.md`

## Current Status: Phase 2 Substantially Complete

### Recently Completed (December 29, 2025)
- [x] **Vaccine Engine** - Complete rewrite:
  - AAP schedule loaded from YAML (not hardcoded)
  - Catch-up vaccine logic for missed doses
  - Vaccine hesitancy injection (8 reason types)
  - CVX codes from YAML schedule
- [x] **Growth Pattern Variations** - New patterns:
  - GrowthPattern enum (NORMAL, FTT, OBESITY, PRETERM_CATCHUP, GROWTH_DELAY)
  - Pattern-aware percentile drift in GrowthTrajectory
  - Condition-based pattern selection (celiac→FTT, obesity→obesity pattern, preterm→catch-up)
- [x] **Labs Expansion** - Numeric values:
  - Age-specific pediatric reference ranges
  - Numeric lab generation with units
  - WBC/CRP for pneumonia, urinalysis panel for UTI
- [x] **Imaging Generation** - New capability:
  - `_generate_imaging()` method
  - Chest X-ray, AP Neck X-ray definitions
  - Positive/negative findings with impressions
- [x] **FHIR Export** - Labs and imaging:
  - Lab results as FHIR Observations
  - Imaging results as FHIR DiagnosticReports

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
| 3 | Time Travel (Disease Arcs) | **Complete** | **Critical** |
| - | Enhanced Messiness System | **Complete** | High |
| 9 | First-Class Vaccine Engine | **Complete** | High |
| 8 | Growth/Development Variations | **Complete** | High |
| 4 | Results Expansion (Labs/Imaging) | **Complete** | High |

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

### Authentication & Panels
- Supabase auth (signup, login, JWT tokens)
- User profiles with learner levels
- Panel creation, viewing, deletion
- Panel patient generation with natural language config

### Patient Generation
- LLM-powered narratives (Claude Haiku 3.5)
- Description parsing ("5 year old with asthma")
- Condition-aware generation
- Template + LLM hybrid approach

### Time Travel
- Disease progression visualization
- Age slider for longitudinal view
- "What Changed" panel
- Only shows for patients with chronic conditions

### Vaccines (NEW)
- AAP schedule from YAML
- Catch-up logic for missed vaccines
- Hesitancy scenarios with reasons
- CVX codes throughout

### Growth Patterns (NEW)
- FTT: Weight drifts down, height stable
- Obesity: Weight >> height percentile
- Preterm catch-up: Accelerated early growth
- Growth delay: Plateau period

### Labs & Imaging (NEW)
- Numeric labs with reference ranges
- Age-specific pediatric ranges
- Imaging with findings/impressions
- Integrated into encounters

### Chart Messiness
- 6 difficulty levels (0=Pristine → 5=Chart From Hell)
- 43 specific error types across 5 categories
- Timeline-aware error placement
- Threading errors that span multiple visits

### Exports
- JSON, FHIR R4, C-CDA 2.1, Markdown
- Proper SNOMED/RxNorm/LOINC codes
- Lab Observations and Imaging DiagnosticReports in FHIR

---

## Immediate Next Steps

1. **Test Phase 2 Features** - Run pytest and manual testing
2. **Commit Phase 2 Work** - ~977 lines of new code ready
3. **Clean Up Duplicates** - Remove server (1).py, engine (1).py, etc.
4. **Start Echo (AI Attending)** - Phase 3 priority

---

## Known Issues / Tech Debt
- [ ] Server requires manual restart after code changes
- [ ] Some UI polish needed for dark mode
- [ ] Duplicate files need cleanup (server (1).py, etc.)
- [x] Environment variables need .env file (fixed)

---

## Key Decisions
- **Audience**: All learners (NP students → senior residents)
- **Images**: Skip for now, focus on text
- **Echo**: Full attending simulation (not MVP)
- **Validation**: Self-review + user flagging
- **Messiness**: Difficulty-based, not count-based
- **Labs**: Both binary and numeric supported
- **Growth**: Pattern-aware drift based on conditions

---

## Environment Setup
- API key: ANTHROPIC_API_KEY in ~/.zshrc
- Supabase: .env file in project root (auto-loaded)
- Current LLM: Claude Haiku 3.5
- Fonts: Crimson Pro + Work Sans + JetBrains Mono
- Icons: Lucide via CDN
