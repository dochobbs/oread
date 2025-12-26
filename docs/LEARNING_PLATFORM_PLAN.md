# Oread Learning Platform: Feature Analysis and Roadmap

## Executive Summary

The 11 proposed features transform Oread from a **synthetic patient generator** into a **pediatric learning platform**. This analysis evaluates each feature, identifies gaps, proposes additions, and recommends phased implementation.

---

## Feature Analysis

### 1. Artifacts (School Forms, Prior Auth, PT/OT Orders)

**Value: HIGH** | **Complexity: MEDIUM**

Pediatric charts are filled with non-clinical documents that learners rarely see:
- School physicals / Sports clearance
- Camp health forms
- Medication administration forms (school nurse)
- PT/OT/Speech therapy orders
- 504 plans / IEP support letters
- Prior authorization requests
- DME orders (nebulizers, wheelchairs)
- FMLA/daycare absence documentation

**Implementation approach:**
- Template library with condition-aware field population
- LLM for personalization (patient name, dates, specific recommendations)
- Export as fillable PDFs or structured data
- Trigger based on condition (asthma → school action plan, ADHD → 504 letter)

**Critique:** Forms vary by state/institution. Recommend generic templates that teach the *concept* rather than jurisdiction-specific forms.

---

### 2. Google Gemini Integration (Images + Forms)

**Value: HIGH** | **Complexity: HIGH**

**Potential uses:**
- Clinical photos: rashes (eczema, impetigo, HSP), conjunctivitis, wounds
- Imaging: chest X-rays (pneumonia, bronchiolitis), fractures
- Growth charts as rendered images
- EKG strips
- Form generation with visual elements

**Technical considerations:**
- Gemini 2.0 Flash for speed, Pro for quality
- Imagen 3 for image generation
- Need separate API client (similar pattern to Claude)
- Storage: base64 in JSON, references in FHIR DiagnosticReport
- Caching critical (image generation is expensive)

**Critique:**
- Medical image generation is RISKY - could produce clinically inaccurate images
- Recommendation: Start with **curated reference images** + AI-generated clinical descriptions
- Use image generation for **low-stakes** visuals first (growth charts, form headers)
- Validate medical images with clinical experts before deployment

**Alternative:** Use open-source medical image databases (DermNet, OpenI) with proper licensing

---

### 3. Time Travel (Disease Arc Visualization)

**Value: VERY HIGH** | **Complexity: HIGH** | **KILLER FEATURE**

This is pedagogically unique - learners almost never see longitudinal disease evolution compressed.

**Clinical examples:**
| Arc | Infant | Toddler | Child | Adolescent |
|-----|--------|---------|-------|------------|
| Atopic March | Eczema | Food allergy | Allergic rhinitis | Asthma |
| RSV → Asthma | RSV bronchiolitis | Post-viral wheeze (RAD) | Intermittent asthma | Persistent asthma |
| Metabolic | — | Overweight | Obesity + acanthosis | Prediabetes → T2DM |
| ADHD | — | "Busy toddler" | ADHD-combined | ADHD + anxiety |
| Recurrent AOM | First AOM | 3+ AOM/year | Tubes placed | Resolution |

**Implementation approach:**
1. Add `progression_rules` to conditions.yaml:
   ```yaml
   rsv_bronchiolitis:
     progression:
       - trigger_age_months: 6
         next_condition: "reactive_airway_disease"
         probability: 0.3
       - trigger_age_months: 36
         next_condition: "asthma"
         probability: 0.25
   ```
2. Timeline slider UI showing patient at different ages
3. Highlight **what changed**: symptoms, medications, exam findings
4. Show clinical decision points: "At 18 months, started PRN albuterol"

**Critique:** Requires significant conditions.yaml expansion and engine changes. High value justifies complexity.

---

### 4. Results (Labs, Imaging, Reports)

**Value: HIGH** | **Complexity: MEDIUM**

Current state: Basic lab results with LOINC codes exist.

**Gaps:**
- Imaging reports (radiology dictations)
- Imaging actual images (X-rays, ultrasounds)
- Comprehensive lab panels (CBC, CMP, UA with reflex)
- Trend data (serial labs showing improvement/worsening)
- Critical values and notification workflows

