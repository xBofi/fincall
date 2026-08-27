"""Configuración de infraestructura y secretos.

Solo variables de entorno (`.env` en local, variables/secretos de CI en despliegue).
La configuración de negocio (fincas, especialistas, reglas) NO va aquí: vive en PostgreSQL.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BC_", env_file=".env", extra="ignore")

    # "memoria" permite validar los agentes sin base de datos; "postgres" es el modo real.
    repositorio: str = "memoria"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/building_call"

    ollama_url: str = "http://localhost:11434"
    ollama_modelo: str = "qwen3.5:4b"
    ollama_timeout_s: float = 120.0

    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
