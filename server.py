"""
Oread Web Server

FastAPI-based web server for the Oread synthetic patient generator.
"""

# Load environment variables from .env file FIRST
from dotenv import load_dotenv
load_dotenv()

import json
import logging
import os
import sys
import threading
import random
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, EmailStr

# Setup paths
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src import runtime_config
from src.models import GenerationSeed, Sex, ComplexityTier, Patient
from src.engines import PedsEngine
from adult.adult_engine import AdultEngine as AdultEngineImpl, infer_age_from_description
from src.exporters import export_json, export_json_summary, export_markdown, export_fhir, export_ccda, patient_to_context
from src.auth import AuthenticatedUser, require_platform_user, require_user
from src.db.client import new_anon_client, is_configured as db_configured
from src.db.repositories import UserRepository, PanelRepository, PatientRepository
from src.store import BoundedStore

logger = logging.getLogger("oread.server")

RUNTIME = runtime_config.current()


# Create FastAPI app
app = FastAPI(
    title="Oread",
    description="Oread - Synthetic Patient Record Generator API",
    version="0.1.0",
)


@app.on_event("startup")
async def _validate_config():
    """Refuse unsafe mode/bind combinations; warn on missing optional config."""
    runtime_config.check_config(RUNTIME)
    logger.warning("Oread starting in %s mode", RUNTIME.mode)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning(
            "ANTHROPIC_API_KEY not set. LLM-powered narrative generation "
            "will be disabled; patients are generated rule-based only."
        )
    if not RUNTIME.is_platform:
        logger.warning(
            "Standalone mode: no authentication, loopback only. "
            "Accounts and panels are unavailable."
        )


# CORS: explicit origins only. Auth uses bearer tokens, not cookies, so
# credentials are never needed cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=RUNTIME.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Metis-User"],
)

# In-memory, owner-scoped, size-bounded storage (lost on restart; spec D3)
patients_store: BoundedStore[Patient] = BoundedStore(RUNTIME.max_patients)
generation_jobs: BoundedStore[dict] = BoundedStore(RUNTIME.max_jobs)

# Generation is CPU- and LLM-bound. Cap how many run at once so one large
# request can't starve the rest; handlers run in FastAPI's threadpool.
_generation_slots = threading.BoundedSemaphore(RUNTIME.max_concurrent_generations)
_quick_executor = ThreadPoolExecutor(
    max_workers=RUNTIME.max_concurrent_generations, thread_name_prefix="oread-quick"
)


@contextmanager
def _generation_slot(wait_s: Optional[float] = None):
    """Hold one generation slot; 503 if none frees up within the queue timeout."""
    timeout = RUNTIME.generation_queue_timeout_s if wait_s is None else wait_s
    if not _generation_slots.acquire(timeout=timeout):
        raise HTTPException(status_code=503, detail="Generator busy; try again shortly")
    try:
        yield
    finally:
        _generation_slots.release()


