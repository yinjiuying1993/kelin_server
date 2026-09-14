from alembic.config import Config

from app.core.config import get_settings


def alembic_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_async_url())
    return cfg
