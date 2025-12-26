# Time Travel Feature - Implementation Spec

## Overview

This document specifies the implementation of the Time Travel feature for the synthetic patient generator. Time Travel allows learners to scrub through a patient's lifespan and see how conditions, medications, and clinical decisions evolve over time.

**Primary use case**: Medical education—learners rarely follow the same patient longitudinally in training. This feature compresses years of disease evolution into an interactive timeline.

---

## UI Architecture

### Integration Point: Header-Embedded Timeline

The timeline slider lives **in the patient header**, not in a separate tab or floating panel. Rationale: Time Travel changes the context of everything on the page, so it should be visually "above" the content it affects.

```
┌─────────────────────────────────────────────────────────────────┐
│ [← Back to list]                                      [Delete]  │
│                                                                 │
│ Patient Name                                                    │
│ {dynamic age display} • Sex • DOB: YYYY-MM-DD                   │
│ [N Conditions]                                                  │
│                                                                 │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ TIME TRAVEL                            [Atopic March]       │ │
│ │                                        [Reset to current]   │ │
│ │                                                             │ │
│ │ ●────────●─────────●────────●────────●────────○─────────── │ │
│ │ Birth   1y        2y       3y       4y       6y        10y │ │
│ │         ▲ Key moments (amber dots)                          │ │
│ │                                                             │ │
│ │           Viewing at 3y — First wheezing episode            │ │
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### Age Display Logic

When viewing current age:
- Show: "10 years old"

When time traveling:
- Header shows: "Viewing at {age}" 
- Format ages as: "4mo", "18mo", "2y", "2y 6mo", "10 years"
- Use months for < 24 months, then years (with months if not exactly on year boundary)

### Tab Structure

```
[Overview]  [Encounters]  [Messages]  [Full Record]  [Timeline]
```

- **Overview**: Existing summary view, but now reactive to selected time point
- **Timeline** (new): Disease arc visualization and key decision points
- Other tabs: Filter to show only data up to selected time point

---

## Component Specifications

### 1. TimelineSlider Component

**Location**: Embedded in patient header (below demographics, above tabs)

**Props**:
```typescript
interface TimelineSliderProps {
  currentAgeMonths: number;          // Patient's actual current age
  selectedAgeMonths: number;         // Currently viewed age
  onAgeChange: (months: number) => void;
  snapshots: TimeSnapshot[];         // For key moment markers
  diseaseArc?: DiseaseArc;           // Optional arc label to display
}
```

**Behavior**:
- Range: 0 to currentAgeMonths
- Key moments rendered as amber dots on the track
- Key moments should be clickable (jump to that age)
- "Reset to current" button appears when selectedAge ≠ currentAge
- Disease arc name displayed as a badge if patient has one

**Styling**:
- Background: `teal-900/30` (semi-transparent dark teal)
- Slider track: `teal-950/50`
- Slider thumb: `amber-500`
- Key moment markers: `amber-400`, 8px circles
- Text: `teal-200` for labels, `amber-300` for current viewing age

### 2. WhatChangedPanel Component

**Location**: Top of Overview content, below tabs

**Visibility**: Only shown when `selectedAgeMonths !== currentAgeMonths` AND there are changes to display

**Props**:
```typescript
interface WhatChangedPanelProps {
  ageDisplay: string;
  changes: {
    newConditions: string[];
    resolvedConditions: string[];
    newMeds: string[];
    stoppedMeds: string[];
  };
}
```

**Styling**:
- Background: `amber-50`
- Border: `amber-200`
- Section labels: green for additions (`green-700`), muted for removals (`stone-500`)

### 3. DiseaseArcVisualization Component

**Location**: Timeline tab content

**Props**:
```typescript
interface DiseaseArcVisualizationProps {
  arc: DiseaseArc;
  patientSnapshots: TimeSnapshot[];
}
```

**Display**:
- Horizontal flow showing stages: numbered circles connected by a line
- Each stage shows: name, onset age, current status (active/improving/resolved)
- Color coding:
  - Active: `teal-100` background, `teal-500` border
  - Improving: `amber-100` background, `amber-400` border  
  - Resolved: `stone-100` background, `stone-300` border

### 4. DecisionPointCard Component

**Location**: Timeline tab, below disease arc

**Props**:
```typescript
interface DecisionPointCardProps {
  ageDisplay: string;
  question: string;
  decision: string;
  alternatives?: string[];
  outcome?: string;
}
```

**Styling**:
- Background: `teal-50`
- Border: `teal-200`
- Age badge: `teal-700` background, white text

---

## Data Model

### TimeSnapshot

```typescript
interface TimeSnapshot {
  ageMonths: number;
  date: string;                      // ISO date
  
  // Clinical state at this point
  activeConditions: Condition[];
  medications: Medication[];
  growth?: GrowthMeasurement;
  
  // What changed since previous snapshot
  newConditions: string[];           // Condition keys
  resolvedConditions: string[];
  medicationChanges: MedicationChange[];
  
  // Events at this time
  encounters: Encounter[];
  decisionPoints: DecisionPoint[];
  
  // UI metadata
  isKeyMoment: boolean;              // Show as marker on timeline
  eventDescription?: string;         // Brief label for this moment
}

