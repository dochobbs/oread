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

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Setup paths
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.models import GenerationSeed, Sex, ComplexityTier, Patient
from src.engines import PedsEngine
from src.exporters import export_json, export_json_summary, export_markdown, export_fhir, export_ccda


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
