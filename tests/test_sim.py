"""The bench: virtual valves, a virtual boiler and the knobs."""

from __future__ import annotations

from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN


def bench_room(**overrides: object) -> dict[str, object]:
    """One room subentry of the bench."""
    data: dict[str, object] = {
        "room_id": "front_room",
        "name": "Front Room",
        "valves": ["climate.sim_front_room_valve"],
        "sources": [{"entity_id": "number.sim_front_room_room_temperature"}],
    }
    data.update(overrides)
    return {
        "data": data,
        "subentry_type": "room",
        "title": str(data["name"]),
        "unique_id": None,
    }


def sim_options(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "simulation": True,
        "boiler_entity": "climate.sim_boiler",
        "outdoor_sources": ["number.sim_outdoor_temperature"],
        "dry_run": False,
        "min_on_seconds": 0,
        "min_off_seconds": 0,
    }
    payload.update(overrides)
    return payload


async def setup_bench(
    hass: HomeAssistant,
    *,
    rooms: list[dict[str, object]] | None = None,
    **overrides: object,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate Bench",
        options=sim_options(**overrides),
        subentries_data=rooms if rooms is not None else [bench_room()],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value})
    await hass.async_block_till_done()


async def set_target(hass: HomeAssistant, entity_id: str, temperature: float) -> None:
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": entity_id, "temperature": temperature}
    )
    await hass.async_block_till_done()


