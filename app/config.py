from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    qiscus_app_id: str = ""
    qiscus_secret_key: str = ""
    qiscus_base_url: str = "https://multichannel.qiscus.com"

    default_max_capacity: int = 2
    poll_interval_seconds: int = 30

    database_url: str = "sqlite:///./allocation.db"

    class Config:
        env_file = ".env"


settings = Settings()
