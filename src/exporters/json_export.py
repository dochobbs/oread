"""
JSON exporter for SynthPatient.

Exports patient data as clean, human-readable JSON.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.models import Patient


class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder that handles dates and datetimes."""
    
    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        return super().default(obj)


def export_json(
    patient: Patient,
    output_path: Path | None = None,
    indent: int = 2,
    include_nulls: bool = False,
) -> str:
    """
    Export a patient to JSON format.
    
    Args:
        patient: The patient to export
        output_path: Optional path to write the JSON file
        indent: JSON indentation level
        include_nulls: Whether to include null values in output
    
    Returns:
        JSON string representation of the patient
    """
    # Use Pydantic's model_dump with mode='json' for proper serialization
    data = patient.model_dump(mode="json", exclude_none=not include_nulls)
    
    # Convert to JSON string
    json_str = json.dumps(data, indent=indent, cls=DateTimeEncoder)
    
    # Write to file if path provided
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json_str)
    
    return json_str


def export_json_summary(patient: Patient) -> dict[str, Any]:
    """
    Export a summary of the patient (useful for listings/previews).
    
    Returns a dict with key patient information.
    """
    return {
        "id": patient.id,
        "name": patient.demographics.full_name,
        "date_of_birth": patient.demographics.date_of_birth.isoformat(),
        "age_years": patient.demographics.age_years,
        "sex": patient.demographics.sex_at_birth.value,
        "complexity_tier": patient.complexity_tier.value,
        "active_conditions": [c.display_name for c in patient.active_conditions],
        "active_medications": [m.display_name for m in patient.active_medications],
        "encounter_count": len(patient.encounters),
        "generated_at": patient.generated_at.isoformat(),
    }
