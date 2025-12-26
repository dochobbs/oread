-- Oread Learning Platform Database Schema
-- Run this in Supabase SQL Editor to set up the database

-- ============================================================================
-- EXTENSIONS
-- ============================================================================

-- UUID generation (usually enabled by default in Supabase)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- ENUMS
-- ============================================================================

-- Learner levels from NP student to attending
CREATE TYPE learner_level AS ENUM (
  'np_student',
  'ms3',
  'ms4',
  'intern',
  'pgy2',
  'pgy3',
  'fellow',
  'attending'
);

-- User roles
CREATE TYPE user_role AS ENUM (
  'learner',
  'instructor',
  'admin'
);

-- Feedback status
CREATE TYPE feedback_status AS ENUM (
  'new',
  'reviewed',
  'resolved',
  'dismissed'
);

-- ============================================================================
-- TABLES
-- ============================================================================

-- Users table (extends Supabase auth.users)
CREATE TABLE public.users (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT UNIQUE NOT NULL,
  display_name TEXT,
  role user_role NOT NULL DEFAULT 'learner',
  learner_level learner_level,
  institution TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Patient panels (collections of patients for a learner)
CREATE TABLE public.panels (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT NOT NULL,
  description TEXT,
  owner_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  config JSONB NOT NULL DEFAULT '{}',
  patient_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Synthetic patients (persistent, reusable)
CREATE TABLE public.patients (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  panel_id UUID REFERENCES public.panels(id) ON DELETE CASCADE,
  demographics JSONB NOT NULL,
  full_record JSONB NOT NULL,
  complexity_tier TEXT,
  conditions TEXT[] DEFAULT '{}',
  age_months INT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Encounters (generated visits for patients)
CREATE TABLE public.encounters (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  patient_id UUID NOT NULL REFERENCES public.patients(id) ON DELETE CASCADE,
  encounter_type TEXT NOT NULL,
  encounter_json JSONB NOT NULL,
  difficulty_level INT CHECK (difficulty_level BETWEEN 1 AND 5),
  competencies TEXT[] DEFAULT '{}',
  generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Learning sessions (learner interacting with an encounter)
CREATE TABLE public.sessions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  encounter_id UUID NOT NULL REFERENCES public.encounters(id) ON DELETE CASCADE,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  -- Learner's work
  learner_notes TEXT,
  learner_hpi TEXT,
  learner_assessment TEXT,
  learner_plan TEXT,
  learner_billing JSONB,
  -- AI feedback
  echo_transcript JSONB,
  documentation_score JSONB,
  billing_score JSONB,
  -- Overall
  score JSONB
);

-- Spaced repetition reviews
CREATE TABLE public.reviews (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  encounter_id UUID NOT NULL REFERENCES public.encounters(id) ON DELETE CASCADE,
  -- SM-2 algorithm fields
  next_review TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ease_factor FLOAT NOT NULL DEFAULT 2.5,
  interval_days INT NOT NULL DEFAULT 1,
  repetitions INT NOT NULL DEFAULT 0,
  -- Stats
  correct_count INT NOT NULL DEFAULT 0,
  incorrect_count INT NOT NULL DEFAULT 0,
  last_reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- One review per user per encounter
  UNIQUE(user_id, encounter_id)
);

-- Competency progress tracking
CREATE TABLE public.competency_progress (
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  competency_code TEXT NOT NULL,
  cases_seen INT NOT NULL DEFAULT 0,
  total_score FLOAT NOT NULL DEFAULT 0,
  avg_score FLOAT GENERATED ALWAYS AS (
    CASE WHEN cases_seen > 0 THEN total_score / cases_seen ELSE 0 END
  ) STORED,
  last_seen_at TIMESTAMPTZ,
  PRIMARY KEY (user_id, competency_code)
);

-- User feedback on content
CREATE TABLE public.feedback (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  encounter_id UUID REFERENCES public.encounters(id) ON DELETE SET NULL,
  patient_id UUID REFERENCES public.patients(id) ON DELETE SET NULL,
  feedback_type TEXT NOT NULL,
  content TEXT NOT NULL,
  status feedback_status NOT NULL DEFAULT 'new',
  admin_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at TIMESTAMPTZ
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Users
CREATE INDEX idx_users_email ON public.users(email);
CREATE INDEX idx_users_role ON public.users(role);

-- Panels
CREATE INDEX idx_panels_owner ON public.panels(owner_id);

-- Patients
CREATE INDEX idx_patients_panel ON public.patients(panel_id);
CREATE INDEX idx_patients_complexity ON public.patients(complexity_tier);

-- Encounters
CREATE INDEX idx_encounters_patient ON public.encounters(patient_id);
CREATE INDEX idx_encounters_type ON public.encounters(encounter_type);
CREATE INDEX idx_encounters_difficulty ON public.encounters(difficulty_level);

-- Sessions
CREATE INDEX idx_sessions_user ON public.sessions(user_id);
CREATE INDEX idx_sessions_encounter ON public.sessions(encounter_id);
CREATE INDEX idx_sessions_completed ON public.sessions(completed_at);

-- Reviews
CREATE INDEX idx_reviews_user ON public.reviews(user_id);
CREATE INDEX idx_reviews_next ON public.reviews(next_review);
CREATE INDEX idx_reviews_user_next ON public.reviews(user_id, next_review);

-- Feedback
CREATE INDEX idx_feedback_status ON public.feedback(status);
CREATE INDEX idx_feedback_user ON public.feedback(user_id);

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER users_updated_at
  BEFORE UPDATE ON public.users
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER panels_updated_at
  BEFORE UPDATE ON public.panels
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Update panel patient count
CREATE OR REPLACE FUNCTION update_panel_patient_count()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    UPDATE public.panels SET patient_count = patient_count + 1 WHERE id = NEW.panel_id;
  ELSIF TG_OP = 'DELETE' THEN
    UPDATE public.panels SET patient_count = patient_count - 1 WHERE id = OLD.panel_id;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER patients_count_trigger
  AFTER INSERT OR DELETE ON public.patients
  FOR EACH ROW EXECUTE FUNCTION update_panel_patient_count();

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

-- Enable RLS on all tables
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.panels ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.patients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.encounters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.competency_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.feedback ENABLE ROW LEVEL SECURITY;

-- Users: can read own profile, admins can read all
CREATE POLICY users_select ON public.users
  FOR SELECT USING (
    auth.uid() = id OR
    EXISTS (SELECT 1 FROM public.users WHERE id = auth.uid() AND role = 'admin')
  );

CREATE POLICY users_update ON public.users
  FOR UPDATE USING (auth.uid() = id);

-- Panels: owners can CRUD, others can read if shared (future)
CREATE POLICY panels_select ON public.panels
  FOR SELECT USING (owner_id = auth.uid());

CREATE POLICY panels_insert ON public.panels
  FOR INSERT WITH CHECK (owner_id = auth.uid());

CREATE POLICY panels_update ON public.panels
  FOR UPDATE USING (owner_id = auth.uid());

CREATE POLICY panels_delete ON public.panels
  FOR DELETE USING (owner_id = auth.uid());

-- Patients: access through panel ownership
CREATE POLICY patients_select ON public.patients
  FOR SELECT USING (
    EXISTS (SELECT 1 FROM public.panels WHERE id = panel_id AND owner_id = auth.uid())
  );

CREATE POLICY patients_insert ON public.patients
  FOR INSERT WITH CHECK (
    EXISTS (SELECT 1 FROM public.panels WHERE id = panel_id AND owner_id = auth.uid())
  );

CREATE POLICY patients_delete ON public.patients
  FOR DELETE USING (
    EXISTS (SELECT 1 FROM public.panels WHERE id = panel_id AND owner_id = auth.uid())
  );

-- Encounters: access through patient's panel
CREATE POLICY encounters_select ON public.encounters
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM public.patients p
      JOIN public.panels pl ON p.panel_id = pl.id
      WHERE p.id = patient_id AND pl.owner_id = auth.uid()
    )
  );

CREATE POLICY encounters_insert ON public.encounters
  FOR INSERT WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.patients p
      JOIN public.panels pl ON p.panel_id = pl.id
      WHERE p.id = patient_id AND pl.owner_id = auth.uid()
    )
  );

