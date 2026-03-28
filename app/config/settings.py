from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
     OPENAI_API_KEY: str
     MONGODB_URI: str 
     MONGODB_DB_NAME: str 
     
     class Config:
          env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
     return Settings()
     