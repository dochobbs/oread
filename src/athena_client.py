from __future__ import annotations

import httpx


class AthenaClient:
  """HTTP client for the Athena knowledge service.

  Used by other MedEd services (Oread, Echo, Syrinx, Mneme) to query
  conditions, frameworks, specialties, and learner tracks.

  Falls back gracefully if Athena is unreachable — returns empty lists
  or None instead of raising exceptions.
  """

  def __init__(self, base_url: str = "http://localhost:9105"):
    self._base_url = base_url
    self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

  async def close(self):
    await self._client.aclose()

  async def _get(self, path: str, params: dict | None = None) -> dict | list | None:
    try:
      params = {k: v for k, v in (params or {}).items() if v is not None}
      response = await self._client.get(path, params=params)
      response.raise_for_status()
      return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
      return None

  async def health_check(self) -> bool:
    result = await self._get("/api/health")
    return result is not None and result.get("status") == "healthy"

  async def get_conditions(
    self,
    specialty: str | None = None,
    age_months: int | None = None,
    level: str | None = None,
    system: str | None = None,
  ) -> list[dict]:
    result = await self._get("/api/conditions", params={
      "specialty": specialty,
      "age_months": age_months,
      "level": level,
      "system": system,
    })
    return result if isinstance(result, list) else []

  async def get_condition(self, condition_id: str) -> dict | None:
    return await self._get(f"/api/conditions/{condition_id}")

  async def get_frameworks(
    self,
    specialty: str | None = None,
    category: str | None = None,
  ) -> list[dict]:
    result = await self._get("/api/frameworks", params={
      "specialty": specialty,
      "category": category,
    })
    return result if isinstance(result, list) else []

  async def get_framework(self, framework_id: str) -> dict | None:
    return await self._get(f"/api/frameworks/{framework_id}")

  async def get_framework_for_condition(
    self,
    condition: str,
    specialty: str,
    level: str | None = None,
  ) -> dict | None:
    return await self._get(
      f"/api/frameworks/for-condition/{condition}",
      params={"specialty": specialty, "level": level},
    )

  async def get_specialties(self) -> list[dict]:
    result = await self._get("/api/specialties")
    return result if isinstance(result, list) else []

  async def get_specialty(self, specialty_id: str) -> dict | None:
    return await self._get(f"/api/specialties/{specialty_id}")

  async def get_learner_tracks(
    self,
    specialty: str | None = None,
    level: str | None = None,
  ) -> list[dict]:
    result = await self._get("/api/learner-tracks", params={
      "specialty": specialty,
      "level": level,
    })
    return result if isinstance(result, list) else []

  async def get_disease_arcs(
    self,
    specialty: str | None = None,
  ) -> list[dict]:
    result = await self._get("/api/disease-arcs", params={
      "specialty": specialty,
    })
    return result if isinstance(result, list) else []

  async def get_immunizations(
    self,
    age_months: int | None = None,
  ) -> list[dict]:
    result = await self._get("/api/immunizations", params={
      "age_months": age_months,
    })
    return result if isinstance(result, list) else []
