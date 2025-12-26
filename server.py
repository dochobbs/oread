"""
Oread Web Server

FastAPI-based web server for the Oread synthetic patient generator.
"""

import json
import sys
import threading
import random
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, EmailStr

# Setup paths
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.models import GenerationSeed, Sex, ComplexityTier, Patient
from src.engines import PedsEngine
from src.exporters import export_json, export_json_summary, export_markdown, export_fhir, export_ccda
from src.auth import get_current_user, get_current_user_optional, AuthenticatedUser
from src.db.client import get_client, get_admin_client, is_configured as db_configured
from src.db.repositories import UserRepository, PanelRepository, PatientRepository


# Create FastAPI app
app = FastAPI(
    title="Oread",
    description="Oread - Synthetic Patient Record Generator API",
    version="0.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize engine
peds_engine = PedsEngine()

# In-memory storage for generated patients (for demo purposes)
patients_store: dict[str, Patient] = {}
generation_jobs: dict[str, dict] = {}


# Request/Response models
class GenerateRequest(BaseModel):
    """Request model for patient generation."""
    age: Optional[int] = Field(None, ge=0, le=120, description="Patient age in years")
    age_months: Optional[int] = Field(None, ge=0, description="Patient age in months")
    sex: Optional[str] = Field(None, description="Patient sex (male/female)")
    conditions: Optional[list[str]] = Field(None, description="List of conditions to include")
    complexity_tier: Optional[str] = Field(None, description="Complexity tier (tier-0 to tier-3)")
    encounter_count: Optional[int] = Field(None, ge=1, description="Number of encounters")
    random_seed: Optional[int] = Field(None, description="Random seed for reproducibility")
    description: Optional[str] = Field(None, description="Natural language description")
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


def _run_generation_job(job_id: str, age: int, use_llm: bool):
    """Background task to generate a patient."""
    try:
        generation_jobs[job_id]["status"] = "running"
        generation_jobs[job_id]["started_at"] = datetime.now().isoformat()

        seed = GenerationSeed(
            age=age,
            random_seed=random.randint(1, 1000000),
        )

        engine = PedsEngine(use_llm=use_llm)
        patient = engine.generate(seed)
        patients_store[patient.id] = patient

        generation_jobs[job_id]["status"] = "completed"
        generation_jobs[job_id]["patient_id"] = patient.id
        generation_jobs[job_id]["patient_name"] = patient.demographics.full_name
        generation_jobs[job_id]["completed_at"] = datetime.now().isoformat()

    except Exception as e:
        generation_jobs[job_id]["status"] = "failed"
        generation_jobs[job_id]["error"] = str(e)
        generation_jobs[job_id]["completed_at"] = datetime.now().isoformat()


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
async def generate_patient(request: GenerateRequest):
    """
    Generate a synthetic patient.

    Returns the patient summary immediately. Use /api/patients/{id} to get full data.
    """
    # Build generation seed
    seed_params = {}

    if request.age is not None:
        seed_params["age"] = request.age
    if request.age_months is not None:
        seed_params["age_months"] = request.age_months
    if request.sex:
        seed_params["sex"] = Sex(request.sex)
    if request.conditions:
        seed_params["conditions"] = request.conditions
    if request.complexity_tier:
        seed_params["complexity_tier"] = ComplexityTier(request.complexity_tier)
    if request.encounter_count:
        seed_params["encounter_count"] = request.encounter_count
    if request.random_seed:
        seed_params["random_seed"] = request.random_seed
    if request.description:
        seed_params["description"] = request.description
    if request.messiness_level > 0:
        seed_params["messiness_level"] = request.messiness_level

    gen_seed = GenerationSeed(**seed_params)

    # Use engine with appropriate LLM and messiness settings
    engine = PedsEngine(use_llm=request.use_llm, messiness_level=request.messiness_level)

    # Generate patient
    try:
        patient = engine.generate(gen_seed)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Store patient
    patients_store[patient.id] = patient

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
async def quick_generate(use_llm: bool = Query(True, description="Use LLM for narratives")):
    """
    Start async patient generation. Returns job_id immediately.

    Poll /api/jobs/{job_id} to check status. LLM generation takes ~2 minutes.
    """
    job_id = str(uuid4())[:8]
    age = random.randint(0, 18)

    # Initialize job status
    generation_jobs[job_id] = {
        "status": "pending",
        "patient_id": None,
        "patient_name": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
        "use_llm": use_llm,
        "age": age,
    }

    # Start background thread
    thread = threading.Thread(
        target=_run_generation_job,
        args=(job_id, age, use_llm),
        daemon=True
    )
    thread.start()

    return {"job_id": job_id, "status": "pending", "message": "Generation started. Poll /api/jobs/{job_id} for status."}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Check the status of a generation job."""
    if job_id not in generation_jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = generation_jobs[job_id]
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
):
    """List all generated patients."""
    patients_list = list(patients_store.values())
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
async def get_patient(patient_id: str, format: str = Query("json", pattern="^(json|fhir|markdown)$")):
    """
    Get a patient by ID.
    
    Supports multiple output formats: json, fhir, markdown.
    """
    if patient_id not in patients_store:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    patient = patients_store[patient_id]
    
    if format == "json":
        return JSONResponse(content=json.loads(export_json(patient)))
    elif format == "fhir":
        return JSONResponse(content=export_fhir(patient))
    elif format == "markdown":
        return {"markdown": export_markdown(patient)}


@app.get("/api/patients/{patient_id}/encounters")
async def get_patient_encounters(patient_id: str):
    """Get all encounters for a patient."""
    if patient_id not in patients_store:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    patient = patients_store[patient_id]
    
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
async def get_encounter(patient_id: str, encounter_id: str):
    """Get a specific encounter."""
    if patient_id not in patients_store:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    patient = patients_store[patient_id]
    encounter = patient.get_encounter_by_id(encounter_id)
    
    if not encounter:
        raise HTTPException(status_code=404, detail="Encounter not found")
    
    return encounter.model_dump(mode="json")


@app.get("/api/patients/{patient_id}/export/{format}")
async def export_patient(patient_id: str, format: str):
    """
    Export a patient to a downloadable file.
    
    Formats: json, fhir, markdown
    """
    if patient_id not in patients_store:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    patient = patients_store[patient_id]
    
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
async def delete_patient(patient_id: str):
    """Delete a patient from memory."""
    if patient_id not in patients_store:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    del patients_store[patient_id]
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
async def get_stats():
    """Get generation statistics."""
    patients = list(patients_store.values())
    
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
async def signup(request: SignUpRequest):
    """
    Create a new user account.

    Returns access token and user profile on success.
    """
    if not db_configured():
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        client = get_client()

        # Sign up with Supabase Auth
        auth_response = client.sign_up(request.email, request.password)

        if not auth_response.user:
            raise HTTPException(status_code=400, detail="Signup failed")

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest):
    """
    Log in an existing user.

    Returns access token and user profile on success.
    """
    if not db_configured():
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        client = get_client()

        # Sign in with Supabase Auth
        auth_response = client.sign_in(request.email, request.password)

        if not auth_response.user or not auth_response.session:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Get user profile
        user_repo = UserRepository()
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
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/api/auth/logout")
async def logout(user: AuthenticatedUser = Depends(get_current_user)):
    """Log out the current user."""
    try:
        client = get_client()
        client.sign_out()
        return {"status": "logged_out"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/auth/me", response_model=UserProfile)
async def get_me(user: AuthenticatedUser = Depends(get_current_user)):
    """Get the current user's profile."""
    user_repo = UserRepository()
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
async def update_me(
    display_name: Optional[str] = None,
    learner_level: Optional[str] = None,
    institution: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user)
):
    """Update the current user's profile."""
    user_repo = UserRepository()

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


