"""Shared fixtures for the Room Climate test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
def hive_entry(hass: HomeAssistant) -> MockConfigEntry:
    """The entry that owns the valves of the house."""
    entry = MockConfigEntry(domain="mqtt", title="Zigbee2MQTT")
    entry.add_to_hass(hass)
    return entry


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Iterator[None]:
    """Enable the integrations under custom_components for every test."""
    yield
