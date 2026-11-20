"""A room is a subentry of the house, added from the discovered valves and sensors."""

from __future__ import annotations

from typing import Any

import probatio
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pydantic import ValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.config_flow import (
    RoomClimateConfigFlow,
    RoomSubentryFlow,
)
from custom_components.room_climate.const import DOMAIN
from custom_components.room_climate.models import HouseConfig

from .test_coordinator import enable_services
from .test_discovery import add_device, add_entity, add_valve

OPTIONS: dict[str, Any] = {
    "boiler_entity": "climate.heating_heat",
    "outdoor_sources": ["sensor.outdoor"],
}

ROOM_FIELDS: tuple[str, ...] = (
    "name",
    "valves",
    "area_id",
    "calibration",
    "calibration_scale",
    "valve_mode",
    "open_offset",
    "min_temp",
    "max_temp",
    "hysteresis",
    "off_setpoint",
    "valve_command_interval_seconds",
    "window_sensors",
    "demand_fallback_sensors",
    "outdoor_sources",
    "lockout_temperature",
)


def room_subentry(room_id: str, valves: list[str], *, name: str | None = None) -> dict[str, object]:
    """One room subentry of the entry."""
    title = name or room_id.replace("_", " ").title()
    return {
        "data": {
            "room_id": room_id,
            "name": title,
            "valves": list(valves),
            "sources": [{"entity_id": f"sensor.{room_id}_temperature"}],
        },
        "subentry_type": "room",
        "title": title,
        "unique_id": None,
    }


def first_form(name: str, valves: list[str], **overrides: object) -> dict[str, object]:
    """The answer of the first room step: the name, the valves and the tuning."""
    payload: dict[str, object] = {"name": name, "valves": list(valves)}
    payload.update(overrides)
    return payload


def add_sensor(hass: HomeAssistant, object_id: str, *, name: str | None = None) -> str:
    """Add one temperature sensor of the house, and return its entity id."""
    return add_entity(
        hass, "sensor", object_id, original_device_class="temperature", original_name=name
    )