async def test_the_bench_creates_its_entities(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    for entity_id in (
        "climate.sim_front_room_valve",
        "number.sim_front_room_room_temperature",
        "number.sim_front_room_valve_local_temperature",
        "number.sim_front_room_valve_external_measured_room_sensor",
        "sensor.sim_front_room_valve_position",
        "binary_sensor.sim_front_room_heat_required",
        "switch.sim_front_room_window",
        "switch.sim_front_room_offline",
        "climate.sim_boiler",
        "binary_sensor.sim_boiler_firing",
        "number.sim_outdoor_temperature",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_a_real_entity_is_rejected_in_a_simulated_entry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate Bench",
        options=sim_options(),
        subentries_data=[bench_room(valves=["climate.trv_front_room"])],
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_the_bench_climate_entities_answer_turn_off_and_turn_on(
    hass: HomeAssistant,
) -> None:
    await setup_bench(hass)
    for entity_id in ("climate.sim_front_room_valve", "climate.sim_boiler"):
        features = hass.states.get(entity_id).attributes["supported_features"]
        assert features & ClimateEntityFeature.TURN_ON, entity_id
        assert features & ClimateEntityFeature.TURN_OFF, entity_id

        await hass.services.async_call("climate", "turn_off", {"entity_id": entity_id})
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "off", entity_id

        await hass.services.async_call("climate", "turn_on", {"entity_id": entity_id})
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "heat", entity_id


async def test_a_cold_room_fires_the_virtual_boiler(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await set_number(hass, "number.sim_front_room_room_temperature", 19.0)
    await set_target(hass, "climate.room_climate_front_room", 21.0)

    assert hass.states.get("climate.sim_boiler").attributes["temperature"] == 31.0
    assert hass.states.get("binary_sensor.sim_boiler_firing").state == "on"
    assert hass.states.get("climate.sim_front_room_valve").attributes["temperature"] == 21.0
    assert hass.states.get("binary_sensor.sim_front_room_heat_required").state == "on"


async def test_a_warm_room_releases_the_virtual_boiler(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await set_number(hass, "number.sim_front_room_room_temperature", 19.0)
    await set_target(hass, "climate.room_climate_front_room", 21.0)
    await set_number(hass, "number.sim_front_room_room_temperature", 22.0)

    assert hass.states.get("climate.sim_boiler").attributes["temperature"] == 15.0
    assert hass.states.get("binary_sensor.sim_boiler_firing").state == "off"


async def test_the_valve_position_follows_the_gap(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await set_number(hass, "number.sim_front_room_room_temperature", 19.0)
    await set_target(hass, "climate.sim_front_room_valve", 21.0)
    assert float(hass.states.get("sensor.sim_front_room_valve_position").state) == 100.0

    await set_number(hass, "number.sim_front_room_valve_external_measured_room_sensor", 2050)
    assert float(hass.states.get("sensor.sim_front_room_valve_position").state) == 25.0


async def test_the_feed_overrides_the_warm_local_sensor(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await set_number(hass, "number.sim_front_room_valve_local_temperature", 25.0)
    await set_number(hass, "number.sim_front_room_room_temperature", 21.0)
    await set_target(hass, "climate.sim_front_room_valve", 21.0)

    assert hass.states.get("climate.sim_front_room_valve").attributes["current_temperature"] == 21.0
    assert float(hass.states.get("sensor.sim_front_room_valve_position").state) == 0.0


async def test_the_local_sensor_is_the_measurement_without_the_calibration(
    hass: HomeAssistant,
) -> None:
    await setup_bench(
        hass,
        rooms=[bench_room(valve_mode="boost", calibration="none")],
    )
    await set_number(hass, "number.sim_front_room_valve_local_temperature", 24.0)
    assert hass.states.get("climate.sim_front_room_valve").attributes["current_temperature"] == 24.0


async def test_an_offline_valve_reports_unavailable(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.sim_front_room_offline"}
    )
    await hass.async_block_till_done()
    assert hass.states.get("climate.sim_front_room_valve").state == "unavailable"


async def test_an_offline_room_takes_its_knobs_and_sensors_away(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.sim_front_room_offline"}
    )
    await hass.async_block_till_done()
    for entity_id in (
        "number.sim_front_room_valve_local_temperature",
        "number.sim_front_room_valve_external_measured_room_sensor",
        "sensor.sim_front_room_valve_position",
        "binary_sensor.sim_front_room_heat_required",
    ):
        assert hass.states.get(entity_id).state == "unavailable", entity_id


async def test_an_open_window_closes_the_valve(hass: HomeAssistant) -> None:
    await setup_bench(hass, rooms=[bench_room(window_sensors=["switch.sim_front_room_window"])])
    await set_number(hass, "number.sim_front_room_room_temperature", 19.0)
    await set_target(hass, "climate.room_climate_front_room", 21.0)
    assert hass.states.get("climate.sim_front_room_valve").attributes["temperature"] == 21.0

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.sim_front_room_window"}
    )
    await hass.async_block_till_done()

    assert hass.states.get("climate.sim_front_room_valve").attributes["temperature"] == 5.0
    assert hass.states.get("binary_sensor.room_climate_front_room_calling").state == "off"


async def test_a_room_threshold_locks_out_one_room_and_closes_its_valve(
    hass: HomeAssistant,
) -> None:
    await setup_bench(
        hass,
        lockout_temperature=25.0,
        rooms=[
            bench_room(lockout_temperature=19.0),
            bench_room(
                room_id="back_room",
                name="Back Room",
                valves=["climate.sim_back_room_valve"],
                sources=[{"entity_id": "number.sim_back_room_room_temperature"}],
            ),
        ],
    )
    await set_number(hass, "number.sim_front_room_room_temperature", 19.0)
    await set_number(hass, "number.sim_back_room_room_temperature", 19.0)
    await set_target(hass, "climate.room_climate_front_room", 21.0)
    await set_target(hass, "climate.room_climate_back_room", 21.0)
    assert hass.states.get("climate.sim_front_room_valve").attributes["temperature"] == 21.0

    await set_number(hass, "number.sim_outdoor_temperature", 19.5)

    assert hass.states.get("climate.room_climate_front_room").attributes["locked_out"] is True
    assert hass.states.get("climate.sim_front_room_valve").attributes["temperature"] == 5.0
    assert hass.states.get("climate.room_climate_back_room").attributes["locked_out"] is False
    assert hass.states.get("climate.sim_back_room_valve").attributes["temperature"] == 21.0
