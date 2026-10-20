"""The coordinator reads states, decides, and applies the commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.const import (
    ATTR_DOMAIN,
    ATTR_SERVICE,
    ATTR_SERVICE_DATA,
    EVENT_CALL_SERVICE,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN
from custom_components.room_climate.models import RuntimeRoom

SERVICE_DOMAINS = ("climate", "number", "switch")

RECORDED: dict[HomeAssistant, list[dict[str, Any]]] = {}


async def enable_services(hass: HomeAssistant) -> None:
    """Load the service domains the coordinator commands, and record every call."""
    for domain in SERVICE_DOMAINS:
        await async_setup_component(hass, domain, {})
    RECORDED[hass] = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, _recorder(hass))


def _recorder(hass: HomeAssistant) -> Callable[[Event[Any]], None]:
    """Return a listener that appends every service call of one instance."""
    calls = RECORDED[hass]

    def record(event: Event[Any]) -> None:
        calls.append(dict(event.data))

    return record


def calls(hass: HomeAssistant, domain: str, service: str) -> list[dict[str, Any]]:
    """Return the service data of every recorded call of one service."""
    return [
        dict(record[ATTR_SERVICE_DATA])
        for record in RECORDED.get(hass, [])
        if record.get(ATTR_DOMAIN) == domain and record.get(ATTR_SERVICE) == service
    ]


def calls_for(hass: HomeAssistant, service: str, entity_id: str) -> list[dict[str, Any]]:
    """Return the recorded climate service calls that name one entity."""
    return [
        service_data
        for service_data in calls(hass, "climate", service)
        if service_data.get("entity_id") == entity_id
    ]


def options(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ["sensor.outdoor"],
        "rooms": [
            {
                "room_id": "front_room",
                "name": "Front Room",
                "valves": ["climate.trv_front_room"],
                "sources": [{"entity_id": "sensor.front_room_temperature"}],
            }
        ],
    }
    payload.update(overrides)
    return payload


async def setup_entry(hass: HomeAssistant, **overrides: object) -> MockConfigEntry:
    await enable_services(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Room Climate", options=options(**overrides))
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.front_room_temperature", "20.0")
    hass.states.async_set("sensor.outdoor", "8.0")
    hass.states.async_set("climate.trv_front_room", "heat", {"temperature": 5.0})
    hass.states.async_set("number.trv_front_room_external_measured_room_sensor", "-8000")
    hass.states.async_set("climate.heating_heat", "off", {"temperature": 15.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def set_room_target(entry: MockConfigEntry, room_id: str, temperature: float) -> None:
    """Set the setpoint of one room through the coordinator, without the climate entity."""
    coordinator = entry.runtime_data
    coordinator.runtimes[room_id] = RuntimeRoom(
        room_id=room_id, target_temperature=temperature, hvac_mode="heat"
    )
    await coordinator.async_recompute()


async def test_a_call_for_heat_commands_the_valve_and_the_boiler(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, dry_run=False)
    await set_room_target(entry, "front_room", 21.0)
    await hass.async_block_till_done()

    boiler = calls_for(hass, "set_temperature", "climate.heating_heat")
    assert boiler, "the boiler must be commanded"
    assert boiler[-1]["temperature"] == 31.0
    assert boiler[-1]["hvac_mode"] == "heat"

    valve = calls_for(hass, "set_temperature", "climate.trv_front_room")
    assert [call["temperature"] for call in valve][-1] == 21.0


async def test_the_calibration_feed_is_written(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=False)
    writes = calls(hass, "number", "set_value")
    assert any(call.get("value") == 2000 for call in writes)


async def test_dry_run_sends_no_command(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, dry_run=True)
    await set_room_target(entry, "front_room", 21.0)
    await hass.async_block_till_done()
    assert calls_for(hass, "set_temperature", "climate.trv_front_room") == []
    assert calls_for(hass, "set_temperature", "climate.heating_heat") == []
    assert calls(hass, "number", "set_value") == []
    assert entry.runtime_data.output is not None


async def test_an_unavailable_valve_does_not_stop_the_other_valve(hass: HomeAssistant) -> None:
    await enable_services(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate",
        options=options(
            dry_run=False,
            rooms=[
                {
                    "room_id": "side_room",
                    "name": "Side Room",
                    "valves": ["climate.trv_side_room_a", "climate.trv_side_room_b"],
                    "sources": [{"entity_id": "sensor.side_room_temperature"}],
                }
            ],
        ),
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.side_room_temperature", "19.0")
    hass.states.async_set("sensor.outdoor", "8.0")
    hass.states.async_set("climate.trv_side_room_a", "unavailable")
    hass.states.async_set("climate.trv_side_room_b", "heat", {"temperature": 5.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await set_room_target(entry, "side_room", 21.0)
    await hass.async_block_till_done()
    assert calls_for(hass, "set_temperature", "climate.trv_side_room_b")
    assert calls_for(hass, "set_temperature", "climate.trv_side_room_a") == []
