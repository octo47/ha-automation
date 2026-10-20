"""The simulation house: virtual valves, a virtual boiler, and the knobs."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, SIM_MARKER

FEED_DISABLED = -8000.0
FEED_SCALE = 100.0
FEED_MIN = -8000.0
FEED_MAX = 3500.0
FEED_STEP = 1.0
VALVE_POSITION_GAIN = 50.0
BOILER_FIRING_THRESHOLD = 30.0
SIM_MIN_TEMPERATURE = 5.0
SIM_MAX_TEMPERATURE = 35.0
SIM_BOILER_MAX_TEMPERATURE = 32.0
SIM_INITIAL_TEMPERATURE = 20.0
SIM_INITIAL_OUTDOOR_TEMPERATURE = 10.0
SIM_INITIAL_BOILER_TEMPERATURE = 15.0
CLOSED_VALVE_STATES = ("off", "unavailable")
UNUSABLE_STATES = ("unknown", "unavailable", "")


def sim_valve_entity(room_id: str) -> str:
    """Return the virtual valve of one room."""
    return f"climate.{SIM_MARKER}{room_id}_valve"


def sim_source_entity(room_id: str) -> str:
    """Return the virtual room temperature of one room."""
    return f"number.{SIM_MARKER}{room_id}_room_temperature"


def sim_local_temperature_entity(room_id: str) -> str:
    """Return the local sensor knob of one virtual valve."""
    return f"number.{SIM_MARKER}{room_id}_valve_local_temperature"


def sim_feed_entity(room_id: str) -> str:
    """Return the feed entity of one virtual valve."""
    return f"number.{SIM_MARKER}{room_id}_valve_external_measured_room_sensor"


def sim_window_entity(room_id: str) -> str:
    """Return the window knob of one room."""
    return f"switch.{SIM_MARKER}{room_id}_window"


def sim_offline_entity(room_id: str) -> str:
    """Return the offline knob of one room."""
    return f"switch.{SIM_MARKER}{room_id}_offline"


def sim_position_entity(room_id: str) -> str:
    """Return the position sensor of one virtual valve."""
    return f"sensor.{SIM_MARKER}{room_id}_valve_position"


def sim_heat_required_entity(room_id: str) -> str:
    """Return the heat demand of one virtual valve."""
    return f"binary_sensor.{SIM_MARKER}{room_id}_heat_required"


SIM_BOILER = "climate.sim_boiler"
SIM_BOILER_FIRING = "binary_sensor.sim_boiler_firing"
SIM_OUTDOOR = "number.sim_outdoor_temperature"


def _number(hass: HomeAssistant, entity_id: str) -> float | None:
    """Return the number of one entity, or None when it carries none."""
    state: State | None = hass.states.get(entity_id)
    if state is None or state.state in UNUSABLE_STATES:
        return None
    try:
        return float(state.state)
    except TypeError, ValueError:
        return None


def measured_temperature(hass: HomeAssistant, room_id: str) -> float | None:
    """Return what a virtual valve measures: the feed when set, else its local sensor.

    The feed value −8000 means "no external measurement", so the local sensor carries the
    measurement until the calibration writes a feed value.
    """
    feed = _number(hass, sim_feed_entity(room_id))
    if feed is not None and feed != FEED_DISABLED:
        return feed / FEED_SCALE
    return _number(hass, sim_local_temperature_entity(room_id))


def _valve_position(target: float | None, measured: float | None) -> float:
    """Return the position of a virtual valve for one target and one measurement."""
    if target is None or measured is None:
        return 0.0
    return max(0.0, min(100.0, (target - measured) * VALVE_POSITION_GAIN))


def valve_position(hass: HomeAssistant, room_id: str) -> float:
    """Return the position of one virtual valve in percent."""
    state = hass.states.get(sim_valve_entity(room_id))
    if state is None or state.state in CLOSED_VALVE_STATES:
        return 0.0
    target = state.attributes.get("temperature")
    return _valve_position(target, measured_temperature(hass, room_id))


def _boiler_fires(target: float | None, mode: HVACMode | None) -> bool:
    """Return whether one boiler target and mode mean firing."""
    return mode == HVACMode.HEAT and target is not None and target >= BOILER_FIRING_THRESHOLD


def boiler_firing(hass: HomeAssistant) -> bool:
    """Return whether the virtual boiler fires."""
    state = hass.states.get(SIM_BOILER)
    if state is None or state.state not in (HVACMode.HEAT, HVACMode.OFF):
        return False
    return _boiler_fires(state.attributes.get("temperature"), HVACMode(state.state))


def _room_offline(hass: HomeAssistant, room_id: str) -> bool:
    """Return whether the offline knob of one room is on."""
    state = hass.states.get(sim_offline_entity(room_id))
    return state is not None and state.state == "on"


class _SimEntity(Entity):
    """A simulation entity that recomputes when one of the entities it reads changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_available = True

    def __init__(
        self,
        entry_id: str,
        key: str,
        name: str,
        watch: tuple[str, ...] = (),
        offline_room: str | None = None,
    ) -> None:
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{key}"
        self._attr_name = name
        self._watch = watch
        self._offline_room = offline_room

    @callback
    def _sync(self) -> None:
        """Recompute the values this entity derives from the entities it reads."""

    @callback
    def _publish(self) -> None:
        """Refresh the availability, recompute the values, and write the state."""
        self._attr_available = self._offline_room is None or not _room_offline(
            self.hass, self._offline_room
        )
        self._sync()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Follow the entities this entity reads, and the offline knob of its room."""
        await super().async_added_to_hass()
        watch = self._watch
        if self._offline_room is not None:
            watch = (*watch, sim_offline_entity(self._offline_room))
        if watch:
            self.async_on_remove(
                async_track_state_change_event(self.hass, list(watch), self._handle_change)
            )
        self._publish()

    @callback
    def _handle_change(self, event: Event[EventStateChangedData]) -> None:
        """Publish the recomputed state after one watched entity changed."""
        self._publish()


class SimValveEntity(_SimEntity, ClimateEntity):
    """A valve that computes its position from the gap of target and measurement."""

    def __init__(self, entry_id: str, room_id: str) -> None:
        super().__init__(
            entry_id,
            f"{SIM_MARKER}{room_id}_valve",
            f"Sim {room_id.replace('_', ' ')} valve",
            (sim_feed_entity(room_id), sim_local_temperature_entity(room_id)),
            offline_room=room_id,
        )
        self.room_id = room_id
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        self._attr_target_temperature_step = 0.5
        self._attr_min_temp = SIM_MIN_TEMPERATURE
        self._attr_max_temp = SIM_MAX_TEMPERATURE
        self._attr_target_temperature = SIM_MIN_TEMPERATURE

    @callback
    def _sync(self) -> None:
        """Recompute the measurement and the action of the virtual valve."""
        measured = measured_temperature(self.hass, self.room_id)
        self._attr_current_temperature = measured
        position = _valve_position(self._attr_target_temperature, measured)
        self._attr_hvac_action = HVACAction.HEATING if position > 0 else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Take the valve command as the target of the virtual valve."""
        temperature = kwargs.get("temperature")
        if isinstance(temperature, (int, float)):
            self._attr_target_temperature = float(temperature)
        self._publish()