def _owned_patient(patient_id: str, user: AuthenticatedUser) -> Patient:
    """Fetch a patient the user owns, or 404 (never reveal others' IDs)."""
    patient = patients_store.get(patient_id, user.id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


# Request/Response models
class GenerateRequest(BaseModel):
    """Request model for patient generation."""
    age: Optional[int] = Field(None, ge=0, le=120, description="Patient age in years")
    age_months: Optional[int] = Field(None, ge=0, le=1440, description="Patient age in months")
    sex: Optional[Sex] = Field(None, description="Patient sex")
    conditions: Optional[list[str]] = Field(None, max_length=20, description="List of conditions to include")
    complexity_tier: Optional[ComplexityTier] = Field(None, description="Complexity tier (tier-0 to tier-5)")
    encounter_count: Optional[int] = Field(None, ge=1, le=500, description="Number of encounters")
    random_seed: Optional[int] = Field(None, description="Random seed for reproducibility")
    description: Optional[str] = Field(None, max_length=2000, description="Natural language description")
    specialty: Optional[Literal["pediatrics", "internal_medicine", "family_practice"]] = Field(
        None, description="Specialty"
    )
    use_llm: bool = Field(True, description="Use LLM for natural narratives (default: True)")
    messiness_level: int = Field(0, ge=0, le=5, description="Chart messiness level (0=pristine to 5=hostile)")


class PatientSummary(BaseModel):
    """Summary of a generated patient."""
    id: str
    name: str
    date_of_birth: str
    age_years: int
    sex: str
    complexity_tier: str
    active_conditions: list[str]
    encounter_count: int
    generated_at: str
    messiness_level: int = 0


class GenerationStatus(BaseModel):
    """Status of a generation job."""
    job_id: str
    status: str  # pending, running, completed, failed
    patient_id: Optional[str] = None
    patient_name: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class GenerateEncounterRequest(BaseModel):
    """Request model for single encounter generation."""
    difficulty_level: int = Field(
        2,
        ge=1,
        le=5,
        description="Difficulty level: 1=Routine, 2=Standard, 3=Complex, 4=Challenging, 5=Zebra"
    )
    visit_type: Optional[str] = Field(
        None,
        description="Visit type override (well-child, acute-illness, chronic-followup)"
    )
    condition: Optional[str] = Field(
        None,
        description="Specific condition to generate (e.g., 'otitis_media')"
    )
    use_llm: bool = Field(
        False,
        description="Use LLM for narrative generation (slower but richer)"
    )


class EncounterSummary(BaseModel):
    """Summary of a generated encounter."""
    id: str
    date: str
    type: str
    chief_complaint: str
    difficulty_level: int
    difficulty_name: str
    assessments: list[str]


def _run_generation_job(job: dict, owner_id: str, age: int, use_llm: bool):
    """Background task to generate a patient. Mutates the stored job dict."""
    with _generation_slots:  # queue behind interactive generations; no timeout
        try:
            job["status"] = "running"
            job["started_at"] = datetime.now().isoformat()

            seed = GenerationSeed(
                age=age,
                random_seed=random.randint(1, 1000000),
            )

            engine = PedsEngine(use_llm=use_llm)
            patient = engine.generate(seed)
            patients_store.put(patient.id, patient, owner_id)

            job["status"] = "completed"
            job["patient_id"] = patient.id
            job["patient_name"] = patient.demographics.full_name
            job["completed_at"] = datetime.now().isoformat()

        except Exception:
            logger.exception("Quick generation job failed")
            job["status"] = "failed"
            job["error"] = "Generation failed"
            job["completed_at"] = datetime.now().isoformat()


# Routes
@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the web UI."""
    ui_path = project_root / "web" / "index.html"
    if ui_path.exists():
        return HTMLResponse(content=ui_path.read_text())
    return HTMLResponse(content="<h1>Oread API</h1><p>Web UI not found. Use /docs for API documentation.</p>")


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.post("/api/generate", response_model=PatientSummary)
def generate_patient(request: GenerateRequest, user: AuthenticatedUser = Depends(require_user)):
    """
    Generate a synthetic patient.

    Returns the patient summary immediately. Use /api/patients/{id} to get full data.
    Sync handler: runs in the threadpool so generation never blocks the event loop.

    Routing: AdultEngine for specialty internal_medicine; family_practice at
    18+; any request aged 22+; and a request with no age whose description
    implies an adult ("a 67-year-old", "elderly", "middle-aged", "adult").
    Everything else goes to PedsEngine.

    use_llm is honored by PedsEngine only. AdultEngine is template-driven and
    ignores it (no LLM calls, no API key needed for adults).

    Adult condition requests are checked before generation: an unknown
    condition, or one impossible for the requested sex/age (PCOS in a male,
    BPH at 25), returns 422.
    """
    # Build generation seed
    seed_params = {}

    if request.age is not None:
        seed_params["age"] = request.age
    if request.age_months is not None:
        seed_params["age_months"] = request.age_months
    if request.sex:
        seed_params["sex"] = request.sex
    if request.conditions:
        seed_params["conditions"] = request.conditions
    if request.complexity_tier:
        seed_params["complexity_tier"] = request.complexity_tier
    if request.encounter_count:
        seed_params["encounter_count"] = request.encounter_count
    if request.random_seed:
        seed_params["random_seed"] = request.random_seed
    if request.description:
        seed_params["description"] = request.description
    if request.messiness_level > 0:
        seed_params["messiness_level"] = request.messiness_level

    gen_seed = GenerationSeed(**seed_params)

    # Route to appropriate engine based on age and specialty
    age_months = request.age_months or (request.age * 12 if request.age else None)
    desc_adult, desc_age = (False, None)
    if age_months is None and request.description:
      desc_adult, desc_age = infer_age_from_description(request.description)
      if desc_age is not None:
        age_months = desc_age * 12
    use_adult = False
    if request.specialty == "internal_medicine":
      use_adult = True
    elif request.specialty == "family_practice" and age_months and age_months >= 216:
      use_adult = True
    elif age_months and age_months >= 264:  # > 22 years, auto-route to adult
      use_adult = True
    elif desc_adult:  # "elderly", "middle-aged", "adult" with no number
      use_adult = True

    if use_adult:
      if request.conditions:
        age_years = request.age if request.age is not None else (
          request.age_months // 12 if request.age_months is not None else None)
        try:
          keys = AdultEngineImpl.resolve_conditions(request.conditions)
          AdultEngineImpl.validate_conditions(keys, request.sex, age_years)
        except ValueError as exc:
          # Our own validation message about the request, not an internal error.
          raise HTTPException(status_code=422, detail=str(exc))
      engine = AdultEngineImpl(use_llm=request.use_llm, messiness_level=request.messiness_level)
    else:
      engine = PedsEngine(use_llm=request.use_llm, messiness_level=request.messiness_level)

    # Generate patient
    with _generation_slot():
        try:
            patient = engine.generate(gen_seed)
        except Exception:
            logger.exception("Patient generation failed")
            raise HTTPException(status_code=500, detail="Patient generation failed")

    # Store patient
    patients_store.put(patient.id, patient, user.id)

    # Return summary
    return PatientSummary(
        id=patient.id,
        name=patient.demographics.full_name,
        date_of_birth=patient.demographics.date_of_birth.isoformat(),
        age_years=patient.demographics.age_years,
        sex=patient.demographics.sex_at_birth.value,
        complexity_tier=patient.complexity_tier.value,
        active_conditions=[c.display_name for c in patient.active_conditions],
        encounter_count=len(patient.encounters),
        generated_at=patient.generated_at.isoformat(),
        messiness_level=request.messiness_level,
    )


@app.post("/api/generate/quick")
async def quick_generate(
    use_llm: bool = Query(True, description="Use LLM for narratives"),
    user: AuthenticatedUser = Depends(require_user),
):
    """
    Start async patient generation. Returns job_id immediately.

    Poll /api/jobs/{job_id} to check status. LLM generation takes ~2 minutes.
    Jobs run on a bounded worker pool, never an unbounded thread per call.
    """
    job_id = uuid4().hex
    age = random.randint(0, 18)

    job = {
        "status": "pending",
        "patient_id": None,
        "patient_name": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
        "use_llm": use_llm,
        "age": age,
    }
    generation_jobs.put(job_id, job, user.id)
    _quick_executor.submit(_run_generation_job, job, user.id, age, use_llm)

    return {"job_id": job_id, "status": "pending", "message": "Generation started. Poll /api/jobs/{job_id} for status."}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str, user: AuthenticatedUser = Depends(require_user)):
    """Check the status of a generation job."""
    job = generation_jobs.get(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return GenerationStatus(
        job_id=job_id,
        status=job["status"],
        patient_id=job.get("patient_id"),
        patient_name=job.get("patient_name"),
        error=job.get("error"),
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
    )


@app.get("/api/patients")
async def list_patients(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_user),
):
    """List the current user's generated patients."""
    patients_list = patients_store.values_for(user.id)
    patients_list.sort(key=lambda p: p.generated_at, reverse=True)
    
    total = len(patients_list)
    patients_page = patients_list[offset:offset + limit]
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "patients": [export_json_summary(p) for p in patients_page],
    }


