"""Settings as a FastAPI dependency.

The app factory stores the Settings it was built with on ``app.state``; dependencies read them
from the request. Tests can therefore build an app with any Settings object, with no global
state and no ``dependency_overrides`` needed for configuration.
"""

from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings


def get_request_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_request_settings)]
