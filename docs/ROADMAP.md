# Oread Learning Platform - Roadmap

**Last Updated:** December 2025
**Full Plan:** `docs/LEARNING_PLATFORM_PLAN.md`

## Current Status: Phase 2 In Progress

### Completed Features
- [x] Parallel LLM generation (7.4x speedup)
- [x] CCDA medications fix
- [x] Description parsing
- [x] Developmental screening
- [x] UI improvements (fonts, icons, wrapping)
- [x] LLM Phases 1-3, 5-6 (narratives, HPI, assessment, guidance)
- [x] **Time Travel** - Disease arc visualization with 6 arcs, timeline API, and interactive UI

---

## Learning Platform Roadmap (15 Features)

### Phase 1: Foundation
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 11 | Database + Auth (Supabase) | Not started | **Critical** |
| 5 | Mass Generate (Panels) | Not started | High |
| 6 | Single Case Generation | Not started | High |

### Phase 2: Clinical Depth
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 3 | Time Travel (Disease Arcs) | **Complete** | **Critical** |
| 9 | First-Class Vaccine Engine | Not started | High |
| 8 | Growth/Development Variations | Not started | High |
| 4 | Results Expansion (Labs/Imaging) | Not started | High |

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

## Feature Summaries

### Phase 1: Foundation

**#11 Database + Auth (Supabase)** - CRITICAL
- User accounts with learner levels (NP student → attending)
- Patient panels for continuity
- Persistent storage (PostgreSQL)
- Row-level security

**#5 Mass Generate (Panels)**
- Generate 15-25 patient panels
- Configurable age/complexity distribution
- 60% healthy, 25% single chronic, 15% complex

**#6 Single Case Generation**
- New encounters for existing patients
- Difficulty levels 1-5 (routine → zebra)
- Adaptive to learner level

### Phase 2: Clinical Depth

**#3 Time Travel (Disease Arcs)** - COMPLETE ✓
- See patients evolve over years
- Atopic march: eczema → food allergy → asthma
- RSV → reactive airway → persistent asthma
- Age slider UI with clinical decision points
- 6 disease arcs implemented (Atopic March, RSV→Asthma, Obesity Cascade, ADHD+Comorbidities, Recurrent AOM, Functional GI)
- Progression rules in conditions.yaml
- Timeline API endpoints
- Interactive web UI with "What Changed" panel

**#9 Vaccine Engine**
- Catch-up schedule calculator
- Alternative/delayed schedules
- Hesitancy scenarios
- Disease risk for unvaccinated

**#8 Growth/Development Variations**
- Normal variation modeling (early/late walkers)
- Screening integration (ASQ, M-CHAT)
- Delay patterns and red flags

**#4 Results Expansion**
- Comprehensive lab panels
- Radiology reports (text)
- Trend data over time

### Phase 3: Learning Experience

**#7 Echo (AI Attending)** - CRITICAL
- Socratic dialogue ("What's on your differential?")
- Direct teaching with clinical pearls
- Literature/guideline references
- Adaptive to learner level

**#13 Documentation Practice** - CRITICAL
- Learner writes HPI, Assessment, Plan
- AI comparison to expert version
- Structured feedback with scoring

**#14 Billing/Coding Practice**
- E&M level estimation (99211-99215)
- ICD-10 coding practice
- MDM complexity teaching

**#10 Edge Case Creation**
- Atypical presentations
- Red flags hidden in routine cases
- "What can't you miss" teaching

### Phase 4: Retention & Polish

**#12 Competency Mapping (ACGME)**
- Link cases to ACGME/AAP milestones
- Track progress per competency
- Identify gaps, suggest cases

**#15 Spaced Repetition**
- SM-2 algorithm for case resurfacing
- "Cases due for review" dashboard
- Reinforce missed diagnoses

**#1 Artifacts (Forms, Orders)**
- School physicals, 504 plans
- PT/OT orders, prior auths
- Condition-triggered generation

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

---

## Technical Notes
- API key: ANTHROPIC_API_KEY in ~/.zshrc
- Current LLM: Claude Haiku 4.5
- Fonts: Crimson Pro + Work Sans + JetBrains Mono
- Icons: Lucide via CDN
- Database: Supabase (PostgreSQL + Auth)