@app.get("/api/patients/{patient_id}")
def get_patient(
    patient_id: str,
    format: str = Query("json", pattern="^(json|fhir|markdown)$"),
    user: AuthenticatedUser = Depends(require_user),
):
    """
    Get a patient by ID.

    Supports multiple output formats: json, fhir, markdown.
    """
    patient = _owned_patient(patient_id, user)

    if format == "json":
        return JSONResponse(content=json.loads(export_json(patient)))
    elif format == "fhir":
        return JSONResponse(content=export_fhir(patient))
    elif format == "markdown":
        return {"markdown": export_markdown(patient)}


@app.get("/api/patients/{patient_id}/context")
async def get_patient_context(patient_id: str, user: AuthenticatedUser = Depends(require_user)):
    """
    Get patient data in Echo's PatientContext format.

    Returns a flat structure suitable for Echo, Metis, and other services.
    """
    patient = _owned_patient(patient_id, user)
    return JSONResponse(content=patient_to_context(patient))


@app.get("/api/patients/{patient_id}/encounters")
async def get_patient_encounters(patient_id: str, user: AuthenticatedUser = Depends(require_user)):
    """Get all encounters for a patient."""
    patient = _owned_patient(patient_id, user)

    encounters = []
    for enc in sorted(patient.encounters, key=lambda e: e.date, reverse=True):
        encounters.append({
            "id": enc.id,
            "date": enc.date.isoformat(),
            "type": enc.type.value,
            "chief_complaint": enc.chief_complaint,
            "provider": enc.provider.name,
            "location": enc.location.name,
            "has_narrative": enc.narrative_note is not None,
        })
    
    return {"patient_id": patient_id, "encounters": encounters}


@app.get("/api/patients/{patient_id}/encounters/{encounter_id}")
async def get_encounter(
    patient_id: str, encounter_id: str, user: AuthenticatedUser = Depends(require_user)
):
    """Get a specific encounter."""
    patient = _owned_patient(patient_id, user)
    encounter = patient.get_encounter_by_id(encounter_id)
    
    if not encounter:
        raise HTTPException(status_code=404, detail="Encounter not found")
    
    return encounter.model_dump(mode="json")


@app.get("/api/patients/{patient_id}/export/{format}")
def export_patient(patient_id: str, format: str, user: AuthenticatedUser = Depends(require_user)):
    """
    Export a patient to a downloadable file.

    Formats: json, fhir, markdown, ccda
    """
    patient = _owned_patient(patient_id, user)

    # Create temp file
    temp_dir = Path("/tmp/oread")
    temp_dir.mkdir(exist_ok=True)
    
    if format == "json":
        file_path = temp_dir / f"{patient_id}.json"
        export_json(patient, file_path)
        media_type = "application/json"
    elif format == "fhir":
        file_path = temp_dir / f"{patient_id}_fhir.json"
        export_fhir(patient, file_path)
        media_type = "application/fhir+json"
    elif format == "markdown":
        file_path = temp_dir / f"{patient_id}.md"
        export_markdown(patient, file_path)
        media_type = "text/markdown"
    elif format == "ccda":
        file_path = temp_dir / f"{patient_id}_ccda.xml"
        export_ccda(patient, file_path)
        media_type = "application/xml"
    else:
        raise HTTPException(status_code=400, detail="Invalid format. Use: json, fhir, markdown, or ccda")
    
    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=file_path.name,
    )


