"""
Narrative claim extraction.

Extracts verifiable factual claims from clinical narrative notes
so they can be compared against structured data.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import NarrativeClaim, ClaimType


class NarrativeClaimExtractor:
  """
  Extracts factual claims from clinical narratives using LLM.

  These claims can then be verified against structured data to
  ensure narrative-structured consistency.
  """

  EXTRACTION_PROMPT = """Extract all factual clinical claims from this note that could be verified against structured data.

Clinical Note:
{narrative}

Return a JSON array of claims. For each claim include:
- claim_type: One of "medication", "condition", "procedure", "lab", "vital", "treatment_status", "social"
- claim_text: The exact phrase from the note making this claim
- structured_value: Your interpretation as structured data (a dict)
- confidence: 0.0 to 1.0

Focus on extracting:
1. MEDICATION claims: "on chemotherapy", "taking amoxicillin", "current medications include..."
2. CONDITION claims: "has diabetes", "asthma well-controlled", "history of..."
3. TREATMENT_STATUS claims: "tolerating treatment well", "in remission", "on active protocol"
4. LAB claims: "labs normal", "CBC showed...", "recent bloodwork"
5. VITAL claims: specific vital sign values mentioned

Example output:
[
  {{
    "claim_type": "treatment_status",
    "claim_text": "currently on active chemotherapy protocol",
    "structured_value": {{"treatment_type": "chemotherapy", "status": "active"}},
    "confidence": 0.95
  }},
  {{
    "claim_type": "medication",
    "claim_text": "tolerating treatment well",
    "structured_value": {{"implies_active_medications": true}},
    "confidence": 0.8
  }}
]

Be exhaustive but precise. Only extract claims that could theoretically be verified against a patient record."""

  def __init__(self, llm_client: Any):
    self.llm = llm_client

  def extract(self, narrative: str) -> list[NarrativeClaim]:
    """
    Extract verifiable claims from a clinical narrative.

    Args:
      narrative: The clinical note text

    Returns:
      List of NarrativeClaim objects
    """
    if not narrative or len(narrative.strip()) < 20:
      return []

    try:
      response = self.llm.generate(
        self.EXTRACTION_PROMPT.format(narrative=narrative),
        system="You are a clinical NLP system. Extract factual claims precisely. Return only valid JSON.",
        temperature=0.2,
      )

      # Parse JSON from response
      json_str = self._extract_json(response)
      claims_data = json.loads(json_str)

      claims = []
      for item in claims_data:
        try:
          # Convert claim_type string to enum
          claim_type_str = item.get("claim_type", "").lower()
          claim_type = self._map_claim_type(claim_type_str)

          claims.append(NarrativeClaim(
            claim_type=claim_type,
            claim_text=item.get("claim_text", ""),
            structured_value=item.get("structured_value", {}),
            confidence=item.get("confidence", 0.8),
          ))
        except Exception:
          continue  # Skip malformed claims

      return claims

    except Exception as e:
      # Don't crash on extraction failure
      print(f"Warning: Claim extraction failed: {e}")
      return []

  def _extract_json(self, response: str) -> str:
    """Extract JSON from response, handling markdown code blocks."""
    # Try to find JSON in code block
    json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', response, re.DOTALL)
    if json_match:
      return json_match.group(1)

    # Try to find raw JSON array
    array_match = re.search(r'\[.*\]', response, re.DOTALL)
    if array_match:
      return array_match.group(0)

    return response

  def _map_claim_type(self, claim_type_str: str) -> ClaimType:
    """Map string to ClaimType enum."""
    mapping = {
      "medication": ClaimType.MEDICATION,
      "condition": ClaimType.CONDITION,
      "procedure": ClaimType.PROCEDURE,
      "lab": ClaimType.LAB,
      "vital": ClaimType.VITAL,
      "treatment_status": ClaimType.TREATMENT_STATUS,
      "social": ClaimType.SOCIAL,
    }
    return mapping.get(claim_type_str, ClaimType.CONDITION)