**Implementation approach:**
- Expand lab definitions with more panels
- Add `ImagingResult` generation with realistic radiology reports (LLM)
- Consider Gemini for actual image generation (with caution)
- Include normal AND abnormal results with clinical correlation

---

### 5. Mass Generate (Patient Panels)

**Value: HIGH** | **Complexity: LOW**

Essential infrastructure for learning platform.

**Panel characteristics:**
- Size: 15-25 patients (realistic residency panel)
- Distribution: 60% healthy, 25% single chronic, 15% complex
- Age spread: newborn through adolescent
- Continuity: Same patients return for multiple visits

**Implementation:**
- Batch generation API (already exists)
- Panel configuration (age distribution, complexity mix)
- Named panels associated with learners
- Persistence in database

---

### 6. Single Case Generation (New Visits for Panel Patients)

**Value: VERY HIGH** | **Complexity: MEDIUM**

Creates the "continuity clinic" experience.

**Difficulty calibration:**
| Level | Description | Example |
|-------|-------------|---------|
| 1 - Routine | Straightforward, textbook | Well-child with normal development |
| 2 - Standard | Common illness, clear diagnosis | Otitis media with classic findings |
| 3 - Complex | Multiple factors, decisions needed | Asthma + URI, step-up therapy? |
| 4 - Challenging | Atypical presentation, competing diagnoses | Fever without source in infant |
| 5 - Zebra | Rare or unexpected | Kawasaki, leukemia, abuse |

**Aberrant variables:**
- Atypical vitals (afebrile UTI, hypothermic sepsis)
- Conflicting history (parent vs. child story)
- Red herrings (incidental findings)
- Social complexity (housing instability, missed appointments)
- Communication barriers (non-English speaking, deaf parent)

---

### 7. Echo - AI Learning Assistant

**Value: TRANSFORMATIVE** | **Complexity: VERY HIGH** | **SEPARATE PRODUCT**

This is essentially a **clinical reasoning coach**. Core capabilities:

**Socratic dialogue:**
- "What's on your differential?"
- "What would change your mind?"
- "What can't you miss?"

**Feedback modes:**
- Gentle nudge: "Have you considered..."
- Direct teaching: "In this age group, always consider..."
- Literature citation: "Per AAP guidelines, you should..."

**Implementation considerations:**
- Needs full patient context + learner's reasoning
- Must not give answers too easily (learning vs. doing)
- Needs access to clinical references (UpToDate-like, AAP guidelines)
- Assessment of learner's clinical reasoning quality
- Track common errors/misconceptions

**Critique:** This is a major undertaking. Recommend MVP: simple case discussion with feedback, expand over time.

---

### 8. Growth and Development Variations

**Value: HIGH** | **Complexity: MEDIUM**

Pediatrics-specific. Current system has milestones but limited variation.

**Normal variations to model:**
- Early/late walkers (10-18 months range)
- Speech: first words timing, vocabulary explosion
- Gross/fine motor variations by temperament

**Concerning patterns:**
- Language delay (no words by 16 months)
- Motor delay (not walking by 18 months)
- Social concerns (poor eye contact, no pointing)
- Regression (autism red flag)

**Screening integration:**
- ASQ-3 at recommended ages
- M-CHAT-R/F at 18, 24 months
- Edinburgh/PHQ-A for adolescents
- Generate realistic screening results

---

### 9. First-Class Vaccine Engine

**Value: HIGH** | **Complexity: MEDIUM**

Current state: AAP schedule exists, basic implementation.

**Enhancements needed:**
- Catch-up schedule calculator
- Alternative/delayed schedules (with clinical implications)
- Vaccine hesitancy scenarios (parent refuses, partial acceptance)
- Disease outbreaks for unvaccinated (what measles looks like)
- Contraindications and precautions
- Special populations (preterm, immunocompromised)
- International catch-up (adopted children, immigrants)

**Teaching scenarios:**
- "Parent wants to space out vaccines"
- "Child missed 9-month visit, now 15 months - what's needed?"
- "Unvaccinated 4-year-old starting school"
- "Pertussis exposure in daycare"

---

### 10. Edge Case Creation

**Value: VERY HIGH** | **Complexity: HIGH**

Where real learning happens. LLMs and learners both struggle here.

