"""Knowledge retrieval module."""

from .condition_service import ConditionKnowledgeService, create_condition_service
from .models import ConditionDefinition, ConditionLookupResult, LabDefinition, MedicationDefinition
from .cache import ConditionCache

# Exa web search (optional - requires exa_py and EXA_API_KEY)
try:
  from .exa_client import (
    ExaSearchClient,
    create_exa_search_functions,
    create_exa_enhanced_search,
  )
  _EXA_AVAILABLE = True
except ImportError:
  _EXA_AVAILABLE = False
  ExaSearchClient = None  # type: ignore
  create_exa_search_functions = None  # type: ignore
  create_exa_enhanced_search = None  # type: ignore

__all__ = [
  "ConditionKnowledgeService",
  "create_condition_service",
  "ConditionDefinition",
  "ConditionLookupResult",
  "LabDefinition",
  "MedicationDefinition",
  "ConditionCache",
  # Exa (optional)
  "ExaSearchClient",
  "create_exa_search_functions",
  "create_exa_enhanced_search",
]
