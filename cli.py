#!/usr/bin/env python3
"""
Oread CLI

Command-line interface for generating synthetic patient records.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.tree import Tree

console = Console()


def setup_paths():
    """Add the project root to sys.path for imports."""
    root = Path(__file__).parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


setup_paths()


def parse_patient_description(description: str) -> dict:
    """
    Parse a natural language patient description using LLM.

    Returns dict with: age_months, sex, conditions, complexity_tier
    """
    from src.llm import get_client

    try:
        llm = get_client()
    except (ValueError, Exception):
        # No API key, fall back to regex parsing
        return _parse_description_regex(description)

    prompt = f'''Extract patient parameters from this description. Return valid JSON only.

Description: "{description}"

Extract:
- age_months: integer (convert years to months, e.g. "2 year old" = 24)
- sex: "male" or "female" or null if not specified
- conditions: array of condition names (lowercase, e.g. ["asthma", "eczema"])
- complexity_tier: "tier-0" (healthy), "tier-1" (single chronic), "tier-2" (multiple), "tier-3" (complex)

Examples:
"2 year old boy with asthma" -> {{"age_months": 24, "sex": "male", "conditions": ["asthma"], "complexity_tier": "tier-1"}}
"healthy 6 month old girl" -> {{"age_months": 6, "sex": "female", "conditions": [], "complexity_tier": "tier-0"}}
"teenager with ADHD and anxiety" -> {{"age_months": 168, "sex": null, "conditions": ["adhd", "anxiety"], "complexity_tier": "tier-2"}}
"newborn" -> {{"age_months": 0, "sex": null, "conditions": [], "complexity_tier": "tier-0"}}

Return ONLY the JSON object, no explanation:'''

    try:
        response = llm.generate(prompt, max_tokens=200, temperature=0.3)
        # Extract JSON from response
        response = response.strip()
        if response.startswith("```"):
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        result = json.loads(response)
        return result
    except Exception:
        # Fall back to regex parsing
        return _parse_description_regex(description)


def _parse_description_regex(description: str) -> dict:
    """Fallback regex-based parsing for patient descriptions."""
    result = {
        "age_months": None,
        "sex": None,
        "conditions": [],
        "complexity_tier": "tier-0",
    }

    desc_lower = description.lower()

    # Parse age
    # "X year old" or "X yo"
    year_match = re.search(r'(\d+)\s*(?:year|yr|yo)', desc_lower)
    if year_match:
        result["age_months"] = int(year_match.group(1)) * 12

    # "X month old" or "X mo"
    month_match = re.search(r'(\d+)\s*(?:month|mo)', desc_lower)
    if month_match:
        result["age_months"] = int(month_match.group(1))

    # "newborn"
    if "newborn" in desc_lower:
        result["age_months"] = 0

    # "infant"
    if "infant" in desc_lower and result["age_months"] is None:
        result["age_months"] = 6

    # "toddler"
    if "toddler" in desc_lower and result["age_months"] is None:
        result["age_months"] = 24

    # "teenager" or "teen"
    if "teen" in desc_lower and result["age_months"] is None:
        result["age_months"] = 168  # 14 years

    # Parse sex
    if any(x in desc_lower for x in ["boy", "male", " son", "his "]):
        result["sex"] = "male"
    elif any(x in desc_lower for x in ["girl", "female", "daughter", "her "]):
        result["sex"] = "female"

    # Parse conditions
    known_conditions = [
        "asthma", "eczema", "adhd", "anxiety", "obesity", "diabetes",
        "allergies", "ear infection", "otitis", "pneumonia", "bronchiolitis",
        "croup", "uti", "urinary tract infection", "gastroenteritis",
        "pharyngitis", "strep", "conjunctivitis"
    ]
    for cond in known_conditions:
        if cond in desc_lower:
            result["conditions"].append(cond.replace(" ", "_"))

    # Determine complexity
    if not result["conditions"]:
        result["complexity_tier"] = "tier-0"
    elif len(result["conditions"]) == 1:
        result["complexity_tier"] = "tier-1"
    elif len(result["conditions"]) <= 3:
        result["complexity_tier"] = "tier-2"
    else:
        result["complexity_tier"] = "tier-3"

    return result


@click.group()
@click.version_option(version="0.1.0", prog_name="oread")
def cli():
    """
    Oread - Synthetic Patient Record Generator

    Generate realistic, clinically coherent patient records for
    AI evaluation, EMR demos, and medical education.
    """
    pass


@cli.command()
@click.option("--engine", type=click.Choice(["peds", "adult", "auto"]), default="auto",
              help="Generation engine to use")
@click.option("--age", type=int, help="Patient age in years")
@click.option("--age-months", type=int, help="Patient age in months (for infants)")
@click.option("--sex", type=click.Choice(["male", "female"]), help="Patient sex")
@click.option("--conditions", type=str, help="Comma-separated list of conditions")
@click.option("--complexity", type=click.Choice(["tier-0", "tier-1", "tier-2", "tier-3"]),
              help="Complexity tier (0=healthy, 3=complex)")
@click.option("--encounters", type=int, help="Approximate number of encounters")
@click.option("--years", type=int, help="Years of medical history")
@click.option("--seed", type=int, help="Random seed for reproducibility")
@click.option("--format", "formats", type=click.Choice(["json", "fhir", "markdown", "all"]),
              multiple=True, default=["all"], help="Output format(s)")
@click.option("--output", "-o", type=click.Path(), help="Output directory")
@click.option("--describe", "-d", type=str, help="Natural language description of the patient")
@click.option("--no-llm", is_flag=True, help="Disable LLM features (use templates only)")
@click.option("--quiet", "-q", is_flag=True, help="Suppress progress output")
def generate(
    engine: str,
    age: Optional[int],
    age_months: Optional[int],
    sex: Optional[str],
    conditions: Optional[str],
    complexity: Optional[str],
    encounters: Optional[int],
    years: Optional[int],
    seed: Optional[int],
    formats: tuple,
    output: Optional[str],
    describe: Optional[str],
    no_llm: bool,
    quiet: bool,
):
    """
    Generate a synthetic patient record.

    Examples:

        oread generate --age 5

        oread generate --age 8 --conditions "asthma,adhd"

        oread generate --describe "A 3yo girl with poorly controlled eczema"

        oread generate -d "healthy newborn boy" --no-llm
    """
    from src.models import GenerationSeed, Sex, ComplexityTier
    from src.engines import EngineOrchestrator, PedsEngine
    from src.exporters import export_json, export_markdown, export_fhir

    # Build generation seed
    seed_params = {}

    # If --describe is provided, parse it to extract parameters
    if describe:
        if not quiet:
            console.print(f"[dim]Parsing description: \"{describe}\"[/dim]")

        parsed = parse_patient_description(description=describe)

        if parsed.get("age_months") is not None:
            seed_params["age_months"] = parsed["age_months"]
        if parsed.get("sex"):
            seed_params["sex"] = Sex(parsed["sex"])
        if parsed.get("conditions"):
            seed_params["conditions"] = parsed["conditions"]
        if parsed.get("complexity_tier"):
            seed_params["complexity_tier"] = ComplexityTier(parsed["complexity_tier"])

        # Store original description
        seed_params["description"] = describe

        if not quiet:
            console.print(f"[dim]  -> age: {parsed.get('age_months')} months, sex: {parsed.get('sex')}, conditions: {parsed.get('conditions')}[/dim]")

    # Explicit options override parsed values
    if age is not None:
        seed_params["age"] = age
    if age_months is not None:
        seed_params["age_months"] = age_months
    if sex:
        seed_params["sex"] = Sex(sex)
    if conditions:
        seed_params["conditions"] = [c.strip() for c in conditions.split(",")]
    if complexity:
        seed_params["complexity_tier"] = ComplexityTier(complexity)
    if encounters:
        seed_params["encounter_count"] = encounters
    if years:
        seed_params["years_of_history"] = years
    if seed:
        seed_params["random_seed"] = seed

    gen_seed = GenerationSeed(**seed_params)

    # Determine output directory
    if output:
        output_dir = Path(output)
    else:
        output_dir = Path.cwd() / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # LLM usage
    use_llm = not no_llm

    # Generate patient
    if not quiet:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Generating patient...", total=None)

            # Use appropriate engine
            if engine == "peds" or (engine == "auto" and (age is None or age < 22)):
                eng = PedsEngine(use_llm=use_llm)
            else:
                console.print("[red]Adult engine not yet implemented[/red]")
                return

            patient = eng.generate(gen_seed)
            progress.update(task, description="Patient generated!")
    else:
        if engine == "peds" or (engine == "auto" and (age is None or age < 22)):
            eng = PedsEngine(use_llm=use_llm)
        else:
            console.print("[red]Adult engine not yet implemented[/red]")
            return
        patient = eng.generate(gen_seed)
    
    # Determine which formats to export
    if "all" in formats:
        export_formats = ["json", "fhir", "markdown"]
    else:
        export_formats = list(formats)
    
    # Create patient-specific output directory
    patient_dir = output_dir / f"patient_{patient.id}"
    patient_dir.mkdir(parents=True, exist_ok=True)
    
    # Export
    exported_files = []
    
    if "json" in export_formats:
        json_path = patient_dir / "patient.json"
        export_json(patient, json_path)
        exported_files.append(("JSON", json_path))
    
    if "fhir" in export_formats:
        fhir_path = patient_dir / "fhir_bundle.json"
        export_fhir(patient, fhir_path)
        exported_files.append(("FHIR R4", fhir_path))
    
    if "markdown" in export_formats:
        md_path = patient_dir / "patient.md"
        export_markdown(patient, md_path)
        exported_files.append(("Markdown", md_path))
    
    # Display results
    if not quiet:
        console.print()
        console.print(Panel(
            f"[bold green]✓ Patient Generated[/bold green]\n\n"
            f"[bold]{patient.demographics.full_name}[/bold]\n"
            f"Age: {patient.demographics.age_years} years\n"
            f"Sex: {patient.demographics.sex_at_birth.value}\n"
            f"Complexity: {patient.complexity_tier.value}\n"
            f"Encounters: {len(patient.encounters)}\n"
            f"Conditions: {len(patient.active_conditions)}",
            title="Patient Summary",
            border_style="green",
        ))
        
        console.print()
        
        table = Table(title="Exported Files")
        table.add_column("Format", style="cyan")
        table.add_column("Path", style="green")
        
        for fmt, path in exported_files:
            table.add_row(fmt, str(path))
        
        console.print(table)
    else:
        # Just print the output directory
        console.print(str(patient_dir))


@cli.command()
@click.option("--count", "-n", type=int, default=10, help="Number of patients to generate")
@click.option("--distribution", type=str, default="healthy:60,tier1:25,tier2:12,tier3:3",
              help="Distribution of complexity tiers (e.g., 'healthy:60,tier1:25,tier2:12,tier3:3')")
@click.option("--age-range", type=str, default="0-18", help="Age range (e.g., '0-18', '5-10')")
@click.option("--engine", type=click.Choice(["peds", "adult", "auto"]), default="auto",
              help="Generation engine to use")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output directory")
@click.option("--format", "formats", type=click.Choice(["json", "fhir", "markdown", "all"]),
              multiple=True, default=["json"], help="Output format(s)")
def batch(
    count: int,
    distribution: str,
    age_range: str,
    engine: str,
    output: str,
    formats: tuple,
):
    """
    Generate a batch of synthetic patients.
    
    Example:
    
        oread batch --count 50 --output ./patients/
    """
    import random
    from src.models import GenerationSeed, ComplexityTier
    from src.engines import PedsEngine
    from src.exporters import export_json, export_markdown, export_fhir, export_json_summary
    
    # Parse distribution
    dist_parts = distribution.split(",")
    dist_map = {}
    for part in dist_parts:
        tier, pct = part.split(":")
        dist_map[tier] = int(pct)
    
    # Parse age range
    age_parts = age_range.split("-")
    min_age = int(age_parts[0])
    max_age = int(age_parts[1])
    
    # Create output directory
    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Determine formats
    if "all" in formats:
        export_formats = ["json", "fhir", "markdown"]
    else:
        export_formats = list(formats)
    
    # Generate patients
    eng = PedsEngine()
    summaries = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Generating {count} patients...", total=count)
        
        for i in range(count):
            # Determine complexity based on distribution
            rand = random.randint(1, 100)
            cumulative = 0
            tier = ComplexityTier.TIER_0
            for tier_name, pct in dist_map.items():
                cumulative += pct
                if rand <= cumulative:
                    tier_map = {
                        "healthy": ComplexityTier.TIER_0,
                        "tier0": ComplexityTier.TIER_0,
                        "tier1": ComplexityTier.TIER_1,
                        "tier2": ComplexityTier.TIER_2,
                        "tier3": ComplexityTier.TIER_3,
                    }
                    tier = tier_map.get(tier_name, ComplexityTier.TIER_0)
                    break
            
            # Random age in range
            age = random.randint(min_age, max_age)
            
            # Generate
            seed = GenerationSeed(age=age, complexity_tier=tier)
            patient = eng.generate(seed)
            
            # Create patient directory
            patient_dir = output_dir / f"patient_{patient.id}"
            patient_dir.mkdir(parents=True, exist_ok=True)
            
            # Export
            if "json" in export_formats:
                export_json(patient, patient_dir / "patient.json")
            if "fhir" in export_formats:
                export_fhir(patient, patient_dir / "fhir_bundle.json")
            if "markdown" in export_formats:
                export_markdown(patient, patient_dir / "patient.md")
            
            summaries.append(export_json_summary(patient))
            
            progress.update(task, advance=1, description=f"Generated {i+1}/{count} patients...")
    
    # Write manifest
    manifest = {
        "count": len(summaries),
        "distribution": dist_map,
        "age_range": age_range,
        "patients": summaries,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    
    console.print()
    console.print(f"[green]✓ Generated {count} patients to {output_dir}[/green]")
    
    # Show summary table
    table = Table(title="Generation Summary")
    table.add_column("Tier", style="cyan")
    table.add_column("Count", justify="right")
    
    tier_counts = {}
    for s in summaries:
        t = s["complexity_tier"]
        tier_counts[t] = tier_counts.get(t, 0) + 1
    
    for tier, cnt in sorted(tier_counts.items()):
        table.add_row(tier, str(cnt))
    
    console.print(table)


@cli.command()
@click.argument("patient_path", type=click.Path(exists=True))
def view(patient_path: str):
    """
    View a patient record summary.
    
    Example:
    
        oread view ./output/patient_abc123/patient.json
    """
    from src.models import Patient
    
    path = Path(patient_path)
    
    if path.is_dir():
        # Look for patient.json in directory
        json_path = path / "patient.json"
        if not json_path.exists():
            console.print(f"[red]No patient.json found in {path}[/red]")
            return
        path = json_path
    
    # Load patient
    patient = Patient.model_validate_json(path.read_text())
    
    # Display summary
    console.print()
    console.print(Panel(
        f"[bold]{patient.demographics.full_name}[/bold]\n"
        f"ID: {patient.id}\n"
        f"DOB: {patient.demographics.date_of_birth}\n"
        f"Age: {patient.demographics.age_years} years\n"
        f"Sex: {patient.demographics.sex_at_birth.value}",
        title="Demographics",
        border_style="blue",
    ))
    
    # Problem list
    if patient.problem_list:
        tree = Tree("[bold]Problem List[/bold]")
        for condition in patient.problem_list:
            status_color = "green" if condition.clinical_status.value == "active" else "yellow"
            tree.add(f"[{status_color}]{condition.display_name}[/{status_color}] ({condition.clinical_status.value})")
        console.print(tree)
    else:
        console.print("[dim]No active problems[/dim]")
    
    # Medications
    if patient.medication_list:
        tree = Tree("[bold]Medications[/bold]")
        for med in patient.active_medications:
            tree.add(f"{med.display_name} {med.dose_quantity} {med.dose_unit} {med.frequency}")
        console.print(tree)
    
    # Encounters summary
    console.print(f"\n[bold]Encounters:[/bold] {len(patient.encounters)}")
    
    # Last 5 encounters
    if patient.encounters:
        table = Table(title="Recent Encounters")
        table.add_column("Date")
        table.add_column("Type")
        table.add_column("Chief Complaint")
        
        for enc in sorted(patient.encounters, key=lambda x: x.date, reverse=True)[:5]:
            table.add_row(
                enc.date.strftime("%Y-%m-%d"),
                enc.type.value.replace("-", " ").title(),
                enc.chief_complaint[:50] + "..." if len(enc.chief_complaint) > 50 else enc.chief_complaint,
            )
        
        console.print(table)


@cli.command()
@click.argument("patient_path", type=click.Path(exists=True))
@click.option("--format", "fmt", type=click.Choice(["json", "fhir", "markdown"]), required=True,
              help="Format to export to")
@click.option("--output", "-o", type=click.Path(), help="Output file path")
def export(patient_path: str, fmt: str, output: Optional[str]):
    """
    Export a patient to a different format.
    
    Example:
    
        oread export ./patient.json --format markdown -o ./patient.md
    """
    from src.models import Patient
    from src.exporters import export_json, export_markdown, export_fhir
    
    path = Path(patient_path)
    
    if path.is_dir():
        json_path = path / "patient.json"
        if not json_path.exists():
            console.print(f"[red]No patient.json found in {path}[/red]")
            return
        path = json_path
    
    # Load patient
    patient = Patient.model_validate_json(path.read_text())
    
    # Determine output path
    if output:
        out_path = Path(output)
    else:
        ext_map = {"json": ".json", "fhir": "_fhir.json", "markdown": ".md"}
        out_path = path.parent / f"{path.stem}{ext_map[fmt]}"
    
    # Export
    if fmt == "json":
        export_json(patient, out_path)
    elif fmt == "fhir":
        export_fhir(patient, out_path)
    elif fmt == "markdown":
        export_markdown(patient, out_path)
    
    console.print(f"[green]✓ Exported to {out_path}[/green]")


@cli.command()
def archetypes():
    """
    List available patient archetypes.
    """
    archetypes_dir = Path(__file__).parent / "archetypes"
    
    if not archetypes_dir.exists():
        console.print("[yellow]No archetypes directory found[/yellow]")
        return
    
    console.print("[bold]Available Archetypes[/bold]\n")
    
    for category in ["peds", "adult"]:
        cat_dir = archetypes_dir / category
        if cat_dir.exists():
            console.print(f"[cyan]{category.upper()}[/cyan]")
            for arch_file in cat_dir.glob("*.yaml"):
                console.print(f"  • {arch_file.stem}")
            console.print()


@cli.command()
def info():
    """
    Show information about Oread.
    """
    console.print(Panel(
        "[bold]Oread[/bold]\n\n"
        "A comprehensive synthetic patient generator for:\n"
        "• AI/LLM evaluation and benchmarking\n"
        "• EMR demos and testing\n"
        "• Medical education and training\n\n"
        "[dim]Generates clinically coherent, longitudinal patient records[/dim]\n"
        "[dim]with support for FHIR R4, JSON, and Markdown export.[/dim]",
        title="About",
        border_style="blue",
    ))
    
    console.print("\n[bold]Features:[/bold]")
    console.print("  • Pediatric patients (birth through age 21)")
    console.print("  • Well-child visits with growth tracking")
    console.print("  • Immunization schedules (AAP/CDC)")
    console.print("  • Acute illnesses and chronic conditions")
    console.print("  • FHIR R4 compliant export")
    console.print("  • Full narrative clinical notes")
    
    console.print("\n[bold]Quick Start:[/bold]")
    console.print("  oread generate --age 5")
    console.print("  oread generate --conditions asthma,adhd")
    console.print("  oread batch --count 50 -o ./patients/")


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
