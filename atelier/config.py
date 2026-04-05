from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="ATELIER_",
    )

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'atelier.db'}"
    image_dir: Path = PROJECT_ROOT / "data" / "images"
    knowledge_dir: Path = PROJECT_ROOT / "knowledge"
