"""
In-memory, size-bounded, owner-scoped storage for generated patients and jobs.

Patients live only in process memory: a restart loses them, and only one
worker process sees them. Persistence is a separate follow-up (spec D3).
"""

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Owned(Generic[T]):
  """A stored value tagged with the user who created it."""

  value: T
  owner_id: str


class BoundedStore(Generic[T]):
  """
  LRU-evicting map of id -> Owned[T].

  Reads by an owner refresh recency. When full, the least recently used
  entry is evicted. All operations are thread-safe.
  """

  def __init__(self, max_items: int):
    if max_items < 1:
      raise ValueError("max_items must be >= 1")
    self._max = max_items
    self._items: OrderedDict[str, Owned[T]] = OrderedDict()
    self._lock = threading.Lock()

  def put(self, key: str, value: T, owner_id: str) -> None:
    with self._lock:
      self._items[key] = Owned(value, owner_id)
      self._items.move_to_end(key)
      while len(self._items) > self._max:
        self._items.popitem(last=False)

  def get(self, key: str, owner_id: str) -> T | None:
    """Return the value if it exists and belongs to owner_id, else None."""
    with self._lock:
      item = self._items.get(key)
      if item is None or item.owner_id != owner_id:
        return None
      self._items.move_to_end(key)
      return item.value

  def delete(self, key: str, owner_id: str) -> bool:
    """Delete if owned by owner_id. Returns True if something was deleted."""
    with self._lock:
      item = self._items.get(key)
      if item is None or item.owner_id != owner_id:
        return False
      del self._items[key]
      return True

  def values_for(self, owner_id: str) -> list[T]:
    with self._lock:
      return [i.value for i in self._items.values() if i.owner_id == owner_id]

  def __len__(self) -> int:
    with self._lock:
      return len(self._items)