**Edge categories:**
| Type | Example |
|------|---------|
| Atypical presentation | Appendicitis with diarrhea, not RLQ pain |
| Age-inappropriate | Stroke in a teenager, MI in young adult |
| Mimics | Asthma that's actually vocal cord dysfunction |
| Red flags hidden | Headache that's actually brain tumor |
| Social masquerading | "Clumsy child" that's actually abuse |
| Rare disease, common presentation | Fever + rash = Kawasaki, not viral |

**Implementation:**
- Edge case library curated with clinical experts
- Injection of edge elements into routine cases
- Explicit "what can't you miss" teaching points
- Debrief showing the key distinguishing features

**Critique:** Requires expert curation. AI-generated edge cases may be clinically inaccurate.

---

### 11. Database and Login (Supabase + Replit)

**Value: FOUNDATIONAL** | **Complexity: MEDIUM**

Required for everything else. Current: in-memory storage.

**Schema (proposed):**
```
users
  - id, email, role (learner/instructor), created_at

patients (synthetic, reusable)
  - id, demographics_json, created_by, panel_id

panels
  - id, name, owner_id, patient_ids[], config

encounters
  - id, patient_id, encounter_json, generated_at

sessions (learning sessions)
  - id, user_id, patient_id, encounter_id, started_at, completed_at

progress
  - id, user_id, competency, cases_seen, performance_metrics
```

**Deployment:**
- Replit for hosting (simple, shareable)
- Supabase for auth + PostgreSQL
- Consider Supabase Realtime for collaborative features later

---

### 12. Competency Mapping (ACGME/AAP Milestones)

**Value: HIGH** | **Complexity: MEDIUM**

Link every case to specific competencies for structured learning.

**ACGME Core Competencies:**
- Patient Care (PC)
- Medical Knowledge (MK)
- Practice-Based Learning (PBLI)
- Interpersonal Communication (ICS)
- Professionalism (PROF)
- Systems-Based Practice (SBP)

**AAP-Specific Milestones (Pediatrics):**
- Development/Behavior
- Acute Care
- Chronic Care
- Preventive Care
- Advocacy

**Implementation:**
```yaml
# In encounter or case definition
competencies:
  primary: ["PC-1", "MK-3"]  # Main teaching focus
  secondary: ["ICS-2"]        # Also addresses
  milestone_level: 2          # Expected learner level (1-5)
```

**Features:**
- Filter cases by competency
- Track learner progress per competency
- Identify gaps: "You've seen 12 acute care cases, only 2 preventive"
- Suggest cases to fill gaps

---

### 13. Documentation Practice (Note Writing + AI Feedback)

**Value: VERY HIGH** | **Complexity: MEDIUM**

Residents learn by writing notes, but rarely get structured feedback.

**Workflow:**
1. Learner reviews case (history, exam, labs)
2. Learner writes their own HPI, Assessment, Plan
3. AI compares to "expert" version
4. Provides structured feedback:
   - Missing elements
   - Incorrect information
   - Style/clarity issues
   - Billing implications

**Feedback categories:**
- **Completeness**: "Missing duration of fever"
- **Accuracy**: "Lung exam documented as clear, but patient has rales"
- **Reasoning**: "Assessment doesn't explain why you chose this diagnosis"
- **Billing**: "This note would support a Level 3, not Level 4 E&M"

**Technical approach:**
- LLM comparison with rubric
- Structured checklist (SOAP elements)
- Side-by-side diff view
- Scoring (0-100 with breakdown)

---

### 14. Billing/Coding Practice (E&M Levels, CPT/ICD)

**Value: HIGH** | **Complexity: MEDIUM**

Residents are notoriously bad at billing. This is teachable.

**Teaching elements:**

| Concept | What to Learn |
|---------|---------------|
| E&M Levels | 99211-99215 (office), 99281-99285 (ED) |
| Time-based billing | 2021 changes, when to use |
| MDM complexity | Low, moderate, high, high/uncertain |
| ICD-10 coding | Primary diagnosis, specificity matters |
| CPT procedures | Lumbar puncture, I&D, suturing, etc. |

**Workflow:**
1. Learner sees completed encounter
2. Asked: "What E&M level would you bill?"
3. Asked: "What ICD-10 codes apply?"
4. AI evaluates and explains:
   - "This is a Level 4 because of moderate MDM complexity"
   - "You said J06.9 (URI), but J20.9 (acute bronchitis) is more specific"

