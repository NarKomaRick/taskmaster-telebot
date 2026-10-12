from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    bot_token: str
    bot_username: str = ''
    bot_proxy: str = ''
    app_url: str = ''
    base_path: str = ''
    database_url: str
    redis_url: str = 'redis://redis:6379/0'
    secret_key: str
    encryption_key: str
    attachment_dir: str = '/data/attachments'
    timezone: str = 'Europe/Moscow'


@lru_cache
def settings():
    return Settings()
