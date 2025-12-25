"""
Integration tests for SynthPatient.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import date


class TestModels:
    """Test data models."""
    
    def test_patient_creation(self):
        from src.models import (
            Patient, Demographics, Address, Contact, SocialHistory,
            Sex, ComplexityTier
        )
        
        address = Address(
            line1="123 Main St",
            city="Springfield",
            state="MN",
            postal_code="55555",
        )
        
        emergency_contact = Contact(
            name="Jane Doe",
            relationship="Mother",
            phone="(555) 123-4567",
        )
        
        demographics = Demographics(
            given_names=["John"],
            family_name="Doe",
            date_of_birth=date(2020, 1, 15),
            sex_at_birth=Sex.MALE,
            address=address,
            phone="(555) 987-6543",
            emergency_contact=emergency_contact,
        )
        
        social_history = SocialHistory(
            living_situation="Lives with parents",
        )
        
        patient = Patient(
            demographics=demographics,
            social_history=social_history,
        )
        
        assert patient.demographics.full_name == "John Doe"
        assert patient.demographics.age_years >= 4  # Born in 2020
        assert patient.is_pediatric == True
        assert patient.complexity_tier == ComplexityTier.TIER_0
    
    def test_generation_seed(self):
        from src.models import GenerationSeed, Sex, ComplexityTier
        
        seed = GenerationSeed(
            age=5,
            sex=Sex.FEMALE,
            conditions=["asthma", "eczema"],
            complexity_tier=ComplexityTier.TIER_2,
        )
        
        assert seed.age == 5
        assert seed.sex == Sex.FEMALE
        assert len(seed.conditions) == 2


class TestGrowth:
    """Test growth calculations."""
    
    def test_weight_percentile(self):
        from knowledge.growth.cdc_2000 import calculate_weight_percentile
        
        # 12-month-old male, ~10kg should be around 50th percentile
        result = calculate_weight_percentile(10.0, 12, "male")
        
        assert 40 < result.percentile < 60
        assert -0.5 < result.z_score < 0.5
    
    def test_height_percentile(self):
        from knowledge.growth.cdc_2000 import calculate_height_percentile
        
        # 12-month-old female, ~74cm should be around 50th percentile
        result = calculate_height_percentile(74.0, 12, "female")
        
        assert 40 < result.percentile < 60
    
    def test_generate_at_percentile(self):
        from knowledge.growth.cdc_2000 import (
            generate_weight_at_percentile,
            generate_height_at_percentile,
        )
        
        # 50th percentile weight for 24-month male
        weight = generate_weight_at_percentile(50, 24, "male")
        assert 11 < weight < 14  # Should be around 12.5kg
        
        # 50th percentile height for 24-month female
        height = generate_height_at_percentile(50, 24, "female")
        assert 84 < height < 88  # Should be around 86cm
    
    def test_growth_trajectory(self):
        from knowledge.growth.cdc_2000 import GrowthTrajectory
        
        trajectory = GrowthTrajectory(
            sex="male",
            weight_percentile=50,
            height_percentile=50,
        )
        
        # Generate measurements at several ages
        m1 = trajectory.generate_measurement(2)
        m2 = trajectory.generate_measurement(6)
        m3 = trajectory.generate_measurement(12)
        
        # Weight should increase
        assert m1[0] < m2[0] < m3[0]
        # Height should increase
        assert m1[1] < m2[1] < m3[1]


class TestEngine:
    """Test generation engine."""
    
    def test_basic_generation(self):
        from src.models import GenerationSeed
        from src.engines import PedsEngine
        
        seed = GenerationSeed(age=2, random_seed=42)
        engine = PedsEngine()
        
        patient = engine.generate(seed)
        
        assert patient is not None
        # Allow for slight age calculation variance
        assert 1 <= patient.demographics.age_years <= 2
        assert len(patient.encounters) > 0
        assert len(patient.growth_data) > 0
    
    def test_generation_with_conditions(self):
        from src.models import GenerationSeed, ComplexityTier
        from src.engines import PedsEngine
        
        seed = GenerationSeed(
            age=8,
            conditions=["Asthma"],
            complexity_tier=ComplexityTier.TIER_1,
            random_seed=42,
        )
        engine = PedsEngine()
        
        patient = engine.generate(seed)
        
        assert patient is not None
        assert patient.complexity_tier == ComplexityTier.TIER_1
        # Should have condition-related encounters
        chronic_encounters = [e for e in patient.encounters if "follow" in e.chief_complaint.lower() or "asthma" in e.chief_complaint.lower()]
        assert len(chronic_encounters) > 0
    
    def test_infant_generation(self):
        from src.models import GenerationSeed
        from src.engines import PedsEngine
        
        seed = GenerationSeed(age_months=6, random_seed=42)
        engine = PedsEngine()
        
        patient = engine.generate(seed)
        
        assert patient is not None
        # Allow for slight age calculation variance
        assert 5 <= patient.demographics.age_months <= 7
        # Should have head circumference in growth data
        hc_measurements = [g for g in patient.growth_data if g.head_circumference_cm is not None]
        assert len(hc_measurements) > 0


class TestExporters:
    """Test export functionality."""
    
    def test_json_export(self):
        from src.models import GenerationSeed
        from src.engines import PedsEngine
        from src.exporters import export_json
        import json
        
        seed = GenerationSeed(age=5, random_seed=42)
        patient = PedsEngine().generate(seed)
        
        json_str = export_json(patient)
        
        # Should be valid JSON
        data = json.loads(json_str)
        assert data["id"] == patient.id
        assert data["demographics"]["full_name"] == patient.demographics.full_name
    
    def test_markdown_export(self):
        from src.models import GenerationSeed
        from src.engines import PedsEngine
        from src.exporters import export_markdown
        
        seed = GenerationSeed(age=5, random_seed=42)
        patient = PedsEngine().generate(seed)
        
        markdown = export_markdown(patient)
        
        # Should contain key sections
        assert "# Patient Record" in markdown
        assert patient.demographics.full_name in markdown
        assert "## Demographics" in markdown
        assert "## Encounter History" in markdown
    
    def test_fhir_export(self):
        from src.models import GenerationSeed
        from src.engines import PedsEngine
        from src.exporters import export_fhir
        
        seed = GenerationSeed(age=5, random_seed=42)
        patient = PedsEngine().generate(seed)
        
        bundle = export_fhir(patient)
        
        # Should be a valid FHIR bundle
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "collection"
        assert len(bundle["entry"]) > 0
        
        # Should have Patient resource
        patient_resources = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient"]
        assert len(patient_resources) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