**Integration with existing:**
- `BillingCodes` model already exists in patient.py
- Encounters already have diagnosis codes
- Add "expert billing" to compare against learner

---

### 15. Spaced Repetition (Anki-like Case Review)

**Value: HIGH** | **Complexity: MEDIUM**

Optimal learning happens when cases resurface at the right intervals.

**Algorithm (SM-2 based):**
```
if correct_response:
    interval = previous_interval * ease_factor
    ease_factor += 0.1
else:
    interval = 1 day
    ease_factor -= 0.2

next_review = today + interval
```

**What gets reviewed:**
- Key clinical pearls from past cases
- Diagnoses the learner missed
- Edge cases/zebras
- Competency gaps

**Presentation:**
- "Remember this case from 3 weeks ago?"
- "What was the diagnosis?"
- "What was the key finding you missed?"
- Quick flashcard-style review

**Data model:**
```
reviews
  - id, user_id, case_id, last_seen, next_review
  - ease_factor, interval_days, repetitions
  - correct_count, incorrect_count
```

**Integration:**
- Dashboard shows "X cases due for review today"
- Quick 5-minute review mode
- Ties into competency tracking

---

## Additional Features (Deferred)

3. **Parent Communication** - Practice breaking bad news, vaccine counseling
4. **Handoff Practice** - Generate sign-out scenarios, practice IPASS
6. **Peer Comparison** - "75% of learners ordered X at this point"
8. **Voice Presentation** - Practice verbal case presentation (speech-to-text)

### Lower Priority:

9. Mobile app (can be PWA initially)
10. LMS integration (SCORM/LTI - later for institutional adoption)
11. Multi-language support (global reach, but adds complexity)

---

## Critiques and Concerns

### Technical Risks
- **Gemini image generation quality** for medical content is unproven
- **Database migration** changes architectural assumptions
- **AI accuracy** for edge cases and feedback needs validation

### Pedagogical Risks
- How to measure **actual learning outcomes**?
- Risk of **reinforcing misconceptions** if AI feedback is wrong
- Who **validates** the edge cases and clinical scenarios?

### Product Risks
- **Scope creep**: This is becoming a platform, not a tool
- **Who is the user?** Med students? Residents? Attendings? Nurses?
- **Business model**: Free? Subscription? Institutional license?

### Mitigations
- Start with **clinical expert review** of generated content
- Build **feedback mechanisms** for learners to report issues
- **Phased rollout** with validation at each stage
- Partner with **residency programs** for real-world testing

---

## Recommended Phasing

### Phase 1: Foundation
**Goal: Persistent, multi-user learning infrastructure**

