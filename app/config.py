from enum import Enum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RadioUnity(str, Enum):
    """
    Valid units for the GEOSEARCH search radius. The values (M/KM/FT/MI) are exactly
    what Redis expects in the `unit` parameter — they're not arbitrary, don't change
    them without checking that Redis still accepts them.
    """
    METERS = 'M'
    KILOMETERS = 'KM'
    FEET = 'FT'
    MILES = 'MI'


class Settings(BaseSettings):
    """
    Config loaded from environment variables (and .env locally). Instantiated once
    (`settings`, at the bottom of this file) and that same instance is imported
    everywhere else — don't create a new Settings() in another module.
    """
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    debug: bool = Field(False)  # enables /docs and lowers logging to DEBUG

    redis_url: str
    redis_timeout_seconds: float = Field(5.0)  # fail fast instead of hanging
    location_key: str        # Redis key where GEOADD/GEOSEARCH store locations
    radius_alert: int        # alert radius, in the unit given by radius_unity
    radius_unity: RadioUnity

    connection_str: str      # Service Bus connection string (emulator/Azure)
    queue_name: str          # queue where notifications get published


settings = Settings()
