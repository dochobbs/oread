"""
Condition Knowledge Service.

Provides unified access to condition definitions from multiple sources:
1. Curated YAML (authoritative, no network calls)
2. Disk cache (previously retrieved conditions)
3. Web search (grounded retrieval for unknown conditions)
4. LLM knowledge (fallback when web search unavailable)

The service handles the complexity of multi-source retrieval so the
rest of the system can just call `get_condition(name)`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Any, TYPE_CHECKING

from .models import (
  ConditionDefinition,
  ConditionLookupResult,
  LabDefinition,
  MedicationDefinition,
  ExamFinding,
)
from .cache import ConditionCache

if TYPE_CHECKING:
  from src.llm.client import LLMClient

# Type for web search function
WebSearchFn = Callable[[str], list[dict]]  # query -> list of {url, title, snippet}
WebFetchFn = Callable[[str], str]  # url -> content


class ConditionKnowledgeService:
  """
  Unified condition knowledge retrieval service.

  Usage:
    service = ConditionKnowledgeService(
      yaml_conditions=loaded_yaml,
      llm_client=claude_client,
      cache_dir=Path("./cache/conditions"),
      web_search_fn=my_web_search,  # Optional
    )

    result = service.get_condition("acute lymphoblastic leukemia")
    if result.found:
      icd10 = result.definition.icd10_primary
  """

  # Authoritative medical domains for web search filtering
  AUTHORITATIVE_DOMAINS = [
    "aap.org", "aappublications.org",
    "cdc.gov", "nih.gov", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
    "who.int",
    "uptodate.com",
    "mayoclinic.org", "clevelandclinic.org",
    "childrenshospital.org", "chop.edu", "seattlechildrens.org",
    "acponline.org", "aafp.org",
    "merckmanuals.com",
  ]

  def __init__(
    self,
    yaml_conditions: dict,
    llm_client: Any,  # LLMClient from src.llm
    cache_dir: Path,
    web_search_fn: WebSearchFn | None = None,
    web_fetch_fn: WebFetchFn | None = None,
  ):
    self.yaml_conditions = yaml_conditions
    self.llm = llm_client
    self.cache = ConditionCache(cache_dir)
    self.web_search = web_search_fn
    self.web_fetch = web_fetch_fn

    # Build lookup index from YAML
    self._name_to_key: dict[str, str] = {}
    self._build_yaml_index()

  def _build_yaml_index(self) -> None:
    """Build reverse lookup from display names and aliases to condition keys."""
    for key, data in self.yaml_conditions.items():
      if key.startswith("_") or not isinstance(data, dict):
        continue

      # Index the key itself
      self._name_to_key[key.lower()] = key

      # Index display name
      display = data.get("display_name", "")
      if display:
        self._name_to_key[display.lower()] = key

      # Index aliases
      for alias in data.get("aliases", []):
        self._name_to_key[alias.lower()] = key

  def _normalize_name(self, name: str) -> str:
    """Normalize a condition name for lookup."""
    return name.lower().strip()

  def get_condition(self, name: str) -> ConditionLookupResult:
    """
    Get a condition definition by name.

    Tries sources in order:
    1. Curated YAML
    2. Disk cache
    3. Web search + LLM structuring
    4. LLM knowledge only

    Returns ConditionLookupResult with found=True/False and definition if found.
    """
    normalized = self._normalize_name(name)

    # Layer 1: Check curated YAML
    yaml_key = self._name_to_key.get(normalized)
    if yaml_key:
      definition = self._yaml_to_definition(yaml_key)
      return ConditionLookupResult(
        found=True,
        definition=definition,
        source="yaml",
        cached=False,
      )

    # Layer 2: Check cache
    cached_data = self.cache.get(normalized)
    if cached_data:
      try:
        definition = ConditionDefinition.model_validate(cached_data)
        return ConditionLookupResult(
          found=True,
          definition=definition,
          source="cache",
          cached=True,
        )
      except Exception:
        pass  # Invalid cache entry, continue to retrieval

    # Layer 3: Web search + LLM (if web search available and LLM available)
    if self.web_search and self.llm:
      definition = self._retrieve_via_web_search(name)
      if definition:
        # Cache for future use
        self.cache.set(normalized, definition.model_dump())
        return ConditionLookupResult(
          found=True,
          definition=definition,
          source="web_search",
          cached=False,
        )

    # Layer 4: LLM knowledge only (no grounding)
    if self.llm:
      definition = self._retrieve_via_llm_only(name)
      if definition:
        # Cache but mark as lower confidence
        definition.confidence = 0.7
        definition.needs_verification = True
        self.cache.set(normalized, definition.model_dump())
        return ConditionLookupResult(
          found=True,
          definition=definition,
          source="llm",
          cached=False,
        )

    # Not found anywhere
    return ConditionLookupResult(found=False)

  def _yaml_to_definition(self, key: str) -> ConditionDefinition:
    """Convert YAML condition data to ConditionDefinition."""
    data = self.yaml_conditions[key]

    # Extract ICD-10 codes (check both top-level and nested under billing_codes)
    billing = data.get("billing_codes", {})
    icd10_raw = billing.get("icd10") or data.get("icd10", [])
    icd10_codes = icd10_raw if isinstance(icd10_raw, list) else [icd10_raw] if icd10_raw else []

    # Extract labs
    labs = []
    for lab_data in data.get("diagnostics", {}).get("labs", []):
      if isinstance(lab_data, dict):
        labs.append(LabDefinition(
          name=lab_data.get("name", ""),
          loinc=lab_data.get("loinc"),
          value_type=lab_data.get("value_type", "binary"),
          unit=lab_data.get("unit"),
          normal_range_low=lab_data.get("normal_range_low"),
          normal_range_high=lab_data.get("normal_range_high"),
          probability_abnormal=lab_data.get("probability_abnormal", 0.3),
          required_at_followup=lab_data.get("required_at_followup", False),
        ))

    # Extract medications
    medications = []
    for med_data in data.get("treatment", {}).get("medications", []):
      if isinstance(med_data, dict):
        medications.append(MedicationDefinition(
          agent=med_data.get("agent", ""),
          rxnorm=med_data.get("rxnorm"),
          dose_mg_kg=med_data.get("dose_mg_kg"),
          max_dose_mg=med_data.get("max_dose_mg"),
          frequency=med_data.get("frequency"),
          route=med_data.get("route", "oral"),
          indication=med_data.get("indication"),
        ))

    # Extract physical exam findings
    exam_findings = []
    for finding in data.get("presentation", {}).get("physical_exam", []):
      if isinstance(finding, dict):
        exam_findings.append(ExamFinding(
          system=finding.get("system", ""),
          finding=finding.get("finding", ""),
          probability=finding.get("probability", 0.8),
        ))

    # Determine if this condition requires monitoring labs
    category = data.get("category", "acute")
    monitoring = data.get("monitoring_requirements", {})
    requires_monitoring = bool(monitoring.get("required_labs_at_followup"))

    return ConditionDefinition(
      condition_key=key,
      display_name=data.get("display_name", key.replace("_", " ").title()),
      aliases=data.get("aliases", []),
      icd10_codes=icd10_codes,
      icd10_primary=icd10_codes[0] if icd10_codes else None,
      snomed_code=billing.get("snomed"),
      category=category,
      body_system=data.get("system"),
      typical_symptoms=[
        s.get("name", "") if isinstance(s, dict) else str(s)
        for s in data.get("presentation", {}).get("symptoms", [])
      ],
      physical_exam_findings=exam_findings,
      labs=labs,
      imaging=data.get("diagnostics", {}).get("imaging", []),
      medications=medications,
      treatment_approach=data.get("treatment", {}).get("approach"),
      managed_by_specialty=data.get("treatment", {}).get("managed_by_specialty", False),
      specialty=data.get("treatment", {}).get("specialty"),
      requires_monitoring_labs=requires_monitoring,
      monitoring_lab_types=monitoring.get("required_labs_at_followup", []),
      followup_frequency=monitoring.get("followup_frequency"),
      source="yaml",
      confidence=1.0,
    )

  def _retrieve_via_web_search(self, condition_name: str) -> ConditionDefinition | None:
    """Use web search to retrieve and structure condition knowledge."""
    if not self.web_search:
      return None

    # Execute searches for different aspects
    search_queries = [
      f"{condition_name} ICD-10 code",
      f"{condition_name} pediatric treatment guidelines",
      f"{condition_name} diagnosis criteria symptoms",
    ]

    all_results = []
    for query in search_queries:
      try:
        results = self.web_search(query)
        all_results.extend(results[:5])  # Top 5 per query
      except Exception:
        continue

    if not all_results:
      return None

    # Filter for authoritative sources
    authoritative = [
      r for r in all_results
      if any(domain in r.get("url", "") for domain in self.AUTHORITATIVE_DOMAINS)
    ]

    # Use all results if no authoritative ones found
    sources_to_use = authoritative if authoritative else all_results[:10]

    # Fetch full content from top sources if fetch function available
    source_content = []
    if self.web_fetch:
      for result in sources_to_use[:3]:  # Fetch top 3
        try:
          content = self.web_fetch(result["url"])
          source_content.append({
            "url": result["url"],
            "title": result.get("title", ""),
            "content": content[:5000],  # Limit content length
          })
        except Exception:
          # Fall back to snippet
          source_content.append({
            "url": result["url"],
            "title": result.get("title", ""),
            "content": result.get("snippet", ""),
          })
    else:
      # Use snippets only
      source_content = [
        {
          "url": r.get("url", ""),
          "title": r.get("title", ""),
          "content": r.get("snippet", ""),
        }
        for r in sources_to_use
      ]

    # Structure with LLM
    return self._structure_with_llm(condition_name, source_content, grounded=True)

  def _retrieve_via_llm_only(self, condition_name: str) -> ConditionDefinition | None:
    """Use LLM's training knowledge only (no grounding)."""
    return self._structure_with_llm(condition_name, [], grounded=False)

  def _structure_with_llm(
    self,
    condition_name: str,
    sources: list[dict],
    grounded: bool,
  ) -> ConditionDefinition | None:
    """Use LLM to create structured condition definition."""
    if not self.llm:
      return None

    if grounded and sources:
      source_text = "\n\n".join([
        f"Source: {s['title']} ({s['url']})\n{s['content']}"
        for s in sources
      ])
      grounding_instruction = f"""Based on these medical sources:

{source_text}

"""
    else:
      grounding_instruction = """Based on your medical knowledge, """

    prompt = f"""{grounding_instruction}Create a structured condition definition for "{condition_name}" in pediatric patients.

Return a JSON object with these fields:
{{
  "condition_key": "snake_case_key",
  "display_name": "Standard Clinical Name",
  "aliases": ["other names", "abbreviations"],
  "icd10_codes": ["X00.0", "X00.1"],
  "icd10_primary": "X00.0",
  "snomed_code": "12345678",
  "category": "acute or chronic",
  "body_system": "e.g., respiratory, hematology_oncology, neurology",
  "typical_symptoms": ["symptom1", "symptom2"],
  "physical_exam_findings": [
    {{"system": "heent", "finding": "finding text", "probability": 0.8}}
  ],
  "labs": [
    {{
      "name": "Lab Name",
      "loinc": "12345-6",
      "value_type": "numeric or binary",
      "unit": "unit if numeric",
      "normal_range_low": 0.0,
      "normal_range_high": 10.0,
      "required_at_followup": true
    }}
  ],
  "medications": [
    {{
      "agent": "Medication Name",
      "rxnorm": "12345",
      "dose_mg_kg": 10.0,
      "frequency": "BID",
      "route": "oral",
      "indication": "why prescribed",
      "line": "first, second, or alternative"
    }}
  ],
  "treatment_approach": "brief description",
  "managed_by_specialty": true,
  "specialty": "specialty name if managed_by_specialty is true",
  "requires_monitoring_labs": true,
  "monitoring_lab_types": ["cbc", "cmp"],
  "followup_frequency": "e.g., monthly, every 3 months",
  "needs_verification": true
}}

Be precise with medical codes. If uncertain about a specific code, set needs_verification to true.
For pediatric-specific conditions, include age-appropriate dosing and monitoring.
For chronic conditions, always set requires_monitoring_labs to true."""

    try:
      response = self.llm.generate(
        prompt,
        system="You are a pediatric medical knowledge system. Provide accurate, structured condition information. Always use real ICD-10-CM, SNOMED-CT, LOINC, and RxNorm codes - never invent codes.",
        temperature=0.2,  # Low temperature for factual content
      )

      # Parse JSON from response
      json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
      if json_match:
        json_str = json_match.group(1)
      else:
        # Try to find raw JSON object
        obj_match = re.search(r'\{.*\}', response, re.DOTALL)
        if obj_match:
          json_str = obj_match.group(0)
        else:
          json_str = response

      data = json.loads(json_str)

      # Add metadata
      data["source"] = "web_search" if grounded else "llm"
      data["confidence"] = 0.9 if grounded else 0.7

      return ConditionDefinition.model_validate(data)

    except Exception as e:
      # Log error but don't crash
      print(f"Warning: Failed to structure condition '{condition_name}': {e}")
      return None

  def get_condition_key(self, name: str) -> str | None:
    """
    Get the condition key for a name (for backward compatibility).

    Returns the YAML key if found, or a generated key for dynamic conditions.
    """
    result = self.get_condition(name)
    if result.found and result.definition:
      return result.definition.condition_key
    return None


# Convenience function for engine integration
def create_condition_service(
  yaml_conditions: dict,
  llm_client: Any,
  cache_dir: Path,
  web_search_fn: WebSearchFn | None = None,
  web_fetch_fn: WebFetchFn | None = None,
) -> ConditionKnowledgeService:
  """Factory function to create a ConditionKnowledgeService."""
  return ConditionKnowledgeService(
    yaml_conditions=yaml_conditions,
    llm_client=llm_client,
    cache_dir=cache_dir,
    web_search_fn=web_search_fn,
    web_fetch_fn=web_fetch_fn,
  )
