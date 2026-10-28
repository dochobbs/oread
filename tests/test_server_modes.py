"""
Server security and robustness (spec lane S).

Standalone vs platform auth, owner scoping, CORS, input validation, the
shared-Supabase-client fix, concurrency limits, and the timeline serializer.
No test touches a real Supabase project.
"""

from __future__ import annotations

import dataclasses
import inspect
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

import server
from src import runtime_config
from src.auth import AuthenticatedUser, require_user
from src.engines import PedsEngine
from src.models import GenerationSeed
from src.runtime_config import ConfigError, RuntimeConfig, check_config
from src.store import BoundedStore

GEN = {"age_months": 30, "use_llm": False}


def _cfg(mode: str, **kw) -> RuntimeConfig:
    base = RuntimeConfig(mode=mode, host="127.0.0.1", port=9104,
                         cors_origins=list(runtime_config.LOCAL_ORIGINS))
    return dataclasses.replace(base, **kw)


@pytest.fixture
def mode(monkeypatch):
    """Switch the server between modes for one test."""
    def _set(name: str, **kw):
        cfg = _cfg(name, **kw)
        monkeypatch.setattr(server, "RUNTIME", cfg)
        monkeypatch.setattr(runtime_config, "_active", cfg)
        return cfg
    return _set


@pytest.fixture
def client():
    server.app.dependency_overrides.clear()
    yield TestClient(server.app)
    server.app.dependency_overrides.clear()


def _as(user_id: str):
    server.app.dependency_overrides[require_user] = (
        lambda: AuthenticatedUser(id=user_id, email=f"{user_id}@test", token="t"))


# --------------------------------------------------------------------------- #
# Modes                                                                       #
# --------------------------------------------------------------------------- #

def test_standalone_generates_without_auth(mode, client):
    mode("standalone")
    r = client.post("/api/generate", json=GEN)
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.get(f"/api/patients/{pid}").status_code == 200


@pytest.mark.parametrize("method,path", [
    ("post", "/api/auth/login"),
    ("get", "/api/auth/me"),
    ("get", "/api/panels"),
    ("post", "/api/auth/logout"),
])
def test_standalone_platform_features_501(mode, client, method, path):
    mode("standalone")
    body = {"email": "a@b.co", "password": "x"} if path.endswith("login") else None
    r = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
    assert r.status_code == 501


@pytest.mark.parametrize("method,path", [
    ("post", "/api/generate"),
    ("post", "/api/generate/quick"),
    ("get", "/api/patients"),
    ("get", "/api/patients/abc"),
    ("get", "/api/patients/abc/context"),
    ("get", "/api/patients/abc/export/json"),
    ("delete", "/api/patients/abc"),
    ("get", "/api/stats"),
])
def test_platform_requires_token(mode, client, method, path):
    mode("platform")
    kwargs = {"json": GEN} if path == "/api/generate" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_platform_patients_are_owner_scoped(mode, client):
    mode("platform")
    _as("alice")
    pid = client.post("/api/generate", json=GEN).json()["id"]
    assert client.get(f"/api/patients/{pid}").status_code == 200

    _as("bob")
    assert client.get(f"/api/patients/{pid}").status_code == 404
    assert client.get(f"/api/patients/{pid}/context").status_code == 404
    assert client.delete(f"/api/patients/{pid}").status_code == 404
    assert pid not in [p["id"] for p in client.get("/api/patients").json()["patients"]]

    _as("alice")
    assert client.get(f"/api/patients/{pid}").status_code == 200


# --------------------------------------------------------------------------- #
# Config safety                                                               #
# --------------------------------------------------------------------------- #

def test_standalone_refuses_public_bind():
    with pytest.raises(ConfigError):
        check_config(_cfg("standalone"), host="0.0.0.0")
    check_config(_cfg("standalone"), host="127.0.0.1")
    check_config(_cfg("standalone"), host="localhost")


def test_platform_requires_supabase(monkeypatch):
    for k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ConfigError):
        check_config(_cfg("platform", host="0.0.0.0"))


def test_wildcard_cors_rejected():
    with pytest.raises(ConfigError):
        check_config(_cfg("standalone", cors_origins=["*"]))


