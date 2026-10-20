"""The config flow of the integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class RoomClimateConfigFlow(ConfigFlow, domain=DOMAIN):
    """The house entry of one installation."""

    VERSION = 1