class SimBoilerEntity(_SimEntity, ClimateEntity):
    """A boiler that fires on a high target, like the Hive pair."""

    def __init__(self, entry_id: str) -> None:
        super().__init__(entry_id, "sim_boiler", "Sim boiler")
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        self._attr_target_temperature_step = 0.5
        self._attr_min_temp = SIM_MIN_TEMPERATURE
        self._attr_max_temp = SIM_BOILER_MAX_TEMPERATURE
        self._attr_target_temperature = SIM_INITIAL_BOILER_TEMPERATURE

    @callback
    def _sync(self) -> None:
        """Recompute the action of the virtual boiler."""
        firing = _boiler_fires(self._attr_target_temperature, self._attr_hvac_mode)
        self._attr_hvac_action = HVACAction.HEATING if firing else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Take the boiler command as the target and the mode of the virtual boiler."""
        temperature = kwargs.get("temperature")
        if isinstance(temperature, (int, float)):
            self._attr_target_temperature = float(temperature)
        mode = kwargs.get("hvac_mode")
        if isinstance(mode, HVACMode):
            self._attr_hvac_mode = mode
        self._publish()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Take the mode of the virtual boiler."""
        self._attr_hvac_mode = hvac_mode
        self._publish()


class SimNumberEntity(_SimEntity, NumberEntity):
    """A knob of the bench, in the range and the units of the entity it stands for."""

    def __init__(
        self,
        entry_id: str,
        key: str,
        name: str,
        initial: float,
        *,
        minimum: float = SIM_MIN_TEMPERATURE,
        maximum: float = SIM_MAX_TEMPERATURE,
        step: float = 0.1,
        device_class: NumberDeviceClass | None = NumberDeviceClass.TEMPERATURE,
        unit: str | None = UnitOfTemperature.CELSIUS,
        offline_room: str | None = None,
    ) -> None:
        super().__init__(entry_id, key, name, offline_room=offline_room)
        self._attr_mode = NumberMode.BOX
        self._attr_native_value = initial
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_step = step
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit

    async def async_set_native_value(self, value: float) -> None:
        """Take the value the operator set by hand."""
        self._attr_native_value = value
        self._publish()