interface MedicationChange {
  type: 'started' | 'stopped' | 'dose_changed';
  medication: string;
  details?: string;
}

interface DecisionPoint {
  ageMonths: number;
  description: string;               // The clinical question
  decisionMade: string;              // What was decided
  alternatives: string[];            // Other options considered
  rationale: string;                 // Why this choice
  outcome?: string;                  // What happened as a result
}
```

### DiseaseArc

```typescript
interface DiseaseArc {
  name: string;                      // e.g., "Atopic March"
  description: string;
  stages: ArcStage[];
  currentStageIndex: number;
  
  // Teaching content
  clinicalPearls: string[];
  references: string[];
}

interface ArcStage {
  conditionKey: string;
  displayName: string;
  typicalAgeRange: [number, number]; // months
  actualOnsetAge?: number;           // When it happened for this patient
  status: 'pending' | 'active' | 'improving' | 'resolved';
  
  symptoms: string[];
  treatments: string[];
  transitionTriggers: string[];      // What causes progression to next stage
}
```

---

## API Endpoints

### GET /api/patients/{id}/timeline

Returns full timeline data for a patient.

**Response**:
```json
{
  "patientId": "...",
  "currentAgeMonths": 120,
  "snapshots": [TimeSnapshot, ...],
  "diseaseArcs": [DiseaseArc, ...],
  "decisionPoints": [DecisionPoint, ...]
}
```

### GET /api/patients/{id}/timeline/at/{ageMonths}

Returns snapshot at specific age with computed changes.

**Response**:
```json
{
  "ageMonths": 36,
  "snapshot": TimeSnapshot,
  "previousSnapshot": TimeSnapshot | null,
  "changes": {
    "newConditions": ["reactive_airway_disease"],
    "resolvedConditions": [],
    "medicationChanges": [
      {"type": "started", "medication": "Albuterol PRN"}
    ]
  }
}
```

### POST /api/generate (modified)

Add parameters for generating patients with time travel data:

```json
{
  "age": 8,
  "diseaseArcs": ["rsv_to_asthma"],
  "generateSnapshots": true,
  "snapshotIntervalMonths": 6
}
```

---

## State Management

### Patient Detail Page State

```typescript
interface PatientDetailState {
  patient: Patient;
  timeline: {
    snapshots: TimeSnapshot[];
    diseaseArcs: DiseaseArc[];
    decisionPoints: DecisionPoint[];
  };
  
  // Time travel state
  selectedAgeMonths: number;         // What age we're viewing
  isTimeTraveling: boolean;          // selectedAge !== currentAge
  
  // Computed from selectedAgeMonths
  currentSnapshot: TimeSnapshot;
  changes: Changes;
}
```

### URL State (optional but recommended)

Encode selected age in URL for shareability:
```
/patients/{id}?age=36
```

---

## Implementation Phases

### Phase 1: UI Shell
- [ ] Add TimelineSlider component to patient header
- [ ] Add Timeline tab
- [ ] Wire up age selection state (local state only, no real data)
- [ ] Implement age display formatting utility

### Phase 2: Snapshot Display
- [ ] Modify Overview tab to accept `selectedAgeMonths` prop
- [ ] Filter/display conditions and meds based on selected age
- [ ] Implement WhatChangedPanel component

### Phase 3: Backend Data Model
- [ ] Add `progression` field to conditions.yaml
- [ ] Create TimeSnapshot and DiseaseArc models
- [ ] Modify patient generation to create snapshots
- [ ] Implement timeline API endpoints

### Phase 4: Disease Arc Visualization
- [ ] Implement DiseaseArcVisualization component
- [ ] Implement DecisionPointCard component
- [ ] Populate Timeline tab with real data

### Phase 5: Polish
- [ ] Make key moment markers clickable
- [ ] Add URL state for selected age
- [ ] Add keyboard navigation (arrow keys to step through snapshots)
- [ ] Cache/optimize snapshot fetching

---

## Reference Implementation

See `time-travel-mockup.jsx` for a working React prototype demonstrating the UI patterns described above. This mockup uses simulated data but demonstrates:

- Timeline slider with key moment markers
- Dynamic header age display
- WhatChangedPanel appearance/behavior
- Disease arc visualization
- Decision point cards

The mockup can be run standalone to validate UX decisions before backend integration.

---

## Design Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Timeline placement | In header | Affects all content, should be visually superior |
| Disease arc tab | Separate tab | Arc is conceptual scaffolding; once understood, users want raw data |
| What Changed panel | Conditional | Reduces noise when viewing current state |
| Key moments | Amber markers | Distinct from slider thumb, draws attention |
| Age display format | Dynamic | "4mo" is clearer than "0y 4mo" for infants |

---

## Open Questions for Implementation

1. **Snapshot storage**: Store all snapshots or generate on demand? Recommendation: Store key moments + quarterly, interpolate others.

2. **Encounter filtering**: When time traveling, should Encounters tab show only encounters up to that age, or all encounters with the current one highlighted?

3. **Message filtering**: Same question for Messages tab.

4. **Growth data**: Include growth charts in time travel? Could show growth trajectory up to selected age.
