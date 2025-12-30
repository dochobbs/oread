"""Disk cache for dynamically-retrieved condition knowledge."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any


class ConditionCache:
  """
  Persistent cache for condition definitions retrieved via web search.

  Stores JSON files on disk with TTL-based expiration.
  """

  DEFAULT_TTL_DAYS = 30  # Cache entries valid for 30 days

  def __init__(self, cache_dir: Path, ttl_days: int = DEFAULT_TTL_DAYS):
    self.cache_dir = cache_dir
    self.ttl = timedelta(days=ttl_days)
    self.cache_dir.mkdir(parents=True, exist_ok=True)

  def _get_cache_key(self, condition_name: str) -> str:
    """Generate a filesystem-safe cache key."""
    normalized = condition_name.lower().strip()
    hash_suffix = hashlib.md5(normalized.encode()).hexdigest()[:8]
    safe_name = "".join(c if c.isalnum() else "_" for c in normalized)[:50]
    return f"{safe_name}_{hash_suffix}"

  def _get_cache_path(self, condition_name: str) -> Path:
    return self.cache_dir / f"{self._get_cache_key(condition_name)}.json"

  def get(self, condition_name: str) -> dict | None:
    """Get cached condition definition if valid."""
    path = self._get_cache_path(condition_name)

    if not path.exists():
      return None

    try:
      with open(path, 'r') as f:
        data = json.load(f)

      # Check TTL
      cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
      if datetime.now() - cached_at > self.ttl:
        path.unlink()  # Expired, delete
        return None

      return data.get("definition")
    except (json.JSONDecodeError, KeyError):
      return None

  def set(self, condition_name: str, definition: dict) -> None:
    """Cache a condition definition."""
    path = self._get_cache_path(condition_name)

    data = {
      "_cached_at": datetime.now().isoformat(),
      "_condition_name": condition_name,
      "definition": definition,
    }

    with open(path, 'w') as f:
      json.dump(data, f, indent=2)

  def invalidate(self, condition_name: str) -> bool:
    """Remove a cached entry."""
    path = self._get_cache_path(condition_name)
    if path.exists():
      path.unlink()
      return True
    return False

  def clear_all(self) -> int:
    """Clear entire cache. Returns count of entries removed."""
    count = 0
    for path in self.cache_dir.glob("*.json"):
      path.unlink()
      count += 1
    return count
