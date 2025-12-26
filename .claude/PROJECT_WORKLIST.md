# Oread Project Worklist

## Completed
- [x] Parallel LLM generation (7.4x speedup)
- [x] CCDA medications fix (include completed meds)
- [x] Description parsing implementation
- [x] Developmental screening for well-child visits
- [x] UI text wrapping fix for narrative notes
- [x] Description field reset after generation
- [x] Font update (Crimson Pro + Work Sans + JetBrains Mono)
- [x] Lucide icons integration

## In Progress
- [ ] LLM Integration Plan (see `.claude/plans/temporal-petting-quill.md`)
  - Phase 1: Narrative notes - DONE (parallel generation)
  - Phase 2: --describe flag - DONE (parsing implemented)
  - Phase 3: Assessment reasoning - NOT STARTED
  - Phase 4: Discharge instructions - NOT STARTED
  - Phase 5: Family narratives - NOT STARTED
  - Phase 6: Anticipatory guidance - NOT STARTED

## Backlog
- [ ] Assessment with clinical reasoning (LLM-enhanced)
- [ ] Personalized discharge instructions
- [ ] Family narrative generation
- [ ] Age-specific anticipatory guidance
- [ ] CLI --no-llm flag for template-only mode
- [ ] Performance optimization (caching for repeated LLM calls)

## Notes
- API key: ANTHROPIC_API_KEY in ~/.zshrc
- Current default model: Claude Haiku 4.5
- Fonts: Editorial style (Crimson Pro + Work Sans + JetBrains Mono)
- Icons: Lucide via CDN
