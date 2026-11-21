"""The valves and the sensors the instance holds, and the room flow offers them."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN
from custom_components.room_climate.discovery import (
    available_temperature_sources,
    available_valves,
)

HIVE_ENTRY_DOMAIN = "mqtt"
TEMPERATURE = "temperature"
HUMIDITY = "humidity"


def add_device(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    key: str,
    *,
    manufacturer: str | None = None,
    model: str | None = None,
    area_id: str | None = None,
) -> str:
    """Add one device to the registry, and return its id."""
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(HIVE_ENTRY_DOMAIN, key)},
        manufacturer=manufacturer,
        model=model,
    )
    if area_id is not None:
        registry.async_update_device(device.id, area_id=area_id)
    return device.id


def add_entity(
    hass: HomeAssistant,
    domain: str,
    object_id: str,
    *,
    device_id: str | None = None,
    original_name: str | None = None,
    original_device_class: str | None = None,
    area_id: str | None = None,
    config_entry: ConfigEntry | None = None,
) -> str:
    """Add one entity to the registry, and return its entity id."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        domain,
        HIVE_ENTRY_DOMAIN,
        f"{object_id}_unique",
        suggested_object_id=object_id,
        device_id=device_id,
        original_name=original_name,
        original_device_class=original_device_class,
        config_entry=config_entry,
    )
    if area_id is not None:
        registry.async_update_entity(entry.entity_id, area_id=area_id)
    return entry.entity_id


def add_valve(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    object_id: str,
    *,
    manufacturer: str = "Hive",
    model: str = "Radiator valve TRV",
    area_id: str | None = None,
) -> str:
    """Add one Hive radiator valve, and return its entity id."""
    device_id = add_device(
        hass, entry, object_id, manufacturer=manufacturer, model=model, area_id=area_id
    )
    return add_entity(hass, "climate", object_id, device_id=device_id)


def add_sensor(
    hass: HomeAssistant,
    object_id: str,
    *,
    device_id: str | None = None,
    original_name: str | None = None,
    original_device_class: str = TEMPERATURE,
    area_id: str | None = None,
    config_entry: ConfigEntry | None = None,
) -> str:
    """Add one sensor of the house, and return its entity id."""
    return add_entity(
        hass,
        "sensor",
        object_id,
        device_id=device_id,
        original_name=original_name,
        original_device_class=original_device_class,
        area_id=area_id,
        config_entry=config_entry,
    )


def room_entry(hass: HomeAssistant, rooms: Iterable[dict[str, object]] = ()) -> MockConfigEntry:
    """The house entry, with the given rooms as its subentries."""
    entry = MockConfigEntry(domain=DOMAIN, options={}, subentries_data=list(rooms))
    entry.add_to_hass(hass)
    return entry


def room_subentry(*valves: str) -> dict[str, object]:
    """One room subentry that claims the given valves."""
    return {
        "data": {
            "room_id": "front_room",
            "name": "Front Room",
            "valves": list(valves),
            "sources": [{"entity_id": "sensor.front_room_temperature"}],
        },
        "subentry_type": "room",
        "title": "Front Room",
        "unique_id": None,
    }


def test_a_hive_radiator_valve_is_a_candidate(hass: HomeAssistant, hive_entry) -> None:
    add_valve(hass, hive_entry, "trv_front_room", model="Radiator valve TRV")
    candidates = available_valves(hass, room_entry(hass))
    assert [candidate.entity_id for candidate in candidates] == ["climate.trv_front_room"]


def test_a_hive_valve_carries_the_model_prefix_only(hass: HomeAssistant, hive_entry) -> None:
    add_valve(hass, hive_entry, "trv_side_room", model="Radiator valve")
    candidates = available_valves(hass, room_entry(hass))
    assert [candidate.entity_id for candidate in candidates] == ["climate.trv_side_room"]