class SimSwitchEntity(_SimEntity, SwitchEntity):
    """A two state knob of the bench."""

    def __init__(self, entry_id: str, key: str, name: str, initial: bool = False) -> None:
        super().__init__(entry_id, key, name)
        self._attr_is_on = initial

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the knob on."""
        self._attr_is_on = True
        self._publish()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the knob off."""
        self._attr_is_on = False
        self._publish()


class SimValvePositionSensor(_SimEntity, SensorEntity):
    """The position of one virtual valve."""

    def __init__(self, entry_id: str, room_id: str) -> None:
        super().__init__(
            entry_id,
            f"{SIM_MARKER}{room_id}_valve_position",
            f"Sim {room_id.replace('_', ' ')} valve position",
            (
                sim_valve_entity(room_id),
                sim_feed_entity(room_id),
                sim_local_temperature_entity(room_id),
            ),
            offline_room=room_id,
        )
        self.room_id = room_id
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @callback
    def _sync(self) -> None:
        """Recompute the position the virtual valve computes."""
        self._attr_native_value = valve_position(self.hass, self.room_id)


class SimHeatRequiredBinary(_SimEntity, BinarySensorEntity):
    """The heat demand of one virtual valve."""

    def __init__(self, entry_id: str, room_id: str) -> None:
        super().__init__(
            entry_id,
            f"{SIM_MARKER}{room_id}_heat_required",
            f"Sim {room_id.replace('_', ' ')} heat required",
            (sim_valve_entity(room_id),),
            offline_room=room_id,
        )
        self.room_id = room_id
        self._attr_device_class = BinarySensorDeviceClass.HEAT

    @callback
    def _sync(self) -> None:
        """Recompute the demand of the position above zero."""
        self._attr_is_on = valve_position(self.hass, self.room_id) > 0


class SimBoilerFiringBinary(_SimEntity, BinarySensorEntity):
    """Whether the virtual boiler fires."""

    def __init__(self, entry_id: str) -> None:
        super().__init__(entry_id, "sim_boiler_firing", "Sim boiler firing", (SIM_BOILER,))
        self._attr_device_class = BinarySensorDeviceClass.HEAT

    @callback
    def _sync(self) -> None:
        """Recompute the firing state of the virtual boiler."""
        self._attr_is_on = boiler_firing(self.hass)
