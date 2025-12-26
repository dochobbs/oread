# Time Travel Feature Design

## Overview

**Time Travel** allows learners to see how a patient's conditions evolve across their lifespan. Instead of seeing a static snapshot, learners can "scrub" through time to watch diseases progress, treatments change, and clinical decisions unfold.

This is the **killer feature** for medical education because learners almost never get to see compressed longitudinal disease evolution in training.

---

## Clinical Value

### What Learners Currently Miss

In residency, you see patients at discrete points:
- A 6-month-old with RSV bronchiolitis
- A different 3-year-old with reactive airway disease
- A different 8-year-old with asthma

You rarely follow the **same child** through this progression. Time Travel fixes that.

### Example Disease Arcs

| Arc Name | Progression |
|----------|-------------|
| **RSV → Asthma** | RSV bronchiolitis (6mo) → Post-viral wheeze (18mo) → Intermittent asthma (4yo) → Persistent asthma (7yo) |
| **Atopic March** | Eczema (4mo) → Food allergy (12mo) → Asthma (3yo) → Allergic rhinitis (6yo) |
| **Metabolic Syndrome** | Overweight (5yo) → Obesity + acanthosis (10yo) → Prediabetes (14yo) → T2DM (17yo) |
| **Recurrent AOM** | First AOM (9mo) → 3+ AOM/year (18mo) → Tubes placed (2yo) → Resolution (4yo) |
| **ADHD Evolution** | "Busy toddler" (2yo) → ADHD-combined diagnosis (6yo) → ADHD + anxiety (12yo) |
| **Prematurity Sequelae** | 28-week preemie → BPD (NICU) → Home O2 (3mo) → Resolved (1yo) → Asthma (5yo) |

---

## User Experience

### Timeline Slider Interface

```
[Patient Header: Maya Johnson, currently 8 years old]

Timeline: ──●────────────────────────────────────
          Birth  1y   2y   3y   4y   5y   6y   7y   8y
                      ▲
                 [Viewing at age 2]

┌─────────────────────────────────────────────────┐
│ Age 2 Snapshot                                  │
│                                                 │
│ Active Conditions: Eczema, Reactive Airway      │
│ Medications: Hydrocortisone cream, Albuterol PRN│
│ Recent Visit: Wheezing episode after cold       │
│                                                 │
│ [What Changed]                                  │
│ • NEW: Reactive airway disease diagnosed        │
│ • Started: Albuterol PRN                        │
│ • Eczema: Worsening, increased steroid potency  │
└─────────────────────────────────────────────────┘

[Key Decision Point] At 18 months, post-viral wheeze
occurred 3x. Would you start daily controller therapy?
```

### Key UI Elements

1. **Timeline Slider**: Scrub from birth to current age
2. **Snapshot Panel**: Shows patient state at selected age
3. **What Changed**: Highlights differences from previous snapshot
4. **Key Decision Points**: Marks where clinical decisions were made
5. **Disease Arc Visualization**: Shows progression of related conditions

### Interaction Modes

| Mode | Description |
|------|-------------|
| **Browse** | Free-form slider exploration |
| **Guided** | Step through key moments with explanations |
| **Quiz** | "What would you do at this point?" |
| **Compare** | Side-by-side of two time points |

---

## Data Model

### Progression Rules (conditions.yaml)

```yaml
rsv_bronchiolitis:
  display_name: "RSV Bronchiolitis"
  # ... existing fields ...

  progression:
    - name: "reactive_airway_disease"
      trigger:
        type: "age_reached"
        age_months: 18
      probability: 0.30
      risk_factors:
        - "family_history_asthma"    # increases to 0.45
        - "eczema"                    # increases to 0.50
        - "smoke_exposure"            # increases to 0.55

    - name: "asthma_intermittent"
      trigger:
        type: "condition_duration"
        from_condition: "reactive_airway_disease"
        duration_months: 24
      probability: 0.40

reactive_airway_disease:
  progression:
    - name: "asthma_intermittent"
      trigger:
        type: "episode_count"
        episodes: 4
        within_months: 12
      probability: 0.60

    - name: "resolution"  # Special: condition resolves
      trigger:
        type: "age_reached"
        age_months: 72
      probability: 0.40
```

### Snapshot Model

```python
@dataclass
class TimeSnapshot:
    """Patient state at a specific point in time."""
    age_months: int
    date: date

    # Clinical state
    active_conditions: list[Condition]
    medications: list[Medication]
    growth: GrowthMeasurement

    # What changed since last snapshot
    new_conditions: list[str]
    resolved_conditions: list[str]
    medication_changes: list[MedicationChange]

    # Key events at this time
    encounters: list[Encounter]
    decision_points: list[DecisionPoint]

@dataclass
class DecisionPoint:
    """A clinical decision that was made."""
    age_months: int
    description: str
    decision_made: str
    alternatives: list[str]
    rationale: str
    outcome: str  # What happened as a result
```

### Disease Arc Model