def test_mode_defaults_from_supabase(monkeypatch):
    monkeypatch.delenv("OREAD_MODE", raising=False)
    for k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(k, raising=False)
    cfg = runtime_config.load_config()
    assert cfg.mode == "standalone" and cfg.host == "127.0.0.1"
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    assert runtime_config.load_config().mode == "platform"


# --------------------------------------------------------------------------- #
# CORS                                                                        #
# --------------------------------------------------------------------------- #

def _preflight(client, origin):
    return client.options("/api/generate", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST"})


def test_cors_rejects_unknown_origin(client):
    r = _preflight(client, "https://evil.example")
    assert r.headers.get("access-control-allow-origin") is None
    assert r.headers.get("access-control-allow-credentials") != "true"


def test_cors_allows_local_ui(client):
    r = _preflight(client, "http://localhost:9104")
    assert r.headers.get("access-control-allow-origin") == "http://localhost:9104"


# --------------------------------------------------------------------------- #
# Input validation: 4xx, never 500                                            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body", [
    {"age_months": 100000},
    {"sex": "banana"},
    {"complexity_tier": "tier-9"},
    {"specialty": "dermatology"},
    {"encounter_count": 100000},
])
def test_generate_rejects_bad_input(mode, client, body):
    mode("standalone")
    assert client.post("/api/generate", json=body).status_code == 422


# --------------------------------------------------------------------------- #
# Shared Supabase client                                                      #
# --------------------------------------------------------------------------- #

def test_login_never_signs_in_on_shared_client(mode, client, monkeypatch):
    """Login must use a fresh client, or every later query runs as this user."""
    mode("platform")
    monkeypatch.setattr(server, "db_configured", lambda: True)

    def boom():
        raise AssertionError("shared Supabase client used during login")
    monkeypatch.setattr("src.db.client.get_client", boom)

    calls = []

    class FakeAuth:
        def sign_in(self, email, password):
            calls.append(email)
            user = type("U", (), {"id": "u1"})()
            session = type("S", (), {"access_token": "a", "refresh_token": "r"})()
            return type("R", (), {"user": user, "session": session})()

        def table(self, name):
            raise RuntimeError("no db in test")

    monkeypatch.setattr(server, "new_anon_client", FakeAuth)
    r = client.post("/api/auth/login", json={"email": "a@b.co", "password": "pw"})
    assert r.status_code == 200, r.text
    assert calls == ["a@b.co"]


# --------------------------------------------------------------------------- #
# Concurrency                                                                 #
# --------------------------------------------------------------------------- #

def test_generation_handlers_run_in_threadpool():
    """Sync handlers run off the event loop, so generation can't freeze /health."""
    for fn in (server.generate_patient, server.generate_panel_patients,
               server.generate_encounter, server.get_patient_timeline):
        assert not inspect.iscoroutinefunction(fn), fn.__name__


def test_generator_busy_returns_503(mode, client, monkeypatch):
    mode("standalone", generation_queue_timeout_s=1)
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(server, "_generation_slots", slots)
    slots.acquire()
    try:
        assert client.post("/api/generate", json=GEN).status_code == 503
    finally:
        slots.release()


# --------------------------------------------------------------------------- #
# Store                                                                       #
# --------------------------------------------------------------------------- #

def test_bounded_store_evicts_lru_and_scopes_owner():
    s: BoundedStore[str] = BoundedStore(2)
    s.put("a", "A", "u1")
    s.put("b", "B", "u1")
    assert s.get("a", "u1") == "A"  # refresh a
    s.put("c", "C", "u1")           # evicts b
    assert s.get("b", "u1") is None
    assert s.get("a", "u2") is None
    assert not s.delete("a", "u2")
    assert s.delete("a", "u1")
    assert len(s) == 1


# --------------------------------------------------------------------------- #
# Timeline                                                                    #
# --------------------------------------------------------------------------- #

def test_snapshot_serializer_handles_medications():
    """Regression: /timeline/at/{age} read m.name / m.dosage, which don't exist."""
    engine = PedsEngine(use_llm=False)
    patient = engine.generate(GenerationSeed(age_months=60, conditions=["asthma"], random_seed=4))
    snapshot, _ = engine.get_snapshot_at_age(patient=patient, age_months=59)
    out = server._snapshot_to_dict(snapshot)
    assert out["medications"], "expected an asthma patient to have medications"
    assert all(set(m) == {"name", "dosage"} for m in out["medications"])