1. **Database + Auth** (#11)
   - Supabase setup, user auth
   - Patient/panel schema
   - Migrate from in-memory to PostgreSQL
   - Learner role/level tracking (NP student → senior resident)

2. **Mass Generate** (#5)
   - Panel configuration
   - Batch generation
   - Panel persistence

3. **Single Case** (#6) - Basic
   - New encounter for existing patient
   - **Adaptive difficulty** based on learner level
   - Continuity preservation

### Phase 2: Clinical Depth
**Goal: Richer, more realistic patient content**

4. **Time Travel** (#3) - KILLER FEATURE
   - Disease progression rules in conditions.yaml
   - Age slider UI
   - Snapshot generation at different ages
   - Show evolution of symptoms, meds, findings

5. **Vaccine Engine** (#9)
   - Catch-up calculator
   - Alternative/delayed schedules
   - Hesitancy scenarios
   - Disease risk for unvaccinated

6. **Growth/Development** (#8)
   - Normal variation modeling
   - Screening integration (ASQ, M-CHAT)
   - Delay patterns and red flags

7. **Results Expansion** (#4)
   - Comprehensive lab panels
   - Radiology reports (text descriptions, no images)
   - Trend data over time

### Phase 3: Learning Experience
**Goal: Active, adaptive learning with structured feedback**

8. **Echo - Full Attending Simulation** (#7) - ELEVATED PRIORITY
   - Socratic questioning ("What's on your differential?")
   - Direct teaching with clinical pearls
   - Literature/guideline references
   - Feedback on learner reasoning
   - Adaptive to learner level
   - **Validation flagging** built-in

9. **Documentation Practice** (#13)
   - Learner writes HPI, Assessment, Plan
   - AI comparison to expert version
   - Structured feedback (completeness, accuracy, style)
   - Scoring with breakdown
   - Ties into billing practice

10. **Billing/Coding Practice** (#14)
    - E&M level estimation exercises
    - ICD-10 coding practice
    - MDM complexity teaching
    - Feedback on over/under-coding

11. **Edge Cases** (#10)
    - Expert-curated edge library
    - Injection into routine cases
    - Teaching points for each edge
    - "What can't you miss" emphasis

### Phase 4: Platform & Retention
**Goal: Sustainable learning with tracking**

12. **Competency Mapping** (#12)
    - Link cases to ACGME milestones
    - AAP pediatric-specific competencies
    - Filter cases by competency
    - Gap analysis and suggestions

13. **Spaced Repetition** (#15)
    - SM-2 algorithm for case resurfacing
    - "Cases due for review" dashboard
    - Quick flashcard review mode
    - Reinforcement of missed diagnoses

14. **Artifacts** (#1)
    - Template library (school forms, 504 plans, etc.)
    - Condition-triggered generation
    - LLM personalization
    - PDF/form export

15. **User Feedback System**
    - Flag incorrect content
    - Suggest improvements
    - Track common issues

16. **Analytics Dashboard**
    - Cases completed, difficulty progression
    - Competency heatmap
    - Spaced repetition stats
    - Panel utilization

*Note: Image generation removed per user decision. Gemini may be reconsidered later for document/form generation only.*

---

## Critical Files to Modify

| Phase | Files | Purpose |
|-------|-------|---------|
| 1 | `server.py` | Add auth middleware, panel endpoints |
| 1 | new `src/db/` module | Supabase integration, schemas |
| 1 | new `src/models/user.py` | User, Panel, LearnerLevel models |
| 2 | `knowledge/conditions/conditions.yaml` | Add progression rules |
| 2 | `src/engines/engine.py` | Time travel snapshot generation |
| 2 | `web/index.html` | Age slider UI |
| 2 | `knowledge/immunizations/` | Catch-up calculator, alt schedules |
| 3 | new `src/llm/echo.py` | Attending simulation engine |
| 3 | new `src/learning/documentation.py` | Note writing + AI feedback |
| 3 | new `src/learning/billing.py` | E&M and coding practice |
| 3 | new `knowledge/edge_cases/` | Curated edge case library |
| 4 | new `knowledge/competencies/` | ACGME/AAP milestone definitions |
| 4 | new `src/learning/spaced_rep.py` | SM-2 algorithm, review scheduling |
| 4 | new `src/artifacts/` | Template library, form generation |
| 4 | new `src/feedback/` | User flagging, issue tracking |

---

## User Decisions (Confirmed)

1. **Audience**: ALL learners - NP students through senior pediatric residents
   - Implication: Adaptive difficulty scaling is critical
   - Content must work from fundamentals to nuanced edge cases

2. **Images**: SKIP image generation entirely
   - Focus on rich text descriptions
   - Removes Gemini/Imagen complexity
   - Gemini may still be useful for forms/documents (text output)

3. **Echo**: Full attending simulation
   - Socratic questioning + teaching + feedback + literature
   - This is a major undertaking but high priority
   - Will need careful prompt engineering for clinical accuracy

4. **Validation**: Self-review + platform feedback
   - User is a physician, can validate personally
   - Build flagging system for users to report issues
   - Iterate based on learner feedback

---

## Summary

**15 features** transform Oread into a **comprehensive pediatric learning platform** serving NP students through senior residents.

### All Features (Final List):

| # | Feature | Phase | Priority |
|---|---------|-------|----------|
| 1 | Artifacts (forms, orders) | 4 | Medium |
| 3 | Time Travel (disease arcs) | 2 | **Critical** |
| 4 | Results (labs, imaging) | 2 | High |
| 5 | Mass Generate (panels) | 1 | High |
| 6 | Single Case (new visits) | 1 | High |
| 7 | Echo (AI attending) | 3 | **Critical** |
| 8 | Growth/Development | 2 | High |
| 9 | Vaccine Engine | 2 | High |
| 10 | Edge Cases | 3 | High |
| 11 | Database + Auth | 1 | **Critical** |
| 12 | Competency Mapping | 4 | High |
| 13 | Documentation Practice | 3 | **Critical** |
| 14 | Billing/Coding | 3 | High |
| 15 | Spaced Repetition | 4 | High |

### Key Decisions Made:
- **Images**: Deferred entirely - focus on rich text descriptions
- **Echo**: Full attending simulation (elevated priority)
- **Audience**: Adaptive difficulty for all learner levels
- **Validation**: Self-review + built-in user flagging

### Critical Success Factors:
1. **Time Travel** (#3) - Killer feature, unique pedagogical value
2. **Echo** (#7) - Transformative attending simulation
3. **Documentation Practice** (#13) - High-value skill building
4. **Spaced Repetition** (#15) - Retention and reinforcement
5. **Database** (#11) - Foundation for all persistence

### What's NOT in scope (for now):
- Image generation (Gemini/Imagen) - #2 deferred
- Parent communication practice
- Handoff/IPASS practice
- Voice presentation
- Mobile app (can use PWA)
- LMS integration (SCORM/LTI)

---

## Implementation Specifications

### Spec 1: Database Schema (Supabase)

```sql
-- Users and Authentication
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  role TEXT CHECK (role IN ('learner', 'instructor', 'admin')) DEFAULT 'learner',
  learner_level TEXT CHECK (learner_level IN (
    'np_student', 'ms3', 'ms4', 'intern', 'pgy2', 'pgy3', 'fellow', 'attending'
  )),
  institution TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Patient Panels
CREATE TABLE panels (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  owner_id UUID REFERENCES users(id),
  config JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Synthetic Patients
CREATE TABLE patients (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  panel_id UUID REFERENCES panels(id),
  demographics JSONB NOT NULL,
  full_record JSONB NOT NULL,
  complexity_tier TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Encounters (generated on-demand)
CREATE TABLE encounters (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id UUID REFERENCES patients(id),
  encounter_json JSONB NOT NULL,
  difficulty_level INT CHECK (difficulty_level BETWEEN 1 AND 5),
  competencies TEXT[],
  generated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Learning Sessions
CREATE TABLE sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  encounter_id UUID REFERENCES encounters(id),
  started_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  learner_notes TEXT,
  learner_billing JSONB,
  echo_transcript JSONB,
  score JSONB
);

-- Spaced Repetition
CREATE TABLE reviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  encounter_id UUID REFERENCES encounters(id),
  next_review TIMESTAMPTZ,
  ease_factor FLOAT DEFAULT 2.5,
  interval_days INT DEFAULT 1,
  repetitions INT DEFAULT 0
);

-- Competency Progress
CREATE TABLE competency_progress (
  user_id UUID REFERENCES users(id),
  competency_code TEXT NOT NULL,
  cases_seen INT DEFAULT 0,
  avg_score FLOAT,
  PRIMARY KEY (user_id, competency_code)
);

-- User Feedback
CREATE TABLE feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  encounter_id UUID REFERENCES encounters(id),
  feedback_type TEXT,
  content TEXT NOT NULL,
  status TEXT DEFAULT 'new',
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

### Spec 2: Echo Attending Simulation

```python
# src/llm/echo.py

class EchoAttending:
    """AI attending physician for Socratic learning."""

    MODES = ["socratic", "teaching", "feedback"]

    def __init__(self, learner_level: str, patient_context: dict):
        self.level = learner_level
        self.context = patient_context
        self.conversation = []

    def respond(self, learner_input: str, mode: str = "socratic") -> str:
        system = f"""You are a pediatric attending working with a {self.level}.

Patient: {self._summarize_context()}

Mode: {mode}
- socratic: Ask ONE probing question. Don't give the answer.
- teaching: Learner is stuck. Give ONE clinical pearl, then ask follow-up.
- feedback: Evaluate reasoning. Acknowledge correct, gently correct errors.

Adjust complexity to learner level. Never belittle."""

        messages = self._build_messages(learner_input)
        return llm.generate(messages, system=system)
```

---

### Spec 3: Documentation Practice

```python
# src/learning/documentation.py

class DocumentationEvaluator:
    """Evaluate learner-written clinical notes."""

    def evaluate(self, learner_note: str, expert_note: str, encounter: dict) -> dict:
        prompt = f"""Compare these notes for the same encounter.

ENCOUNTER: {json.dumps(encounter)}

LEARNER: {learner_note}

EXPERT: {expert_note}

Score 0-100 on:
1. COMPLETENESS - Missing elements?
2. ACCURACY - Factual errors?
3. REASONING - Clear thinking?
4. BILLING - What E&M level would this support?

Return JSON with scores and specific feedback."""

        return llm.generate_structured(prompt, DocumentationFeedback)
```

---

### Spec 4: Spaced Repetition (SM-2)

```python
# src/learning/spaced_rep.py

def schedule_next_review(review: dict, quality: int) -> dict:
    """SM-2 algorithm. quality: 0-5 (0=blackout, 5=perfect)"""

    if quality >= 3:  # Correct
        if review["repetitions"] == 0:
            review["interval_days"] = 1
        elif review["repetitions"] == 1:
            review["interval_days"] = 6
        else:
            review["interval_days"] *= review["ease_factor"]

        review["ease_factor"] = max(1.3, review["ease_factor"] + 0.1 - (5-quality)*0.08)
        review["repetitions"] += 1
    else:  # Incorrect
        review["interval_days"] = 1
        review["repetitions"] = 0
        review["ease_factor"] = max(1.3, review["ease_factor"] - 0.2)

    review["next_review"] = now() + timedelta(days=review["interval_days"])
    return review
```

---

### Spec 5: Competency Mapping

```yaml
# knowledge/competencies/acgme_peds.yaml

patient_care:
  PC-1:
    name: "Gather essential information"
    milestones: [1,2,3,4,5]
    criteria: ["onset/duration", "associated symptoms", "red flags"]

  PC-2:
    name: "Develop differential diagnosis"

medical_knowledge:
  MK-1:
    name: "Clinical reasoning"

# Map conditions to competencies
condition_mapping:
  otitis_media: ["PC-1", "PC-2", "MK-1"]
  asthma_exacerbation: ["PC-3", "MK-2", "SBP-1"]
```

---

### Spec 6: Time Travel (Disease Progression)

```yaml
# knowledge/conditions/progressions.yaml

rsv_to_asthma:
  name: "RSV → Reactive Airway → Asthma"
  stages:
    - age_months: 3
      condition: "rsv_bronchiolitis"
      symptoms: ["wheeze", "cough"]
      treatment: ["supportive"]

    - age_months: 18
      condition: "reactive_airway_disease"
      trigger: "viral_uri"
      treatment: ["albuterol_prn"]

    - age_months: 48
      condition: "asthma_intermittent"
      treatment: ["albuterol_prn"]

    - age_months: 84
      condition: "asthma_persistent_mild"
      treatment: ["low_dose_ics", "albuterol_prn"]

atopic_march:
  stages:
    - {age: 4, condition: "eczema"}
    - {age: 12, condition: "food_allergy"}
    - {age: 36, condition: "asthma"}
    - {age: 72, condition: "allergic_rhinitis"}
```

---

### Spec 7: Billing/Coding Practice

```python
# src/learning/billing.py

E_M_LEVELS = {
    "99211": {"mdm": "minimal", "time": None},
    "99212": {"mdm": "straightforward", "time": 10},
    "99213": {"mdm": "low", "time": 20},
    "99214": {"mdm": "moderate", "time": 30},
    "99215": {"mdm": "high", "time": 40},
}

def evaluate_billing(learner_codes: dict, expert_codes: dict, encounter: dict) -> dict:
    prompt = f"""Evaluate this billing attempt.

ENCOUNTER: {encounter}

LEARNER BILLED:
- E&M: {learner_codes["em_level"]}
- ICD-10: {learner_codes["icd10"]}

CORRECT BILLING:
- E&M: {expert_codes["em_level"]}
- ICD-10: {expert_codes["icd10"]}

Explain:
1. Why the correct E&M level applies (MDM complexity)
2. Why the correct ICD-10 codes are more specific
3. Common billing errors to avoid"""

    return llm.generate(prompt)
```

---

## Next Steps

1. **Start with Phase 1**: Supabase setup, auth, database migration
2. **Track progress** using the ROADMAP.md file
3. **Validate clinically** as you build each feature
