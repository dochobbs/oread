"""
Exa Web Search Client for Condition Knowledge Service.

Provides web search and content fetching capabilities using Exa's
neural/semantic search API, optimized for medical content retrieval.

Usage:
  from src.knowledge.exa_client import create_exa_search_functions

  # Get search functions for ConditionKnowledgeService
  web_search_fn, web_fetch_fn = create_exa_search_functions()

  # Or use directly
  client = ExaSearchClient()
  results = client.search("acute lymphoblastic leukemia ICD-10")

Requires:
  - EXA_API_KEY environment variable
  - pip install exa_py (or pip install oread[websearch])
"""

from __future__ import annotations

import os
from typing import Callable

# Type aliases matching condition_service.py
WebSearchFn = Callable[[str], list[dict]]  # query -> list of {url, title, snippet}
WebFetchFn = Callable[[str], str]  # url -> content


class ExaSearchClient:
  """
  Wrapper around Exa's Python SDK for medical content search.

  Features:
  - Neural/semantic search optimized for medical queries
  - Domain filtering for authoritative medical sources
  - Content retrieval with highlights
  - Graceful degradation if API unavailable
  """

  # Authoritative medical domains to prioritize
  MEDICAL_DOMAINS = [
    "aap.org",
    "aappublications.org",
    "cdc.gov",
    "nih.gov",
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "who.int",
    "uptodate.com",
    "mayoclinic.org",
    "clevelandclinic.org",
    "childrenshospital.org",
    "chop.edu",
    "seattlechildrens.org",
    "acponline.org",
    "aafp.org",
    "merckmanuals.com",
    "medlineplus.gov",
    "emedicine.medscape.com",
  ]

  def __init__(self, api_key: str | None = None):
    """
    Initialize Exa client.

    Args:
      api_key: Exa API key. If not provided, reads from EXA_API_KEY env var.

    Raises:
      ImportError: If exa_py package not installed.
      ValueError: If no API key available.
    """
    self.api_key = api_key or os.getenv("EXA_API_KEY")

    if not self.api_key:
      raise ValueError(
        "Exa API key required. Set EXA_API_KEY environment variable "
        "or pass api_key parameter."
      )

    try:
      from exa_py import Exa
      self._client = Exa(self.api_key)
    except ImportError:
      raise ImportError(
        "exa_py package not installed. Install with: "
        "pip install exa_py  OR  pip install oread[websearch]"
      )

  def search(
    self,
    query: str,
    num_results: int = 10,
    include_domains: list[str] | None = None,
  ) -> list[dict]:
    """
    Search for medical content using Exa's neural search.

    Args:
      query: Search query (e.g., "acute otitis media ICD-10 code")
      num_results: Maximum number of results to return
      include_domains: Restrict to specific domains (defaults to MEDICAL_DOMAINS)

    Returns:
      List of dicts with keys: url, title, snippet
    """
    try:
      # Use medical domains by default
      domains = include_domains or self.MEDICAL_DOMAINS

      response = self._client.search(
        query,
        num_results=num_results,
        include_domains=domains,
        type="neural",  # Use semantic search
      )

      results = []
      for result in response.results:
        results.append({
          "url": result.url,
          "title": result.title or "",
          "snippet": getattr(result, "text", "") or getattr(result, "snippet", "") or "",
          "score": getattr(result, "score", None),
        })

      return results

    except Exception as e:
      print(f"Warning: Exa search failed: {e}")
      return []

  def search_with_contents(
    self,
    query: str,
    num_results: int = 5,
    text_length: int = 2000,
  ) -> list[dict]:
    """
    Search and retrieve content in a single call.

    More efficient than separate search + fetch for multiple URLs.

    Args:
      query: Search query
      num_results: Maximum results
      text_length: Characters of content to retrieve per result

    Returns:
      List of dicts with keys: url, title, content
    """
    try:
      # In v2, contents is a parameter on search()
      response = self._client.search(
        query,
        num_results=num_results,
        include_domains=self.MEDICAL_DOMAINS,
        contents={"text": {"maxCharacters": text_length}},
        type="neural",
      )

      results = []
      for result in response.results:
        results.append({
          "url": result.url,
          "title": result.title or "",
          "content": result.text or "",
        })

      return results

    except Exception as e:
      print(f"Warning: Exa search_with_contents failed: {e}")
      return []

  def get_contents(self, urls: list[str], text_length: int = 5000) -> dict[str, str]:
    """
    Retrieve content from specific URLs.

    Args:
      urls: List of URLs to fetch
      text_length: Maximum characters per URL

    Returns:
      Dict mapping URL -> content
    """
    try:
      response = self._client.get_contents(
        urls,
        text={"maxCharacters": text_length},
      )

      return {
        result.url: result.text or ""
        for result in response.results
      }

    except Exception as e:
      print(f"Warning: Exa get_contents failed: {e}")
      return {}


def create_exa_search_functions(
  api_key: str | None = None,
) -> tuple[WebSearchFn | None, WebFetchFn | None]:
  """
  Factory function to create web search functions for ConditionKnowledgeService.

  Returns (None, None) if Exa is not available (no API key or package).

  Usage:
    web_search_fn, web_fetch_fn = create_exa_search_functions()

    service = ConditionKnowledgeService(
      yaml_conditions=conditions,
      llm_client=llm,
      cache_dir=Path("./cache/conditions"),
      web_search_fn=web_search_fn,
      web_fetch_fn=web_fetch_fn,
    )

  Returns:
    Tuple of (web_search_fn, web_fetch_fn) or (None, None) if unavailable.
  """
  try:
    client = ExaSearchClient(api_key)
  except (ImportError, ValueError) as e:
    print(f"Exa web search not available: {e}")
    return None, None

  def web_search(query: str) -> list[dict]:
    """Search wrapper matching WebSearchFn signature."""
    return client.search(query, num_results=10)

  def web_fetch(url: str) -> str:
    """Fetch wrapper matching WebFetchFn signature."""
    contents = client.get_contents([url])
    return contents.get(url, "")

  return web_search, web_fetch


def create_exa_enhanced_search(
  api_key: str | None = None,
) -> tuple[WebSearchFn | None, WebFetchFn | None]:
  """
  Create enhanced search functions that fetch content inline.

  This variant uses search_and_contents for more efficient retrieval,
  returning content directly with search results.

  Returns:
    Tuple of (web_search_fn, web_fetch_fn) or (None, None) if unavailable.
  """
  try:
    client = ExaSearchClient(api_key)
  except (ImportError, ValueError) as e:
    print(f"Exa web search not available: {e}")
    return None, None

  # Cache for content retrieved during search
  _content_cache: dict[str, str] = {}

  def web_search(query: str) -> list[dict]:
    """
    Search with inline content retrieval.

    Results include content, which is also cached for fetch calls.
    """
    results = client.search_with_contents(query, num_results=5, text_length=3000)

    # Cache content for potential fetch calls
    for r in results:
      if r.get("content"):
        _content_cache[r["url"]] = r["content"]

    # Return in standard format (snippet = truncated content)
    return [
      {
        "url": r["url"],
        "title": r["title"],
        "snippet": r.get("content", "")[:500],  # First 500 chars as snippet
      }
      for r in results
    ]

  def web_fetch(url: str) -> str:
    """
    Fetch content, using cache if available.

    Content may already be cached from search_and_contents call.
    """
    if url in _content_cache:
      return _content_cache[url]

    contents = client.get_contents([url])
    content = contents.get(url, "")
    _content_cache[url] = content
    return content

  return web_search, web_fetch
