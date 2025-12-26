# Oread Learning Platform - Roadmap

**Last Updated:** December 2025
**Full Plan:** `docs/LEARNING_PLATFORM_PLAN.md`

## Current Status: Patient Generator Complete

### Completed Features
- [x] Parallel LLM generation (7.4x speedup)
- [x] CCDA medications fix
- [x] Description parsing
- [x] Developmental screening
- [x] UI improvements (fonts, icons, wrapping)
- [x] LLM Phases 1-3, 5-6 (narratives, HPI, assessment, guidance)

---

## Learning Platform Roadmap (15 Features)

### Phase 1: Foundation (Weeks 1-4)
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 11 | Database + Auth (Supabase) | Not started | **Critical** |
| 5 | Mass Generate (Panels) | Not started | High |
| 6 | Single Case Generation | Not started | High |

### Phase 2: Clinical Depth (Weeks 5-10)
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 3 | Time Travel (Disease Arcs) | Not started | **Critical** |
| 9 | First-Class Vaccine Engine | Not started | High |
| 8 | Growth/Development Variations | Not started | High |
| 4 | Results Expansion (Labs/Imaging) | Not started | High |

### Phase 3: Learning Experience (Weeks 11-18)
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 7 | Echo (AI Attending) | Not started | **Critical** |
| 13 | Documentation Practice | Not started | **Critical** |
| 14 | Billing/Coding Practice | Not started | High |
| 10 | Edge Case Creation | Not started | High |

### Phase 4: Retention & Polish (Weeks 19-24)
| # | Feature | Status | Priority |
|---|---------|--------|----------|
| 12 | Competency Mapping (ACGME) | Not started | High |
| 15 | Spaced Repetition | Not started | High |
| 1 | Artifacts (Forms, Orders) | Not started | Medium |
| - | Analytics Dashboard | Not started | Medium |

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

## Implementation Specs Available
See `.claude/plans/nested-growing-island.md` for:
1. Database Schema (Supabase SQL)
2. Echo Attending Simulation
3. Documentation Practice
4. Spaced Repetition (SM-2)
5. Competency Mapping (YAML)
6. Time Travel (Disease Progression)
7. Billing/Coding Practice

---

## Notes
- API key: ANTHROPIC_API_KEY in ~/.zshrc
- Current LLM: Claude Haiku 4.5
- Fonts: Crimson Pro + Work Sans + JetBrains Mono
- Icons: Lucide via CDN
