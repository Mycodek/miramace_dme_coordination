from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # gemini | openai | fake
    llm_provider: str = "gemini"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    pcp_max_attempts: int = 3
    supplier_max_attempts: int = 2
    max_concurrent_supplier_contacts: int = 3
    suppliers_csv: Path = DATA_DIR / "suppliers.csv"
    patients_json: Path = DATA_DIR / "patients.json"
    scenarios_dir: Path = DATA_DIR / "scenarios"
    cases_db: Path = DATA_DIR / "cases.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