```python
@dataclass
class DiseaseArc:
    """A tracked progression of related conditions."""
    name: str  # e.g., "Atopic March"
    description: str

    stages: list[ArcStage]
    current_stage: int

    # Teaching content
    clinical_pearls: list[str]
    references: list[str]

@dataclass
class ArcStage:
    """One stage in a disease arc."""
    condition_key: str
    typical_age_range: tuple[int, int]  # months
    actual_age: int | None  # When it happened for this patient

    symptoms: list[str]
    treatments: list[str]
    transition_triggers: list[str]  # What causes progression
```

---

## API Design

### Generate Patient with Time Travel

```
POST /api/generate
{
    "age": 8,
    "disease_arcs": ["rsv_to_asthma", "atopic_march"],
    "generate_snapshots": true,
    "snapshot_interval_months": 6
}
```

### Get Patient Snapshots

```
GET /api/patients/{id}/timeline
Response:
{
    "patient_id": "...",
    "current_age_months": 96,
    "snapshots": [
        {
            "age_months": 0,
            "date": "2017-01-15",
            "active_conditions": [],
            "medications": [],
            ...
        },
        {
            "age_months": 6,
            "date": "2017-07-15",
            "active_conditions": ["rsv_bronchiolitis"],
            "new_conditions": ["rsv_bronchiolitis"],
            ...
        },
        ...
    ],
    "disease_arcs": [
        {
            "name": "RSV to Asthma",
            "stages": [...],
            "current_stage": 2
        }
    ],
    "decision_points": [...]
}
```

### Get Snapshot at Specific Age

```
GET /api/patients/{id}/timeline/at/{age_months}
Response:
{
    "age_months": 24,
    "snapshot": {...},
    "previous_snapshot": {...},
    "changes": {
        "new_conditions": ["reactive_airway_disease"],
        "resolved_conditions": [],
        "medication_changes": [
            {"type": "started", "medication": "Albuterol"}
        ]
    }
}
```

---

## Implementation Phases

### Phase 1: Data Model & Generation
- Add `progression` field to conditions.yaml
- Create `TimeSnapshot` and `DiseaseArc` models
- Modify PedsEngine to generate snapshots during patient creation
- Store snapshots in patient record

### Phase 2: API Endpoints
- `/api/patients/{id}/timeline` - Get all snapshots
- `/api/patients/{id}/timeline/at/{age}` - Get specific snapshot
- `/api/patients/{id}/arcs` - Get disease arcs

### Phase 3: Basic UI
- Timeline slider component
- Snapshot display panel
- "What changed" highlighting

### Phase 4: Enhanced UI
- Disease arc visualization
- Decision point markers
- Guided mode with teaching content
- Quiz mode integration

---

## Disease Arcs to Implement

### Priority 1 (Common, High Teaching Value)

| Arc | Conditions | Teaching Focus |
|-----|------------|----------------|
| RSV → Asthma | RSV, RAD, Asthma | Viral-induced wheeze progression |
| Atopic March | Eczema, Food allergy, Asthma, Rhinitis | Allergic disease progression |
| Recurrent AOM | AOM, OME, Tubes | When to refer, tube criteria |
| Obesity → Metabolic | Overweight, Obesity, Prediabetes, T2DM | Metabolic syndrome progression |

### Priority 2 (Important but Less Common)

| Arc | Conditions | Teaching Focus |
|-----|------------|----------------|
| ADHD + Comorbidities | ADHD, Anxiety, Depression | Mental health evolution |
| Prematurity Sequelae | BPD, ROP, Developmental delay | Long-term preemie outcomes |
| Failure to Thrive | FTT, Feeding disorder, Resolution | Growth monitoring |
| Constipation Cycle | Functional constipation, Encopresis, Resolution | Bowel retraining |

### Priority 3 (Zebras for Advanced Learners)

| Arc | Conditions | Teaching Focus |
|-----|------------|----------------|
| JIA Progression | JIA, Uveitis, Growth effects | Autoimmune monitoring |
| IBD Evolution | Crohn's/UC, Growth failure, Complications | Chronic disease management |
| Epilepsy Course | Seizures, Medication trials, Resolution/persistence | Seizure management |

---

## Open Questions

1. **Snapshot Granularity**: Every month? Every 3 months? Only at key events?
   - Recommendation: Key events + quarterly snapshots

2. **Retroactive Generation**: If a patient exists without snapshots, can we generate them?
   - Recommendation: Yes, with a "reconstruct timeline" feature

3. **Branching Paths**: Should we show "what if" scenarios?
   - Recommendation: Phase 5 feature - show alternate outcomes

4. **Data Storage**: Snapshots could be large. Store all or generate on-demand?
   - Recommendation: Store key snapshots, interpolate others

5. **LLM Integration**: Use LLM to generate narrative for each snapshot?
   - Recommendation: Yes, but cache aggressively

---

## Success Metrics

- Learners can identify disease progression patterns
- Learners understand when/why clinical decisions were made
- Learners can predict next steps in disease evolution
- Time to understand a patient's full history is reduced

---

## References

- AAP Bright Futures Guidelines (well-child timing)
- NAEPP Asthma Guidelines (asthma classification progression)
- AAP Clinical Practice Guidelines (condition-specific)