-- Sessions: users can only access their own
CREATE POLICY sessions_select ON public.sessions
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY sessions_insert ON public.sessions
  FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY sessions_update ON public.sessions
  FOR UPDATE USING (user_id = auth.uid());

-- Reviews: users can only access their own
CREATE POLICY reviews_select ON public.reviews
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY reviews_insert ON public.reviews
  FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY reviews_update ON public.reviews
  FOR UPDATE USING (user_id = auth.uid());

-- Competency progress: users can only access their own
CREATE POLICY competency_select ON public.competency_progress
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY competency_insert ON public.competency_progress
  FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY competency_update ON public.competency_progress
  FOR UPDATE USING (user_id = auth.uid());

-- Feedback: users can create and view their own, admins can view all
CREATE POLICY feedback_select ON public.feedback
  FOR SELECT USING (
    user_id = auth.uid() OR
    EXISTS (SELECT 1 FROM public.users WHERE id = auth.uid() AND role = 'admin')
  );

CREATE POLICY feedback_insert ON public.feedback
  FOR INSERT WITH CHECK (user_id = auth.uid());

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Get reviews due for a user
CREATE OR REPLACE FUNCTION get_due_reviews(p_user_id UUID, p_limit INT DEFAULT 10)
RETURNS TABLE (
  review_id UUID,
  encounter_id UUID,
  patient_id UUID,
  encounter_type TEXT,
  days_overdue INT
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    r.id AS review_id,
    r.encounter_id,
    e.patient_id,
    e.encounter_type,
    EXTRACT(DAY FROM NOW() - r.next_review)::INT AS days_overdue
  FROM public.reviews r
  JOIN public.encounters e ON r.encounter_id = e.id
  WHERE r.user_id = p_user_id
    AND r.next_review <= NOW()
  ORDER BY r.next_review ASC
  LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get competency gaps for a user
CREATE OR REPLACE FUNCTION get_competency_gaps(p_user_id UUID, p_min_cases INT DEFAULT 3)
RETURNS TABLE (
  competency_code TEXT,
  cases_seen INT,
  avg_score FLOAT,
  needs_attention BOOLEAN
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    cp.competency_code,
    cp.cases_seen,
    cp.avg_score,
    (cp.cases_seen < p_min_cases OR cp.avg_score < 70) AS needs_attention
  FROM public.competency_progress cp
  WHERE cp.user_id = p_user_id
  ORDER BY cp.cases_seen ASC, cp.avg_score ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- INITIAL DATA (Optional)
-- ============================================================================

-- You can add initial data here if needed, such as:
-- - Default competency codes
-- - Sample panels for testing

-- Example: Insert competency codes (uncomment if needed)
-- INSERT INTO ...
