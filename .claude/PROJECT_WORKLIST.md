# Oread Learning Platform - Roadmap

**Last Updated:** December 26, 2025
**Full Plan:** `docs/LEARNING_PLATFORM_PLAN.md`

## Current Status: Phase 1 Complete, Phase 2 In Progress

### Recently Completed (December 26, 2025)
- [x] **Enhanced Messiness System** - Complete rewrite with:
  - 5 error categories (safety-critical, workflow, data-integrity, coding/billing, medicolegal)
  - Timeline-aware error distribution (early/middle/recent encounters)
  - 4 "boss level" threading errors for level 5 charts
  - Difficulty-based level descriptions (0-5)
- [x] Fixed time travel slider (only shows for active chronic conditions)
- [x] Panel generation now uses LLM by default (richer patient records)
- [x] Added natural language configuration modal for panel generation
- [x] Fixed database credentials (.env file + dotenv loading)
- [x] Fixed variable name mismatch (SUPABASE_KEY vs SUPABASE_ANON_KEY)
- [x] Dark mode header text fixes

---

## Learning Platform Roadmap (15 Features)

### Phase 1: Foundation - **COMPLETE**
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 11 | Database + Auth (Supabase) | **Complete** | **Critical** |
| 5 | Mass Generate (Panels) | **Complete** | High |
| 6 | Single Case Generation | **Complete** | High |

### Phase 2: Clinical Depth (In Progress)
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 3 | Time Travel (Disease Arcs) | **Complete** | **Critical** |
| - | Enhanced Messiness System | **Complete** | High |
| 9 | First-Class Vaccine Engine | Not started | High |
| 8 | Growth/Development Variations | Partial (screening exists) | High |
| 4 | Results Expansion (Labs/Imaging) | Partial (labs exist) | High |

### Phase 3: Learning Experience
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

### Chart Messiness (NEW)
- 6 difficulty levels (0=Pristine → 5=Chart From Hell)
- 43 specific error types across 5 categories
- Timeline-aware error placement
- Threading errors that span multiple visits

### Exports
- JSON, FHIR R4, C-CDA 2.1, Markdown
- Proper SNOMED/RxNorm/LOINC codes

---

## Messiness Level Reference

| Level | Name | Description |
|-------|------|-------------|
| 0 | Pristine | Teaching ideal - learn what "right" looks like |
| 1 | Real World | Minor inconsistencies, abbreviations |
| 2 | Busy Clinic | Copy-forward artifacts, stale data |
| 3 | Needs Reconciliation | Conflicts requiring clinical judgment |
| 4 | Safety Landmines | Hidden dangers among the noise |
| 5 | Chart From Hell | Threading errors, near-misses, medicolegal nightmares |

---

## Known Issues / Tech Debt
- [ ] Server requires manual restart after code changes
- [x] Environment variables need .env file (fixed)
- [ ] Some UI polish needed for dark mode

---

## Deferred Features
- #2 Image Generation (Gemini) - Skipped per decision
- Parent Communication Practice
- Handoff/IPASS Practice
- Voice Presentation
- Mobile App (use PWA)
- LMS Integration

---

## Key Decisions
- **Audience**: All learners (NP students → senior residents)
- **Images**: Skip for now, focus on text
- **Echo**: Full attending simulation (not MVP)
- **Validation**: Self-review + user flagging
- **Messiness**: Difficulty-based, not count-based

---

## Environment Setup
- API key: ANTHROPIC_API_KEY in ~/.zshrc
- Supabase: .env file in project root (auto-loaded)
- Current LLM: Claude Haiku 3.5
- Fonts: Crimson Pro + Work Sans + JetBrains Mono
- Icons: Lucide via CDN

---

## Next Up (Suggested)
1. **Vaccine Engine** - Catch-up schedules, hesitancy scenarios
2. **Echo (AI Attending)** - Socratic learning assistant
3. **Documentation Practice** - Note writing with AI feedback