class PanelResponse(BaseModel):
    """Response model for a panel."""
    id: str
    name: str
    description: Optional[str] = None
    patient_count: int = 0
    config: dict = {}
    created_at: str


@app.get("/api/panels")
async def list_panels(user: AuthenticatedUser = Depends(get_current_user)):
    """List all panels for the current user."""
    panel_repo = PanelRepository()
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
async def create_panel(
    request: CreatePanelRequest,
    user: AuthenticatedUser = Depends(get_current_user)
):
    """Create a new patient panel."""
    panel_repo = PanelRepository()

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
async def get_panel(
    panel_id: str,
    user: AuthenticatedUser = Depends(get_current_user)
):
    """Get a panel with its patients."""
    panel_repo = PanelRepository()
    patient_repo = PatientRepository()

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
async def delete_panel(
    panel_id: str,
    user: AuthenticatedUser = Depends(get_current_user)
):
    """Delete a panel and all its patients."""
    panel_repo = PanelRepository()

    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    panel_repo.delete(panel_id)
    return {"status": "deleted", "panel_id": panel_id}


@app.post("/api/panels/{panel_id}/generate")
async def generate_panel_patients(
    panel_id: str,
    count: int = Query(20, ge=1, le=50),
    user: AuthenticatedUser = Depends(get_current_user)
):
    """Generate patients for a panel."""
    panel_repo = PanelRepository()
    patient_repo = PatientRepository()

    panel = panel_repo.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if panel["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get panel config
    config = panel.get("config", {})
    min_age = config.get("min_age_months", 0)
    max_age = config.get("max_age_months", 216)

    # Generate patients
    engine = PedsEngine(use_llm=False)  # No LLM for batch generation
    generated = []

    for _ in range(count):
        age_months = random.randint(min_age, max_age)
        seed = GenerationSeed(age_months=age_months)

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
            })

        except Exception as e:
            # Log error but continue
            print(f"Failed to generate patient: {e}")

    return {
        "panel_id": panel_id,
        "generated_count": len(generated),
        "patients": generated,
    }


