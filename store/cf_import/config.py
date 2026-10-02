from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodeforcesOptions(Options):
    handle: str = ""
    api_url: str = "https://codeforces.com/api"
    status_url: str = ""  # Optional user.status-compatible endpoint.
    status_token_env: str = "CF_STATUS_TOKEN"
    interval_seconds: float = Field(default=2.1, ge=2.0)
    statement_interval_seconds: float = Field(default=3.0, ge=2.0)
    page_size: int = Field(default=1000, ge=1, le=10000)
    max_pages: int = Field(default=1000, ge=1)


class LLMOptions(Options):
    base_url: str = ""
    api_key_env: str = "CF_IMPORT_LLM_API_KEY"
    model: str = ""
    api_mode: str = "chat_completions"
    json_mode: bool = True
    attempts: int = Field(default=3, ge=1, le=3)
    repair_attempts: int = Field(default=1, ge=0, le=2)
    timeout_seconds: float = Field(default=180, gt=0)


class MiniOJOptions(Options):
    base_url: str = "http://127.0.0.1:8080/minioj"
    token_env: str = "CF_IMPORT_MINIOJ_TOKEN"
    poll_seconds: float = Field(default=1.0, ge=0.1)
    verification_timeout_seconds: float = Field(default=600, gt=0)


class SandboxOptions(Options):
    image: str = "minioj-cpp20:latest"
    seed: int = Field(default=20261001, ge=0, le=2**63 - 1)
    time_limit_ms: int = Field(default=10000, ge=100, le=60000)
    memory_mb: int = Field(default=512, ge=16, le=2048)
    file_limit_bytes: int = Field(default=8 * 1024 * 1024, gt=0)
    total_limit_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_tests: int = Field(default=30, ge=6, le=50)


class Config(Options):
    source_dir: Path = ROOT / "store/codeforces"
    generated_dir: Path = ROOT / "store/generated/codeforces"
    cache_dir: Path = ROOT / "store/cf_import/cache"
    state_file: Path = ROOT / "store/cf_import/state.json"
    secrets_file: Path = ROOT / "store/cf_import/secrets.env"
    codeforces: CodeforcesOptions = Field(default_factory=CodeforcesOptions)
    llm: LLMOptions = Field(default_factory=LLMOptions)
    minioj: MiniOJOptions = Field(default_factory=MiniOJOptions)
    sandbox: SandboxOptions = Field(default_factory=SandboxOptions)
    references: dict[str, str] = Field(default_factory=dict)


def load_config(path: Path) -> Config:
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    cfg = Config.model_validate(data)
    for name in (
        "source_dir",
        "generated_dir",
        "cache_dir",
        "state_file",
        "secrets_file",
    ):
        value = getattr(cfg, name).expanduser()
        setattr(
            cfg,
            name,
            value.resolve() if value.is_absolute() else (ROOT / value).resolve(),
        )
    if cfg.secrets_file.exists():
        # Parse allowlisted literal assignments; never source a shell or import the
        # server's session/database settings into generated programs.
        allowed = {
            cfg.llm.api_key_env,
            cfg.minioj.token_env,
            cfg.codeforces.status_token_env,
        }
        for line in cfg.secrets_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or key not in allowed:
                raise ValueError(
                    "secrets.env must contain only configured credential variable assignments"
                )
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            if value and not os.getenv(key):
                os.environ[key] = value
    cfg.codeforces.handle = os.getenv("CF_HANDLE", cfg.codeforces.handle)
    cfg.llm.base_url = os.getenv("CF_IMPORT_LLM_BASE_URL", cfg.llm.base_url)
    cfg.llm.model = os.getenv("CF_IMPORT_LLM_MODEL", cfg.llm.model)
    cfg.minioj.base_url = os.getenv("CF_IMPORT_MINIOJ_URL", cfg.minioj.base_url)
    if cfg.llm.api_mode not in {"chat_completions", "responses"}:
        raise ValueError("llm.api_mode must be chat_completions or responses")
    return cfg
