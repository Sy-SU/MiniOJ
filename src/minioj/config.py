from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_ROOT.parents[1]
PROJECT_ROOT = SOURCE_ROOT if (SOURCE_ROOT / "pyproject.toml").is_file() else Path.cwd()


def _path_from_env(name: str, default: Path) -> Path:
    value = os.getenv(name)
    path = Path(value).expanduser() if value else default
    return path if path.is_absolute() else PROJECT_ROOT / path


def _root_path_from_env() -> str:
    value = os.getenv("MINIOJ_ROOT_PATH", "").strip()
    if not value or value == "/":
        return ""
    return "/" + value.strip("/")


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    database_url: str = os.getenv(
        "MINIOJ_DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'database' / 'oj.db'}"
    )
    data_dir: Path = field(
        default_factory=lambda: _path_from_env("MINIOJ_DATA_DIR", PROJECT_ROOT / "data")
    )
    secret_key: str = os.getenv(
        "MINIOJ_SECRET_KEY", "development-only-change-this-secret-key"
    )
    root_path: str = field(default_factory=_root_path_from_env)
    docker_image: str = os.getenv("MINIOJ_DOCKER_IMAGE", "minioj-cpp20:latest")
    feedback_policy: str = os.getenv("MINIOJ_FEEDBACK_POLICY", "full")
    session_https_only: bool = os.getenv(
        "MINIOJ_SESSION_HTTPS_ONLY", "false"
    ).lower() in {
        "1",
        "true",
        "yes",
    }
    output_limit_bytes: int = int(os.getenv("MINIOJ_OUTPUT_LIMIT_BYTES", "1048576"))
    source_limit_bytes: int = int(os.getenv("MINIOJ_SOURCE_LIMIT_BYTES", "262144"))
    stdin_limit_bytes: int = int(os.getenv("MINIOJ_STDIN_LIMIT_BYTES", "262144"))
    token_default_days: int = int(os.getenv("MINIOJ_TOKEN_DEFAULT_DAYS", "90"))

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def problems_dir(self) -> Path:
        return self.data_dir / "problems"

    @property
    def templates_dir(self) -> Path:
        packaged = PACKAGE_ROOT / "templates"
        return packaged if packaged.is_dir() else self.project_root / "templates"

    @property
    def static_dir(self) -> Path:
        packaged = PACKAGE_ROOT / "static"
        return packaged if packaged.is_dir() else self.project_root / "static"


settings = Settings()