# =============================================================================
# PANEL PATIENT DATA ENDPOINTS
# =============================================================================

@app.get("/api/panels/{panel_id}/patients/{patient_id}")
async def get_panel_patient(
    panel_id: str,
    patient_id: str,
    user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Get full patient data from a panel.

    Returns the complete patient record including demographics, encounters,
    problem list, medications, immunizations, and messages.
    """
    panel_repo = PanelRepository()
    patient_repo = PatientRepository()

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
async def generate_encounter(
    panel_id: str,
    patient_id: str,
    request: GenerateEncounterRequest,
    user: AuthenticatedUser = Depends(get_current_user)
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

    panel_repo = PanelRepository()
    patient_repo = PatientRepository()

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
        patient = Patient(**db_patient["full_record"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load patient record: {e}")

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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate encounter: {e}")

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
    except Exception as e:
        # Log but don't fail - encounter was generated successfully
        print(f"Warning: Failed to persist encounter to database: {e}")

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
async def list_patient_encounters(
    panel_id: str,
    patient_id: str,
    user: AuthenticatedUser = Depends(get_current_user)
):
    """List all encounters for a patient."""
    panel_repo = PanelRepository()
    patient_repo = PatientRepository()

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
async def get_encounter(
    panel_id: str,
    patient_id: str,
    encounter_id: str,
    user: AuthenticatedUser = Depends(get_current_user)
):
    """Get a specific encounter by ID."""
    panel_repo = PanelRepository()
    patient_repo = PatientRepository()

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


class TimelineRequest(BaseModel):
    """Request model for timeline generation."""
    arc_names: list[str] | None = None
    snapshot_interval_months: int = 6


@app.get("/api/panels/{panel_id}/patients/{patient_id}/timeline")
async def get_patient_timeline(
    panel_id: str,
    patient_id: str,
    arc_names: str | None = None,
    interval: int = 6,
    user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Get the patient's timeline showing disease progression over time.

    This is the core Time Travel API - it returns snapshots at different ages
    showing how conditions, medications, and clinical state evolved.
    """
    panel_repo = PanelRepository()
    patient_repo = PatientRepository()

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
        patient = Patient(**full_record)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading patient: {str(e)}")

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
        "snapshots": [
            {
                "age_months": s.age_months,
                "date": s.date.isoformat(),
                "active_conditions": [
                    {"display_name": c.display_name, "code": c.code.code if c.code else None}
                    for c in s.active_conditions
                ],
                "medications": [
                    {"name": m.name, "dosage": m.dosage}
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
            for s in snapshots
        ],
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
async def get_snapshot_at_age(
    panel_id: str,
    patient_id: str,
    age_months: int,
    arc_names: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Get the patient's clinical state at a specific age.

    Returns the snapshot at that age plus the previous snapshot for comparison.
    """
    panel_repo = PanelRepository()
    patient_repo = PatientRepository()

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
        patient = Patient(**full_record)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading patient: {str(e)}")

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

    def snapshot_to_dict(s):
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
                {"name": m.name, "dosage": m.dosage}
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

    return {
        "age_months": age_months,
        "snapshot": snapshot_to_dict(snapshot),
        "previous_snapshot": snapshot_to_dict(prev_snapshot),
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


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
