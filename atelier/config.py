from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="ATELIER_",
        # Ignore unrelated keys in .env (e.g. raw DISCORD_TOKEN used by
        # scripts/mj_spike.py) so they don't crash app startup.
        extra="ignore",
    )

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'atelier.db'}"
    image_dir: Path = PROJECT_ROOT / "data" / "images"
    knowledge_dir: Path = PROJECT_ROOT / "knowledge"

    # Midjourney via Discord (v2). Off by default; the rest of the app
    # works fine without these. When enabled, requires `pip install
    # 'atelier[mj]'` and a personal Discord server with MJ installed.
    mj_enabled: bool = False
    mj_discord_token: str = ""
    mj_channel_id: int = 0
    mj_guild_id: int = 0
    mj_timeout_seconds: float = 300
