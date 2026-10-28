"""
Server runtime configuration: standalone vs platform mode.

Standalone: no Supabase, no login. Binds loopback only, because nothing
authenticates requests. Panels and accounts are unavailable.

Platform: Supabase-backed auth. Every patient route requires a bearer token
(Metis sends one on every Oread call). Safe to bind a public interface.

The mode defaults to platform when Supabase is configured, standalone
otherwise. Override with OREAD_MODE.
"""

import ipaddress
import os
from dataclasses import dataclass, field

STANDALONE = "standalone"
PLATFORM = "platform"

# Oread's own UI and the Metis dev portal. Metis in production proxies Oread
# same-origin through Caddy, so it needs no CORS entry.
LOCAL_ORIGINS = [
  "http://localhost:9104",
  "http://127.0.0.1:9104",
  "http://localhost:9100",
  "http://127.0.0.1:9100",
]


class ConfigError(RuntimeError):
  """Raised when the runtime configuration is unsafe or inconsistent."""


def supabase_configured() -> bool:
  """True when Supabase URL and anon key are both set (either naming)."""
  url = os.environ.get("SUPABASE_URL")
  key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
  return bool(url and key)


def is_loopback(host: str) -> bool:
  """True for localhost and loopback IPs."""
  if host == "localhost":
    return True
  try:
    return ipaddress.ip_address(host).is_loopback
  except ValueError:
    return False


def _int_env(name: str, default: int) -> int:
  raw = os.environ.get(name)
  if not raw:
    return default
  try:
    value = int(raw)
  except ValueError as exc:
    raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
  if value < 1:
    raise ConfigError(f"{name} must be >= 1, got {value}")
  return value


@dataclass(frozen=True)
class RuntimeConfig:
  """Resolved server configuration."""

  mode: str
  host: str
  port: int
  cors_origins: list[str] = field(default_factory=list)
  max_patients: int = 500
  max_jobs: int = 200
  max_concurrent_generations: int = 4
  generation_queue_timeout_s: int = 30

  @property
  def is_platform(self) -> bool:
    return self.mode == PLATFORM


def load_config() -> RuntimeConfig:
  """Build the config from environment variables."""
  mode = (os.environ.get("OREAD_MODE") or "").strip().lower()
  if not mode:
    mode = PLATFORM if supabase_configured() else STANDALONE
  if mode not in (STANDALONE, PLATFORM):
    raise ConfigError(f"OREAD_MODE must be '{STANDALONE}' or '{PLATFORM}', got {mode!r}")

  default_host = "127.0.0.1" if mode == STANDALONE else "0.0.0.0"
  raw_origins = os.environ.get("OREAD_CORS_ORIGINS")
  if raw_origins is not None:
    origins = [o.strip() for o in raw_origins.split(",") if o.strip()]
  else:
    origins = list(LOCAL_ORIGINS)

  return RuntimeConfig(
    mode=mode,
    host=os.environ.get("OREAD_HOST") or default_host,
    port=_int_env("OREAD_PORT", 9104),
    cors_origins=origins,
    max_patients=_int_env("OREAD_MAX_PATIENTS", 500),
    max_jobs=_int_env("OREAD_MAX_JOBS", 200),
    max_concurrent_generations=_int_env("OREAD_MAX_CONCURRENT_GENERATIONS", 4),
    generation_queue_timeout_s=_int_env("OREAD_GENERATION_QUEUE_TIMEOUT", 30),
  )


def check_config(config: RuntimeConfig, host: str | None = None) -> None:
  """
  Refuse unsafe combinations.

  Args:
    config: The resolved configuration.
    host: The interface actually being bound, if known (defaults to config.host).

  Raises:
    ConfigError: If standalone would be exposed beyond loopback, or platform
      mode has no Supabase to authenticate against.
  """
  bind = host or config.host
  if config.mode == STANDALONE and not is_loopback(bind):
    raise ConfigError(
      f"Standalone mode has no authentication; refusing to bind {bind}. "
      "Bind 127.0.0.1, or set OREAD_MODE=platform with Supabase configured."
    )
  if config.mode == PLATFORM and not supabase_configured():
    raise ConfigError(
      "Platform mode requires SUPABASE_URL and SUPABASE_ANON_KEY. "
      "Configure Supabase or run with OREAD_MODE=standalone."
    )
  if "*" in config.cors_origins:
    raise ConfigError("OREAD_CORS_ORIGINS may not contain '*'; list origins explicitly.")


# Process-wide active config. server.py sets it at import; tests may swap it.
_active: RuntimeConfig | None = None


def current() -> RuntimeConfig:
  """Return the active config, loading it from the environment on first use."""
  global _active
  if _active is None:
    _active = load_config()
  return _active


def set_current(config: RuntimeConfig) -> None:
  """Replace the active config (server startup and tests)."""
  global _active
  _active = config