@app.delete("/api/patients/{patient_id}")
async def delete_patient(patient_id: str, user: AuthenticatedUser = Depends(require_user)):
    """Delete one of the current user's patients from memory."""
    if not patients_store.delete(patient_id, user.id):
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"status": "deleted", "patient_id": patient_id}


@app.get("/api/archetypes")
async def list_archetypes():
    """List available patient archetypes."""
    archetypes_dir = project_root / "archetypes"
    archetypes = {"peds": [], "adult": []}
    
    for category in ["peds", "adult"]:
        cat_dir = archetypes_dir / category
        if cat_dir.exists():
            for arch_file in cat_dir.glob("*.yaml"):
                archetypes[category].append({
                    "name": arch_file.stem,
                    "path": str(arch_file.relative_to(archetypes_dir)),
                })
    
    return archetypes


@app.get("/api/conditions")
async def list_conditions():
    """List available conditions from the knowledge base."""
    conditions_dir = project_root / "knowledge" / "conditions"
    conditions = []
    
    for yaml_file in conditions_dir.rglob("*.yaml"):
        if yaml_file.name.startswith("_"):
            continue
        
        # Parse category from path
        rel_path = yaml_file.relative_to(conditions_dir)
        parts = list(rel_path.parts)
        
        conditions.append({
            "name": yaml_file.stem.replace("_", " ").title(),
            "file": yaml_file.stem,
            "category": parts[1] if len(parts) > 2 else parts[0] if len(parts) > 1 else "general",
            "population": parts[0] if len(parts) > 1 else "general",
        })
    
    return conditions


@app.get("/api/stats")
async def get_stats(user: AuthenticatedUser = Depends(require_user)):
    """Get generation statistics for the current user's patients."""
    patients = patients_store.values_for(user.id)
    
    if not patients:
        return {
            "total_patients": 0,
            "tier_distribution": {},
            "age_distribution": {},
            "sex_distribution": {},
        }
    
    tier_dist = {}
    age_dist = {"0-2": 0, "2-5": 0, "5-12": 0, "12-18": 0, "18+": 0}
    sex_dist = {"male": 0, "female": 0}
    
    for p in patients:
        # Tier
        tier = p.complexity_tier.value
        tier_dist[tier] = tier_dist.get(tier, 0) + 1
        
        # Age
        age = p.demographics.age_years
        if age < 2:
            age_dist["0-2"] += 1
        elif age < 5:
            age_dist["2-5"] += 1
        elif age < 12:
            age_dist["5-12"] += 1
        elif age < 18:
            age_dist["12-18"] += 1
        else:
            age_dist["18+"] += 1
        
        # Sex
        sex = p.demographics.sex_at_birth.value
        sex_dist[sex] = sex_dist.get(sex, 0) + 1
    
    return {
        "total_patients": len(patients),
        "tier_distribution": tier_dist,
        "age_distribution": age_dist,
        "sex_distribution": sex_dist,
    }


# =============================================================================
# AUTH ENDPOINTS
# =============================================================================

class SignUpRequest(BaseModel):
    """Request model for user signup."""
    email: EmailStr
    password: str = Field(..., min_length=8)
    display_name: Optional[str] = None
    learner_level: Optional[str] = None
    institution: Optional[str] = None


class LoginRequest(BaseModel):
    """Request model for user login."""
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    """Response model for auth endpoints."""
    access_token: str
    refresh_token: str
    user: dict


class UserProfile(BaseModel):
    """Response model for user profile."""
    id: str
    email: str
    display_name: Optional[str] = None
    role: str = "learner"
    learner_level: Optional[str] = None
    institution: Optional[str] = None


@app.post("/api/auth/signup", response_model=AuthResponse)
def signup(request: SignUpRequest):
    """
    Create a new user account.

    Returns access token and user profile on success.
    """
    _require_platform()

    try:
        # Fresh client: never attach a user session to the shared client
        client = new_anon_client()

        # Sign up with Supabase Auth
        auth_response = client.sign_up(request.email, request.password)

        if not auth_response.user:
            raise HTTPException(status_code=400, detail="Signup failed")
        if not auth_response.session:
            # Email confirmation is on: the account exists but has no session yet
            raise HTTPException(status_code=409, detail="Check your email to confirm your account, then log in")

        # Create user profile in our table (use admin client to bypass RLS)
        user_repo = UserRepository(use_admin=True)
        profile = user_repo.create(
            user_id=auth_response.user.id,
            email=request.email,
            display_name=request.display_name,
            learner_level=request.learner_level,
            institution=request.institution,
        )

        return AuthResponse(
            access_token=auth_response.session.access_token,
            refresh_token=auth_response.session.refresh_token,
            user={
                "id": str(auth_response.user.id),
                "email": request.email,
                "display_name": request.display_name,
                "role": "learner",
                "learner_level": request.learner_level,
            }
        )

    except HTTPException:
        raise
    except Exception:
        logger.info("Signup failed", exc_info=True)
        raise HTTPException(status_code=400, detail="Signup failed")