async def setup_house(
    hass: HomeAssistant, *, rooms: list[dict[str, object]] | None = None, **overrides: Any
) -> MockConfigEntry:
    """One loaded house entry, with the given rooms as its subentries."""
    await enable_services(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate",
        options={**OPTIONS, **overrides},
        subentries_data=rooms if rooms is not None else [],
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.outdoor", "8.0")
    hass.states.async_set("climate.heating_heat", "off", {"temperature": 15.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def start_user_step(hass: HomeAssistant, entry: MockConfigEntry):
    """Start the room flow of one house, from the integration page."""
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, "room"), context={"source": "user"}
    )


async def submit(hass: HomeAssistant, flow_id: str, payload: dict[str, object]):
    """Submit one room form result to the flow it belongs to."""
    return await hass.config_entries.subentries.async_configure(flow_id, payload)


async def add_room(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    name: str,
    valves: list[str],
    sources: list[str],
    weights: dict[str, float] | None = None,
    **overrides: object,
):
    """Add one room through the whole room flow, and let the reload finish."""
    result = await start_user_step(hass, entry)
    flow_id = result["flow_id"]
    result = await submit(hass, flow_id, first_form(name, valves, **overrides))
    assert result["step_id"] == "sources", result
    result = await submit(hass, flow_id, {"sources": list(sources)})
    if weights is not None:
        assert result["step_id"] == "weights", result
        result = await submit(hass, flow_id, weights)
    await hass.async_block_till_done()
    return result


def form_fields(result) -> list[str]:
    """The field names of one shown form."""
    return list(result["data_schema"].schema)


def offered_valves(result) -> list[str]:
    """The valve choices of one shown form."""
    validator = result["data_schema"].schema["valves"]
    return [option["value"] for option in validator.config["options"]]


def offered_sources(result) -> list[str]:
    """The source choices of one shown form."""
    validator = result["data_schema"].schema["sources"]
    return [option["value"] for option in validator.config["options"]]


def test_the_flow_supports_the_room_subentry() -> None:
    entry = MockConfigEntry(domain=DOMAIN, options={})
    assert RoomClimateConfigFlow.async_get_supported_subentry_types(entry) == {
        "room": RoomSubentryFlow
    }


async def test_the_room_form_renders_for_the_frontend(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    """The panel serializes the first step before it renders it, and a nested schema throws."""
    add_valve(hass, hive_entry, "trv_front_room")
    entry = await setup_house(hass)
    result = await start_user_step(hass, entry)
    fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    names = [field["name"] for field in fields]
    assert names == list(ROOM_FIELDS)
    valves = next(field for field in fields if field["name"] == "valves")
    assert valves["selector"]["select"]["options"] == [
        {"value": "climate.trv_front_room", "label": "climate.trv_front_room"}
    ]


async def test_the_source_picker_renders_for_the_frontend(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_sensor(hass, "front_room_temperature", name="Room Sensor")
    entry = await setup_house(hass)
    result = await start_user_step(hass, entry)
    result = await submit(
        hass, result["flow_id"], first_form("Front Room", ["climate.trv_front_room"])
    )
    assert result["step_id"] == "sources"
    fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    source_field = next(field for field in fields if field["name"] == "sources")
    options = source_field["selector"]["select"]["options"]
    assert options[0] == {"value": "sensor.front_room_temperature", "label": "Room Sensor"}


async def test_the_user_step_offers_exactly_the_candidates(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_valve(hass, hive_entry, "trv_back_room")
    entry = await setup_house(hass)
    result = await start_user_step(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert offered_valves(result) == ["climate.trv_back_room", "climate.trv_front_room"]


async def test_the_user_step_refuses_a_valve_that_is_not_a_candidate(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    entry = await setup_house(hass)
    result = await start_user_step(hass, entry)
    assert "climate.heating_heat" not in offered_valves(result)
    with pytest.raises(InvalidData):
        await submit(hass, result["flow_id"], first_form("Front Room", ["climate.heating_heat"]))


async def test_the_user_step_refuses_a_valve_another_room_claims(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_valve(hass, hive_entry, "trv_back_room")
    entry = await setup_house(hass, rooms=[room_subentry("front_room", ["climate.trv_front_room"])])
    result = await start_user_step(hass, entry)
    assert "climate.trv_front_room" not in offered_valves(result)
    assert "climate.trv_back_room" in offered_valves(result)
    with pytest.raises(InvalidData):
        await submit(hass, result["flow_id"], first_form("Back Room", ["climate.trv_front_room"]))


async def test_the_user_step_aborts_when_no_free_valve_is_left(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    entry = await setup_house(hass, rooms=[room_subentry("front_room", ["climate.trv_front_room"])])
    result = await start_user_step(hass, entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_valves"


async def test_the_sources_step_offers_the_sensors_of_the_area_first(
    hass: HomeAssistant, hive_entry: MockConfigEntry, area_registry: ar.AreaRegistry
) -> None:
    area = area_registry.async_get_or_create("Front Room")
    add_valve(hass, hive_entry, "trv_front_room", area_id=area.id)
    device_id = add_device(hass, hive_entry, "room_sensor", area_id=area.id)
    add_entity(
        hass,
        "sensor",
        "front_room_temperature",
        device_id=device_id,
        original_device_class="temperature",
    )
    add_sensor(hass, "back_room_temperature")
    entry = await setup_house(hass)
    result = await start_user_step(hass, entry)
    result = await submit(
        hass, result["flow_id"], first_form("Front Room", ["climate.trv_front_room"])
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "sources"
    offered = offered_sources(result)
    assert offered[:2] == [
        "sensor.front_room_temperature",
        "sensor.back_room_temperature",
    ]


async def test_a_source_another_room_claims_is_not_offered(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_valve(hass, hive_entry, "trv_back_room")
    add_sensor(hass, "front_room_temperature")
    add_sensor(hass, "back_room_temperature")
    entry = await setup_house(hass, rooms=[room_subentry("front_room", ["climate.trv_front_room"])])
    result = await start_user_step(hass, entry)
    result = await submit(
        hass, result["flow_id"], first_form("Back Room", ["climate.trv_back_room"])
    )
    assert result["step_id"] == "sources"
    offered = offered_sources(result)
    assert "sensor.front_room_temperature" not in offered
    assert "sensor.back_room_temperature" in offered
    with pytest.raises(InvalidData):
        await submit(hass, result["flow_id"], {"sources": ["sensor.front_room_temperature"]})


async def test_a_device_temperature_is_not_offered_as_a_source(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_sensor(hass, "front_room_temperature")
    add_sensor(hass, "front_room_window_device_temperature")
    entry = await setup_house(hass)
    result = await start_user_step(hass, entry)
    result = await submit(
        hass, result["flow_id"], first_form("Front Room", ["climate.trv_front_room"])
    )
    offered = offered_sources(result)
    assert "sensor.front_room_window_device_temperature" not in offered
    assert "sensor.front_room_temperature" in offered


async def test_one_source_stores_the_weight_one_and_asks_no_weight(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_sensor(hass, "front_room_temperature", name="Room Sensor")
    entry = await setup_house(hass)
    result = await add_room(
        hass,
        entry,
        name="Front Room",
        valves=["climate.trv_front_room"],
        sources=["sensor.front_room_temperature"],
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["sources"] == [
        {"entity_id": "sensor.front_room_temperature", "weight": 1.0}
    ]
    assert HouseConfig.from_entry(entry).rooms[0].sources[0].weight == 1.0


async def test_two_sources_ask_for_their_weights(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_sensor(hass, "front_room_temperature", name="Room Sensor")
    add_sensor(hass, "back_room_temperature", name="Back Sensor")
    entry = await setup_house(hass)
    result = await start_user_step(hass, entry)
    flow_id = result["flow_id"]
    result = await submit(hass, flow_id, first_form("Front Room", ["climate.trv_front_room"]))
    assert result["step_id"] == "sources"
    result = await submit(
        hass,
        flow_id,
        {"sources": ["sensor.front_room_temperature", "sensor.back_room_temperature"]},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "weights"
    fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    assert [field["name"] for field in fields] == ["Back Sensor", "Room Sensor"]
    assert [field["default"] for field in fields] == [1.0, 1.0]

    result = await submit(hass, flow_id, {"Back Sensor": 3.0, "Room Sensor": 1.0})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["sources"] == [
        {"entity_id": "sensor.back_room_temperature", "weight": 3.0},
        {"entity_id": "sensor.front_room_temperature", "weight": 1.0},
    ]
    stored = HouseConfig.from_entry(entry).rooms[0].sources
    assert [(source.entity_id, source.weight) for source in stored] == [
        ("sensor.back_room_temperature", 3.0),
        ("sensor.front_room_temperature", 1.0),
    ]


async def test_a_room_may_name_two_valves(hass: HomeAssistant, hive_entry: MockConfigEntry) -> None:
    add_valve(hass, hive_entry, "trv_front_room_a")
    add_valve(hass, hive_entry, "trv_front_room_b")
    add_sensor(hass, "front_room_temperature")
    entry = await setup_house(hass)
    result = await add_room(
        hass,
        entry,
        name="Front Room",
        valves=["climate.trv_front_room_a", "climate.trv_front_room_b"],
        sources=["sensor.front_room_temperature"],
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    assert subentry.data["valves"] == [
        "climate.trv_front_room_a",
        "climate.trv_front_room_b",
    ]
    assert HouseConfig.from_entry(entry).rooms[0].valves == (
        "climate.trv_front_room_a",
        "climate.trv_front_room_b",
    )


async def test_the_room_form_saves_the_outdoor_fields(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_sensor(hass, "front_room_temperature")
    entry = await setup_house(hass)
    result = await add_room(
        hass,
        entry,
        name="Front Room",
        valves=["climate.trv_front_room"],
        sources=["sensor.front_room_temperature"],
        outdoor_sources=["sensor.room_outdoor"],
        lockout_temperature=20.0,
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["outdoor_sources"] == ["sensor.room_outdoor"]
    assert result["data"]["lockout_temperature"] == 20.0


async def test_an_empty_room_threshold_saves_none(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_sensor(hass, "front_room_temperature")
    entry = await setup_house(hass)
    result = await add_room(
        hass,
        entry,
        name="Front Room",
        valves=["climate.trv_front_room"],
        sources=["sensor.front_room_temperature"],
        outdoor_sources=[],
        lockout_temperature=None,
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["outdoor_sources"] == []
    assert result["data"]["lockout_temperature"] is None


async def test_two_rooms_yield_two_devices_and_two_thermostats(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_valve(hass, hive_entry, "trv_back_room")
    add_sensor(hass, "front_room_temperature")
    add_sensor(hass, "back_room_temperature")
    entry = await setup_house(hass)
    await add_room(
        hass,
        entry,
        name="Front Room",
        valves=["climate.trv_front_room"],
        sources=["sensor.front_room_temperature"],
    )
    await add_room(
        hass,
        entry,
        name="Back Room",
        valves=["climate.trv_back_room"],
        sources=["sensor.back_room_temperature"],
    )

    assert hass.states.get("climate.room_climate_front_room") is not None
    assert hass.states.get("climate.room_climate_back_room") is not None
    front = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.data["room_id"] == "front_room"
    )
    entity = er.async_get(hass).async_get("climate.room_climate_front_room")
    assert entity is not None
    assert entity.unique_id is not None
    assert front.subentry_id in entity.unique_id


async def test_a_room_name_yields_the_room_id_and_the_device(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_sensor(hass, "front_room_temperature")
    entry = await setup_house(hass)
    result = await add_room(
        hass,
        entry,
        name="Front Room",
        valves=["climate.trv_front_room"],
        sources=["sensor.front_room_temperature"],
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["room_id"] == "front_room"

    assert hass.states.get("climate.room_climate_front_room") is not None
    subentry = next(iter(entry.subentries.values()))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_{subentry.subentry_id}"), entry.entry_id
    )
    assert device is not None


async def test_a_simulated_entry_asks_for_the_name_only(hass: HomeAssistant) -> None:
    entry = await setup_house(
        hass,
        simulation=True,
        boiler_entity="climate.sim_boiler",
        outdoor_sources=["number.sim_outdoor_temperature"],
        dry_run=False,
    )
    result = await start_user_step(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert form_fields(result) == ["name"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Front Room"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["room_id"] == "front_room"
    assert result["data"]["valves"] == ["climate.sim_front_room_valve"]
    assert result["data"]["sources"] == [{"entity_id": "number.sim_front_room_room_temperature"}]


async def test_the_reconfigure_step_changes_one_room_alone(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_valve(hass, hive_entry, "trv_back_room")
    add_valve(hass, hive_entry, "trv_side_room")
    add_sensor(hass, "front_room_temperature")
    add_sensor(hass, "back_room_temperature")
    entry = await setup_house(hass)
    await add_room(
        hass,
        entry,
        name="Front Room",
        valves=["climate.trv_front_room"],
        sources=["sensor.front_room_temperature"],
    )
    await add_room(
        hass,
        entry,
        name="Back Room",
        valves=["climate.trv_back_room"],
        sources=["sensor.back_room_temperature"],
    )

    front = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.data["room_id"] == "front_room"
    )
    result = await entry.start_subentry_reconfigure_flow(hass, front.subentry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert "climate.trv_front_room" in offered_valves(result)
    assert "climate.trv_back_room" not in offered_valves(result)

    flow_id = result["flow_id"]
    result = await submit(hass, flow_id, first_form("Front Room", ["climate.trv_side_room"]))
    assert result["step_id"] == "sources"
    result = await submit(hass, flow_id, {"sources": ["sensor.front_room_temperature"]})
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    assert entry.subentries[front.subentry_id].data["valves"] == ["climate.trv_side_room"]
    back = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.data["room_id"] == "back_room"
    )
    assert back.data["valves"] == ["climate.trv_back_room"]


async def test_the_reconfigure_step_opens_with_the_stored_room(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_sensor(hass, "front_room_temperature")
    entry = await setup_house(hass)
    await add_room(
        hass,
        entry,
        name="Front Room",
        valves=["climate.trv_front_room"],
        sources=["sensor.front_room_temperature"],
    )

    front = next(iter(entry.subentries.values()))
    result = await entry.start_subentry_reconfigure_flow(hass, front.subentry_id)
    assert result["step_id"] == "reconfigure"
    fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    name_field = next(field for field in fields if field["name"] == "name")
    assert name_field["description"]["suggested_value"] == "Front Room"

    result = await submit(
        hass, result["flow_id"], first_form("Front Room", ["climate.trv_front_room"])
    )
    assert result["step_id"] == "sources"
    fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    source_field = next(field for field in fields if field["name"] == "sources")
    assert source_field["description"]["suggested_value"] == ["sensor.front_room_temperature"]


async def test_removing_a_room_takes_its_thermostat_away(
    hass: HomeAssistant, hive_entry: MockConfigEntry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    add_valve(hass, hive_entry, "trv_back_room")
    add_sensor(hass, "front_room_temperature")
    add_sensor(hass, "back_room_temperature")
    entry = await setup_house(hass)
    await add_room(
        hass,
        entry,
        name="Front Room",
        valves=["climate.trv_front_room"],
        sources=["sensor.front_room_temperature"],
    )
    await add_room(
        hass,
        entry,
        name="Back Room",
        valves=["climate.trv_back_room"],
        sources=["sensor.back_room_temperature"],
    )
    front = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.data["room_id"] == "front_room"
    )

    hass.config_entries.async_remove_subentry(entry, front.subentry_id)
    await hass.async_block_till_done()

    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    assert entities.async_get("climate.room_climate_front_room") is None
    assert (
        devices.async_get_device_by_identifier(
            (DOMAIN, f"{entry.entry_id}_{front.subentry_id}"), entry.entry_id
        )
        is None
    )
    assert entities.async_get("climate.room_climate_back_room") is not None
    assert hass.states.get("climate.room_climate_back_room") is not None


async def test_a_room_device_belongs_to_its_room_and_its_area(
    hass: HomeAssistant, hive_entry: MockConfigEntry, area_registry: ar.AreaRegistry
) -> None:
    area = area_registry.async_get_or_create("Front Room")
    add_valve(hass, hive_entry, "trv_front_room")
    add_sensor(hass, "front_room_temperature")
    entry = await setup_house(hass)
    await add_room(
        hass,
        entry,
        name="Front Room",
        valves=["climate.trv_front_room"],
        sources=["sensor.front_room_temperature"],
        area_id=area.id,
    )

    subentry = next(iter(entry.subentries.values()))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_{subentry.subentry_id}"), entry.entry_id
    )
    assert device is not None
    assert device.config_subentry_id == subentry.subentry_id
    assert device.area_id == area.id


async def test_the_reconfigure_step_aborts_when_the_room_is_gone(hass: HomeAssistant) -> None:
    entry = await setup_house(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "room"), context={"source": "reconfigure", "subentry_id": "gone"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_subentry"


async def test_the_options_flow_carries_the_house_settings_only(hass: HomeAssistant) -> None:
    entry = await setup_house(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert "rooms" not in form_fields(result)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"outdoor_sources": ["sensor.outdoor"], "lockout_temperature": 20.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["lockout_temperature"] == 20.0
    assert "rooms" not in result["data"]


async def test_from_entry_names_the_rooms_that_share_a_valve(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        options=OPTIONS,
        subentries_data=[
            room_subentry("front_room", ["climate.trv_shared"]),
            room_subentry("back_room", ["climate.trv_shared"]),
        ],
    )
    entry.add_to_hass(hass)
    with pytest.raises(ValidationError) as error:
        HouseConfig.from_entry(entry)
    assert "climate.trv_shared" in str(error.value)


async def test_a_shared_valve_between_subentries_fails_at_load(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate",
        options=OPTIONS,
        subentries_data=[
            room_subentry("front_room", ["climate.trv_shared"]),
            room_subentry("back_room", ["climate.trv_shared"]),
        ],
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
