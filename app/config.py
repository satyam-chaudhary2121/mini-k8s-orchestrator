from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://orchestrator:orchestrator@localhost:5432/orchestrator"


settings = Settings()