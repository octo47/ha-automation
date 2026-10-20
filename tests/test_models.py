"""Configuration models reject a configuration the control rules cannot honour."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from custom_components.room_climate.models import HouseConfig, RoomConfig, TemperatureSource


def room(room_id: str = "front_room", **overrides: object) -> RoomConfig:
    payload: dict[str, object] = {
        "room_id": room_id,
        "name": room_id.replace("_", " ").title(),
        "valves": (f"climate.trv_{room_id}",),
        "sources": (TemperatureSource(entity_id=f"sensor.{room_id}_temperature"),),
    }
    payload.update(overrides)
    return RoomConfig(**payload)


def house(**overrides: object) -> HouseConfig:
    payload: dict[str, object] = {
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ("sensor.outdoor_temperature",),
        "rooms": (room(),),
    }
    payload.update(overrides)
    return HouseConfig(**payload)


def test_a_room_keeps_its_valves_and_weights() -> None:
    config = room(sources=(TemperatureSource(entity_id="sensor.a", weight=3.0),))
    assert config.valves == ("climate.trv_front_room",)
    assert config.sources[0].weight == 3.0


@pytest.mark.parametrize("weight", [0.0, -1.0])
def test_weight_must_be_positive(weight: float) -> None:
    with pytest.raises(ValidationError):
        TemperatureSource(entity_id="sensor.a", weight=weight)


def test_a_room_id_is_a_slug() -> None:
    with pytest.raises(ValidationError):
        room("Front Room Room")
    with pytest.raises(ValidationError):
        room("front_room-room")


def test_house_rejects_two_rooms_with_one_room_id() -> None:
    other = room("front_room", name="Front Room Two", valves=("climate.trv_other",))
    with pytest.raises(ValidationError) as error:
        house(rooms=(room(), other))
    assert "room_id front_room" in str(error.value)


def test_a_room_needs_one_valve_and_one_source() -> None:
    with pytest.raises(ValidationError):
        room(valves=())
    with pytest.raises(ValidationError):
        room(sources=())


def test_house_rejects_one_valve_in_two_rooms() -> None:
    other = room("back_room", valves=("climate.trv_front_room",))
    with pytest.raises(ValidationError) as error:
        house(rooms=(room(), other))
    assert "trv_front_room" in str(error.value)
    assert "Front Room" in str(error.value) and "Back Room" in str(error.value)


def test_house_rejects_one_source_in_two_rooms() -> None:
    other = room(
        "back_room", sources=(TemperatureSource(entity_id="sensor.front_room_temperature"),)
    )
    with pytest.raises(ValidationError) as error:
        house(rooms=(room(), other))
    assert "sensor.front_room_temperature" in str(error.value)


def test_modulate_requires_the_calibration_feed() -> None:
    with pytest.raises(ValidationError):
        room(valve_mode="modulate", calibration="none")


def test_boost_works_without_the_calibration_feed() -> None:
    config = room(valve_mode="boost", calibration="none")
    assert config.open_offset == 2.0


def test_on_temperature_must_exceed_off_temperature() -> None:
    with pytest.raises(ValidationError):
        house(boiler_on_temperature=15.0, boiler_off_temperature=20.0)


def test_from_options_round_trips_the_entry_payload() -> None:
    payload = {
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ["sensor.outdoor_temperature"],
        "rooms": [
            {
                "room_id": "front_room",
                "name": "Front Room",
                "valves": ["climate.trv_front_room"],
                "sources": [{"entity_id": "sensor.front_room_temperature"}],
            }
        ],
    }
    config = HouseConfig.from_options(payload)
    assert config.rooms[0].valves == ("climate.trv_front_room",)
    assert config.rooms[0].calibration == "external_sensor"
    assert config.rooms[0].valve_mode == "modulate"
    assert config.dry_run is True
    assert config.simulation is False


def sim_options(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "simulation": True,
        "boiler_entity": "climate.sim_boiler",
        "outdoor_sources": ["number.sim_outdoor_temperature"],
        "rooms": [
            {
                "room_id": "front_room",
                "name": "Front Room",
                "valves": ["climate.sim_front_room_valve"],
                "sources": [{"entity_id": "number.sim_front_room_room_temperature"}],
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_a_simulated_house_accepts_the_virtual_entities() -> None:
    config = HouseConfig.from_options(sim_options())
    assert config.simulation is True
    assert config.rooms[0].valves == ("climate.sim_front_room_valve",)


def test_a_simulated_house_rejects_a_real_entity() -> None:
    payload = sim_options(
        rooms=[
            {
                "room_id": "front_room",
                "name": "Front Room",
                "valves": ["climate.trv_front_room"],
                "sources": [{"entity_id": "number.sim_front_room_room_temperature"}],
            }
        ]
    )
    with pytest.raises(ValidationError) as error:
        HouseConfig.from_options(payload)
    assert "climate.trv_front_room" in str(error.value)


def test_a_simulated_house_rejects_a_real_boiler() -> None:
    with pytest.raises(ValidationError):
        HouseConfig.from_options(sim_options(boiler_entity="climate.heating_heat"))