def _require_platform() -> None:
    """Accounts and panels need Supabase; standalone answers 501."""
    if not RUNTIME.is_platform or not db_configured():
        raise HTTPException(
            status_code=501,
            detail="Requires platform mode (Supabase). This Oread runs standalone.",
        )


@app.post("/api/auth/login", response_model=AuthResponse)
def login(request: LoginRequest):
    """
    Log in an existing user.

    Returns access token and user profile on success.
    """
    _require_platform()

    try:
        # Fresh client: signing in on the shared client would make every
        # later query run as this user
        client = new_anon_client()

        # Sign in with Supabase Auth
        auth_response = client.sign_in(request.email, request.password)

        if not auth_response.user or not auth_response.session:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Get user profile (the fresh client now carries this user's session)
        user_repo = UserRepository(client=client)
        profile = user_repo.get_by_id(auth_response.user.id)

        return AuthResponse(
            access_token=auth_response.session.access_token,
            refresh_token=auth_response.session.refresh_token,
            user={
                "id": str(auth_response.user.id),
                "email": request.email,
                "display_name": profile.get("display_name") if profile else None,
                "role": profile.get("role", "learner") if profile else "learner",
                "learner_level": profile.get("learner_level") if profile else None,
            }
        )

    except HTTPException:
        raise
    except Exception:
        logger.info("Login failed", exc_info=True)
        raise HTTPException(status_code=401, detail="Invalid credentials")


@app.post("/api/auth/logout")
async def logout(user: AuthenticatedUser = Depends(require_platform_user)):
    """
    Log out the current user.

    Tokens are stateless JWTs held by the client; logging out means the client
    discards them. Nothing server-side is shared, so this affects only the caller.
    """
    return {"status": "logged_out"}


@app.get("/api/auth/me", response_model=UserProfile)
def get_me(user: AuthenticatedUser = Depends(require_platform_user)):
    """Get the current user's profile."""
    user_repo = UserRepository(client=user.db())
    profile = user_repo.get_by_id(user.id)

    return UserProfile(
        id=user.id,
        email=user.email,
        display_name=profile.get("display_name") if profile else None,
        role=profile.get("role", "learner") if profile else "learner",
        learner_level=profile.get("learner_level") if profile else None,
        institution=profile.get("institution") if profile else None,
    )


@app.patch("/api/auth/me")
def update_me(
    display_name: Optional[str] = None,
    learner_level: Optional[str] = None,
    institution: Optional[str] = None,
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """Update the current user's profile."""
    user_repo = UserRepository(client=user.db())

    updates = {}
    if display_name is not None:
        updates["display_name"] = display_name
    if learner_level is not None:
        updates["learner_level"] = learner_level
    if institution is not None:
        updates["institution"] = institution

    if updates:
        user_repo.update(user.id, **updates)

    return {"status": "updated"}


# =============================================================================
# PANEL ENDPOINTS
# =============================================================================

class CreatePanelRequest(BaseModel):
    """Request model for creating a panel."""
    name: str
    description: Optional[str] = None
    config: Optional[dict] = None


class GeneratePanelPatientsRequest(BaseModel):
    """Request model for generating patients in a panel."""
    count: int = Field(20, ge=1, le=50, description="Number of patients to generate")
    description: Optional[str] = Field(
        None,
        description="Natural language description for patient generation (e.g., 'toddlers with asthma and eczema')"
    )
    conditions: Optional[list[str]] = Field(None, description="Specific conditions to include")
    min_age_months: Optional[int] = Field(None, ge=0, le=216, description="Minimum age in months")
    max_age_months: Optional[int] = Field(None, ge=0, le=216, description="Maximum age in months (max 18 years)")
    complexity_tier: Optional[ComplexityTier] = Field(None, description="Complexity tier (tier-0 to tier-5)")
    use_llm: bool = Field(True, description="Use LLM for rich narrative content (default: True)")


class PanelResponse(BaseModel):
    """Response model for a panel."""
    id: str
    name: str
    description: Optional[str] = None
    patient_count: int = 0
    config: dict = {}
    created_at: str


@app.get("/api/panels")
def list_panels(user: AuthenticatedUser = Depends(require_platform_user)):
    """List all panels for the current user."""
    panel_repo = PanelRepository(client=user.db())
    panels = panel_repo.get_by_owner(user.id)

    return {
        "panels": [
            {
                "id": p["id"],
                "name": p["name"],
                "description": p.get("description"),
                "patient_count": p.get("patient_count", 0),
                "created_at": p.get("created_at"),
            }
            for p in panels
        ]
    }


@app.post("/api/panels", response_model=PanelResponse)
def create_panel(
    request: CreatePanelRequest,
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """Create a new patient panel."""
    panel_repo = PanelRepository(client=user.db())

    panel = panel_repo.create(
        owner_id=user.id,
        name=request.name,
        config=request.config or {},
    )

    if not panel:
        raise HTTPException(status_code=500, detail="Failed to create panel")

    return PanelResponse(
        id=panel["id"],
        name=panel["name"],
        description=panel.get("description"),
        patient_count=0,
        config=panel.get("config", {}),
        created_at=panel.get("created_at", datetime.now().isoformat()),
    )


@app.get("/api/panels/{panel_id}")
def get_panel(
    panel_id: str,
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """Get a panel with its patients."""
    panel_repo = PanelRepository(client=user.db())
    patient_repo = PatientRepository(client=user.db())

    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    # Verify ownership
    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get patients
    patients = patient_repo.get_by_panel(panel_id)

    return {
        "panel": {
            "id": panel["id"],
            "name": panel["name"],
            "description": panel.get("description"),
            "patient_count": panel.get("patient_count", 0),
            "config": panel.get("config", {}),
            "created_at": panel.get("created_at"),
        },
        "patients": [
            {
                "id": p["id"],
                "name": p["demographics"].get("full_name", "Unknown"),
                "age_months": p.get("age_months"),
                "complexity_tier": p.get("complexity_tier"),
                "conditions": p.get("conditions", []),
            }
            for p in patients
        ]
    }


@app.delete("/api/panels/{panel_id}")
def delete_panel(
    panel_id: str,
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """Delete a panel and all its patients."""
    panel_repo = PanelRepository(client=user.db())

    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    panel_repo.delete(panel_id)
    return {"status": "deleted", "panel_id": panel_id}


@app.post("/api/panels/{panel_id}/generate")
def generate_panel_patients(
    panel_id: str,
    request: GeneratePanelPatientsRequest,
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """
    Generate patients for a panel with optional natural language configuration.

    Examples:
    - {"count": 10} - Generate 10 random patients with LLM
    - {"count": 5, "description": "toddlers with asthma"} - 5 toddlers with asthma
    - {"count": 15, "conditions": ["eczema", "food allergy"], "max_age_months": 36}
    - {"count": 20, "description": "complex adolescents with ADHD and anxiety"}
    """
    panel_repo = PanelRepository(client=user.db())
    patient_repo = PatientRepository(client=user.db())

    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get panel config as defaults, request params override
    config = panel.get("config", {})
    min_age = request.min_age_months if request.min_age_months is not None else config.get("min_age_months", 0)
    max_age = request.max_age_months if request.max_age_months is not None else config.get("max_age_months", 216)
    # Checked after merging with stored panel config, which the request model can't see
    if not (0 <= min_age <= max_age <= 216):
        raise HTTPException(
            status_code=400,
            detail=f"Need 0 <= min_age_months <= max_age_months <= 216; got {min_age}..{max_age}",
        )

    # Generate patients with LLM by default
    engine = PedsEngine(use_llm=request.use_llm)
    generated = []

    # One slot for the whole batch: a 50-patient panel runs in this worker
    # thread without blocking the event loop or other users' slots beyond one.
    with _generation_slot():
        for i in range(request.count):
            age_months = random.randint(min_age, max_age)

            # Build generation seed
            seed_params = {"age_months": age_months}

            if request.description:
                seed_params["description"] = request.description
            if request.conditions:
                seed_params["conditions"] = request.conditions
            if request.complexity_tier:
                seed_params["complexity_tier"] = request.complexity_tier

            seed = GenerationSeed(**seed_params)

            try:
                patient = engine.generate(seed)

                # Save to database
                db_patient = patient_repo.create(
                    panel_id=panel_id,
                    demographics=patient.demographics.model_dump(mode="json"),
                    full_record=json.loads(export_json(patient)),
                    complexity_tier=patient.complexity_tier.value,
                    conditions=[c.display_name for c in patient.active_conditions],
                    age_months=age_months,
                )

                generated.append({
                    "id": db_patient["id"],
                    "name": patient.demographics.full_name,
                    "age_months": age_months,
                    "conditions": [c.display_name for c in patient.active_conditions],
                })

                # Log progress for longer batches
                if (i + 1) % 5 == 0:
                    print(f"Generated {i + 1}/{request.count} patients for panel {panel_id}")

            except Exception:
                # Log error but continue
                logger.exception("Failed to generate panel patient %d", i + 1)

    return {
        "panel_id": panel_id,
        "generated_count": len(generated),
        "patients": generated,
    }


# =============================================================================
# PANEL PATIENT DATA ENDPOINTS
# =============================================================================

@app.get("/api/panels/{panel_id}/patients/{patient_id}")
def get_panel_patient(
    panel_id: str,
    patient_id: str,
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """
    Get full patient data from a panel.

    Returns the complete patient record including demographics, encounters,
    problem list, medications, immunizations, and messages.
    """
    panel_repo = PanelRepository(client=user.db())
    patient_repo = PatientRepository(client=user.db())

    # Verify panel access
    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get patient from database
    db_patient = patient_repo.get_by_id(patient_id)
    if not db_patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if db_patient["panel_id"] != panel_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to this panel")

    # Return the full patient record
    full_record = db_patient.get("full_record", {})

    # Add the patient ID to the record
    full_record["id"] = patient_id

    return full_record


# =============================================================================
# SINGLE CASE GENERATION ENDPOINTS
# =============================================================================

@app.post("/api/panels/{panel_id}/patients/{patient_id}/encounters", response_model=EncounterSummary)
def generate_encounter(
    panel_id: str,
    patient_id: str,
    request: GenerateEncounterRequest,
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """
    Generate a new encounter for an existing patient.

    This is the "Single Case" feature for the learning platform.

    Difficulty levels:
    - 1: Routine (well-child, normal development)
    - 2: Standard (common illness, classic presentation)
    - 3: Complex (multiple factors, decisions needed)
    - 4: Challenging (atypical presentation, competing diagnoses)
    - 5: Zebra (rare or unexpected diagnosis)
    """
    from src.models import EncounterType

    panel_repo = PanelRepository(client=user.db())
    patient_repo = PatientRepository(client=user.db())

    # Verify panel access
    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get patient from database
    db_patient = patient_repo.get_by_id(patient_id)
    if not db_patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if db_patient["panel_id"] != panel_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to this panel")

    # Reconstruct Patient object from stored record
    try:
        patient = Patient.from_stored(db_patient["full_record"])
    except Exception:
        logger.exception("Stored patient %s failed to load", patient_id)
        raise HTTPException(status_code=500, detail="Failed to load patient record")

    # Parse visit type if provided
    visit_type = None
    if request.visit_type:
        try:
            visit_type = EncounterType(request.visit_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid visit type: {request.visit_type}. Valid types: well-child, acute-illness, chronic-followup"
            )

    # Generate encounter
    engine = PedsEngine(use_llm=request.use_llm)
    difficulty_configs = engine.DIFFICULTY_CONFIGS

    try:
        encounter = engine.generate_encounter_for_patient(
            patient=patient,
            difficulty_level=request.difficulty_level,
            visit_type=visit_type,
            condition=request.condition,
        )
    except Exception:
        logger.exception("Encounter generation failed")
        raise HTTPException(status_code=500, detail="Failed to generate encounter")

    # Add encounter to patient record
    patient.encounters.append(encounter)

    # Update patient in database (use admin client to bypass RLS)
    try:
        admin_patient_repo = PatientRepository(use_admin=True)
        result = admin_patient_repo.update(
            patient_id=patient_id,
            full_record=json.loads(export_json(patient)),
        )
        if not result:
            print(f"Warning: Patient update returned None for {patient_id}")
    except Exception:
        # Log but don't fail - encounter was generated successfully
        logger.exception("Failed to persist encounter to database")

    # Build response
    difficulty_config = difficulty_configs.get(request.difficulty_level, {})
    return EncounterSummary(
        id=encounter.id,
        date=encounter.date.isoformat(),
        type=encounter.type.value,
        chief_complaint=encounter.chief_complaint,
        difficulty_level=request.difficulty_level,
        difficulty_name=difficulty_config.get("name", "Unknown"),
        assessments=[a.diagnosis for a in encounter.assessment],
    )


@app.get("/api/panels/{panel_id}/patients/{patient_id}/encounters")
def list_patient_encounters(
    panel_id: str,
    patient_id: str,
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """List all encounters for a patient."""
    panel_repo = PanelRepository(client=user.db())
    patient_repo = PatientRepository(client=user.db())

    # Verify panel access
    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get patient
    db_patient = patient_repo.get_by_id(patient_id)
    if not db_patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if db_patient["panel_id"] != panel_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to this panel")

    # Extract encounters from full record
    full_record = db_patient.get("full_record", {})
    encounters = full_record.get("encounters", [])

    return {
        "patient_id": patient_id,
        "encounter_count": len(encounters),
        "encounters": [
            {
                "id": enc.get("id"),
                "date": enc.get("date"),
                "type": enc.get("type"),
                "chief_complaint": enc.get("chief_complaint"),
            }
            for enc in encounters
        ]
    }


@app.get("/api/panels/{panel_id}/patients/{patient_id}/encounters/{encounter_id}")
def get_panel_patient_encounter(
    panel_id: str,
    patient_id: str,
    encounter_id: str,
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """Get a specific encounter by ID."""
    panel_repo = PanelRepository(client=user.db())
    patient_repo = PatientRepository(client=user.db())

    # Verify panel access
    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get patient
    db_patient = patient_repo.get_by_id(patient_id)
    if not db_patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if db_patient["panel_id"] != panel_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to this panel")

    # Find encounter
    full_record = db_patient.get("full_record", {})
    encounters = full_record.get("encounters", [])

    for enc in encounters:
        if enc.get("id") == encounter_id:
            return enc

    raise HTTPException(status_code=404, detail="Encounter not found")


# =============================================================================
# TIME TRAVEL ENDPOINTS
# =============================================================================


def _snapshot_to_dict(s) -> Optional[dict]:
    """Serialize a TimeSnapshot (shared by both timeline endpoints)."""
    if not s:
        return None
    return {
        "age_months": s.age_months,
        "date": s.date.isoformat(),
        "active_conditions": [
            {"display_name": c.display_name, "code": c.code.code if c.code else None}
            for c in s.active_conditions
        ],
        "medications": [
            {"name": m.display_name, "dosage": f"{m.dose_quantity} {m.dose_unit}"}
            for m in s.medications
        ],
        "new_conditions": s.new_conditions,
        "resolved_conditions": s.resolved_conditions,
        "medication_changes": [
            {"type": mc.type.value, "medication": mc.medication}
            for mc in s.medication_changes
        ],
        "is_key_moment": s.is_key_moment,
        "event_description": s.event_description,
        "growth": {
            "weight_kg": s.growth.weight_kg,
            "height_cm": s.growth.height_cm,
            "bmi": s.growth.bmi,
        } if s.growth else None,
    }


class TimelineRequest(BaseModel):
    """Request model for timeline generation."""
    arc_names: list[str] | None = None
    snapshot_interval_months: int = 6


@app.get("/api/panels/{panel_id}/patients/{patient_id}/timeline")
def get_patient_timeline(
    panel_id: str,
    patient_id: str,
    arc_names: str | None = None,
    interval: int = Query(6, ge=1, le=60),
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """
    Get the patient's timeline showing disease progression over time.

    This is the core Time Travel API - it returns snapshots at different ages
    showing how conditions, medications, and clinical state evolved.
    """
    panel_repo = PanelRepository(client=user.db())
    patient_repo = PatientRepository(client=user.db())

    # Verify panel access
    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get patient
    db_patient = patient_repo.get_by_id(patient_id)
    if not db_patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if db_patient["panel_id"] != panel_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to this panel")

    # Reconstruct Patient object
    full_record = db_patient.get("full_record", {})
    try:
        from src.models import Patient
        patient = Patient.from_stored(full_record)
    except Exception:
        logger.exception("Stored patient %s failed to load", patient_id)
        raise HTTPException(status_code=500, detail="Error loading patient")

    # Parse arc_names from query param
    arc_list = arc_names.split(",") if arc_names else None

    # Generate timeline
    from src.engines import PedsEngine
    engine = PedsEngine(use_llm=False)
    snapshots, disease_arcs = engine.generate_timeline(
        patient=patient,
        arc_names=arc_list,
        snapshot_interval_months=interval,
    )

    # Convert to serializable format
    return {
        "patient_id": patient_id,
        "current_age_months": patient.demographics.age_months,
        "snapshots": [_snapshot_to_dict(s) for s in snapshots],
        "disease_arcs": [
            {
                "id": arc.id,
                "name": arc.name,
                "description": arc.description,
                "current_stage_index": arc.current_stage_index,
                "stages": [
                    {
                        "condition_key": stage.condition_key,
                        "display_name": stage.display_name,
                        "typical_age_range": list(stage.typical_age_range),
                        "actual_onset_age": stage.actual_onset_age,
                        "status": stage.status.value,
                        "symptoms": stage.symptoms,
                        "treatments": stage.treatments,
                    }
                    for stage in arc.stages
                ],
                "clinical_pearls": arc.clinical_pearls,
            }
            for arc in disease_arcs
        ],
    }


@app.get("/api/panels/{panel_id}/patients/{patient_id}/timeline/at/{age_months}")
def get_snapshot_at_age(
    panel_id: str,
    patient_id: str,
    age_months: int,
    arc_names: str | None = None,
    user: AuthenticatedUser = Depends(require_platform_user)
):
    """
    Get the patient's clinical state at a specific age.

    Returns the snapshot at that age plus the previous snapshot for comparison.
    """
    panel_repo = PanelRepository(client=user.db())
    patient_repo = PatientRepository(client=user.db())

    # Verify panel access
    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get patient
    db_patient = patient_repo.get_by_id(patient_id)
    if not db_patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if db_patient["panel_id"] != panel_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to this panel")

    # Reconstruct Patient object
    full_record = db_patient.get("full_record", {})
    try:
        from src.models import Patient
        patient = Patient.from_stored(full_record)
    except Exception:
        logger.exception("Stored patient %s failed to load", patient_id)
        raise HTTPException(status_code=500, detail="Error loading patient")

    # Validate age
    if age_months < 0 or age_months > patient.demographics.age_months:
        raise HTTPException(
            status_code=400,
            detail=f"Age must be between 0 and {patient.demographics.age_months} months"
        )

    # Parse arc_names from query param
    arc_list = arc_names.split(",") if arc_names else None

    # Get snapshot
    from src.engines import PedsEngine
    engine = PedsEngine(use_llm=False)
    snapshot, prev_snapshot = engine.get_snapshot_at_age(
        patient=patient,
        age_months=age_months,
        arc_names=arc_list,
    )

    return {
        "age_months": age_months,
        "snapshot": _snapshot_to_dict(snapshot),
        "previous_snapshot": _snapshot_to_dict(prev_snapshot),
        "changes": {
            "new_conditions": snapshot.new_conditions,
            "resolved_conditions": snapshot.resolved_conditions,
            "medication_changes": [
                {"type": mc.type.value, "medication": mc.medication}
                for mc in snapshot.medication_changes
            ],
        }
    }


# Mount static files for web UI
web_dir = project_root / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")


def run_server(host: Optional[str] = None, port: Optional[int] = None):
    """Run the server, refusing to expose a standalone (no-auth) server."""
    import uvicorn
    bind = host or RUNTIME.host
    runtime_config.check_config(RUNTIME, host=bind)
    uvicorn.run(app, host=bind, port=port or RUNTIME.port)


if __name__ == "__main__":
    run_server()