def test_a_boiler_thermostat_is_not_a_candidate(hass: HomeAssistant, hive_entry) -> None:
    device_id = add_device(
        hass,
        hive_entry,
        "boiler",
        manufacturer="Hive",
        model="Dual channel heating and hot water thermostat",
    )
    add_entity(hass, "climate", "heating_heat", device_id=device_id)
    add_entity(hass, "climate", "heating_water", device_id=device_id)
    assert available_valves(hass, room_entry(hass)) == ()


def test_a_better_thermostat_is_not_a_candidate(hass: HomeAssistant, hive_entry) -> None:
    device_id = add_device(
        hass,
        hive_entry,
        "bt_front_room",
        manufacturer="Better Thermostat",
        model="Better Thermostat",
    )
    add_entity(hass, "climate", "bt_front_room", device_id=device_id)
    assert available_valves(hass, room_entry(hass)) == ()


def test_a_climate_that_owns_a_feed_is_a_candidate(hass: HomeAssistant, hive_entry) -> None:
    device_id = add_device(hass, hive_entry, "other", manufacturer="Acme", model="Thermostat")
    add_entity(hass, "climate", "trv_side_room", device_id=device_id)
    add_entity(hass, "number", "trv_side_room_external_measured_room_sensor")
    candidates = available_valves(hass, room_entry(hass))
    assert [candidate.entity_id for candidate in candidates] == ["climate.trv_side_room"]


def test_a_climate_without_a_device_is_not_a_candidate(hass: HomeAssistant, hive_entry) -> None:
    add_entity(hass, "climate", "trv_orphan")
    assert available_valves(hass, room_entry(hass)) == ()


def test_a_candidate_carries_its_local_temperature_when_it_exists(
    hass: HomeAssistant, hive_entry
) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    assert available_valves(hass, room_entry(hass))[0].source_entity_id is None
    add_entity(hass, "sensor", "trv_front_room_local_temperature")
    assert (
        available_valves(hass, room_entry(hass))[0].source_entity_id
        == "sensor.trv_front_room_local_temperature"
    )


def test_a_candidate_carries_the_area_of_its_device(
    hass: HomeAssistant, hive_entry, area_registry: ar.AreaRegistry
) -> None:
    area = area_registry.async_get_or_create("Front Room")
    add_valve(hass, hive_entry, "trv_front_room", area_id=area.id)
    assert available_valves(hass, room_entry(hass))[0].area_id == area.id


def test_a_candidate_carries_the_name_of_its_entity(hass: HomeAssistant, hive_entry) -> None:
    device_id = add_device(
        hass, hive_entry, "trv_front_room", manufacturer="Hive", model="Radiator valve TRV"
    )
    add_entity(
        hass,
        "climate",
        "trv_front_room",
        device_id=device_id,
        original_name="Front Room Valve",
    )
    assert available_valves(hass, room_entry(hass))[0].name == "Front Room Valve"


def test_a_claimed_valve_is_not_offered(hass: HomeAssistant, hive_entry) -> None:
    add_valve(hass, hive_entry, "trv_front_room")
    entry = room_entry(hass, [room_subentry("climate.trv_front_room")])
    assert available_valves(hass, entry) == ()


