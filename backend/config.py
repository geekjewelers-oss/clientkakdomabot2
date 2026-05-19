from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/rent_reg"
    DIRECTUS_URL: str = "http://localhost:8055"

    S3_ENDPOINT: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "files-media"

    OCR_URL: str = "http://localhost:8010"
    OCR_TIMEOUT_SECONDS: int = 30

    OCR_SPACE_API_KEY: str = ""
    YANDEX_VISION_API_KEY: str = ""
    YANDEX_FOLDER_ID: str = ""
    MIN_CONFIDENCE: float = 0.85
    OCR_FALLBACK_ENABLED: bool = True

    JWT_SECRET: str = "change-me"


settings = Settings()