def test_a_temperature_sensor_is_a_source(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_temperature")
    sources = available_temperature_sources(hass, room_entry(hass))
    assert [source.entity_id for source in sources] == ["sensor.front_room_temperature"]


def test_a_sensor_of_another_device_class_is_not_a_source(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_humidity", original_device_class=HUMIDITY)
    assert available_temperature_sources(hass, room_entry(hass)) == ()


def test_a_sensor_without_a_device_class_is_not_a_source(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_temperature", original_device_class=None)
    assert available_temperature_sources(hass, room_entry(hass)) == ()


def test_a_source_is_read_from_the_state_of_the_entity(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_temperature", original_device_class=None)
    hass.states.async_set("sensor.front_room_temperature", "21.4", {"device_class": TEMPERATURE})
    sources = available_temperature_sources(hass, room_entry(hass))
    assert [source.entity_id for source in sources] == ["sensor.front_room_temperature"]


def test_the_state_of_an_entity_leads_its_registry_entry(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_temperature", original_device_class=HUMIDITY)
    hass.states.async_set("sensor.front_room_temperature", "21.4", {"device_class": TEMPERATURE})
    sources = available_temperature_sources(hass, room_entry(hass))
    assert [source.entity_id for source in sources] == ["sensor.front_room_temperature"]


def test_a_source_of_the_component_itself_is_not_offered(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_temperature")
    entry = room_entry(hass)
    add_sensor(hass, "room_climate_house_temperature", config_entry=entry)
    add_sensor(hass, "room_climate_front_room_temperature", config_entry=entry)
    sources = available_temperature_sources(hass, entry)
    assert [source.entity_id for source in sources] == ["sensor.front_room_temperature"]


def test_a_source_of_another_component_is_offered(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_temperature", config_entry=hive_entry)
    sources = available_temperature_sources(hass, room_entry(hass))
    assert [source.entity_id for source in sources] == ["sensor.front_room_temperature"]


def test_a_device_temperature_is_not_a_source(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_door_device_temperature")
    add_sensor(hass, "front_room_temperature")
    sources = available_temperature_sources(hass, room_entry(hass))
    assert [source.entity_id for source in sources] == ["sensor.front_room_temperature"]


def test_a_claimed_source_is_not_offered(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_temperature")
    entry = room_entry(hass, [room_subentry("climate.trv_front_room")])
    assert available_temperature_sources(hass, entry) == ()


def test_a_claimed_source_of_another_room_stays_offered(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_temperature")
    entry = room_entry(hass, [room_subentry("climate.trv_front_room")])
    sources = available_temperature_sources(
        hass, entry, exclude_subentry_id=next(iter(entry.subentries))
    )
    assert [source.entity_id for source in sources] == ["sensor.front_room_temperature"]


def test_the_sources_of_the_first_area_come_first(
    hass: HomeAssistant, hive_entry, area_registry: ar.AreaRegistry
) -> None:
    area = area_registry.async_get_or_create("Front Room")
    device_id = add_device(hass, hive_entry, "room_sensor", area_id=area.id)
    add_sensor(hass, "front_room_temperature", device_id=device_id)
    add_sensor(hass, "back_room_temperature")
    sources = available_temperature_sources(hass, room_entry(hass), first_area=area.id)
    assert [source.entity_id for source in sources] == [
        "sensor.front_room_temperature",
        "sensor.back_room_temperature",
    ]


def test_a_source_carries_the_area_of_its_device(
    hass: HomeAssistant, hive_entry, area_registry: ar.AreaRegistry
) -> None:
    area = area_registry.async_get_or_create("Front Room")
    device_id = add_device(hass, hive_entry, "room_sensor", area_id=area.id)
    add_sensor(hass, "front_room_temperature", device_id=device_id)
    assert available_temperature_sources(hass, room_entry(hass))[0].area_id == area.id


def test_a_source_carries_the_area_of_its_entity_when_its_device_has_none(
    hass: HomeAssistant, hive_entry, area_registry: ar.AreaRegistry
) -> None:
    area = area_registry.async_get_or_create("Front Room")
    device_id = add_device(hass, hive_entry, "room_sensor")
    add_sensor(
        hass,
        "front_room_temperature",
        device_id=device_id,
        original_name="Room Sensor",
        area_id=area.id,
    )
    assert available_temperature_sources(hass, room_entry(hass))[0].area_id == area.id


def test_a_source_carries_the_name_of_its_entity(hass: HomeAssistant, hive_entry) -> None:
    add_sensor(hass, "front_room_temperature", original_name="Front Room Sensor")
    assert available_temperature_sources(hass, room_entry(hass))[0].name == "Front Room Sensor"
