"""The config flow of the integration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlowWithConfigEntry,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.util import slugify
from pydantic import ValidationError

from .const import (
    CALIBRATION_EXTERNAL,
    CALIBRATION_NONE,
    CONF_AREA_ID,
    CONF_BOILER_ENTITY,
    CONF_BOILER_OFF_TEMPERATURE,
    CONF_BOILER_ON_TEMPERATURE,
    CONF_CALIBRATION,
    CONF_CALIBRATION_SCALE,
    CONF_DEMAND_FALLBACK_SENSORS,
    CONF_DRY_RUN,
    CONF_HYSTERESIS,
    CONF_LOCKOUT_HYSTERESIS,
    CONF_LOCKOUT_TEMPERATURE,
    CONF_MASTER_ENABLED,
    CONF_MAX_TEMP,
    CONF_MIN_OFF_SECONDS,
    CONF_MIN_ON_SECONDS,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_OFF_SETPOINT,
    CONF_OPEN_OFFSET,
    CONF_OUTDOOR_SOURCES,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_SIMULATION,
    CONF_SOURCES,
    CONF_UPDATE_INTERVAL_SECONDS,
    CONF_VALVE_COMMAND_INTERVAL_SECONDS,
    CONF_VALVE_MODE,
    CONF_VALVES,
    CONF_WINDOW_SENSORS,
    DEFAULT_BOILER_ENTITY,
    DEFAULT_BOILER_OFF_TEMPERATURE,
    DEFAULT_BOILER_ON_TEMPERATURE,
    DEFAULT_CALIBRATION_SCALE,
    DEFAULT_DRY_RUN,
    DEFAULT_HYSTERESIS,
    DEFAULT_LOCKOUT_HYSTERESIS,
    DEFAULT_LOCKOUT_TEMPERATURE,
    DEFAULT_MIN_OFF_SECONDS,
    DEFAULT_MIN_ON_SECONDS,
    DEFAULT_OFF_SETPOINT,
    DEFAULT_OPEN_OFFSET,
    DEFAULT_OUTDOOR_SOURCES,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    DEFAULT_VALVE_COMMAND_INTERVAL_SECONDS,
    DOMAIN,
    SUBENTRY_TYPE_ROOM,
    VALVE_MODE_BOOST,
    VALVE_MODE_MODULATE,
)
from .discovery import ValveCandidate, available_valves
from .models import HouseConfig, RoomConfig
from .sim import sim_source_entity, sim_valve_entity

SOURCE_ENTITY = "entity_id"
SOURCE_WEIGHT = "weight"

# The room form carries no room id, so the payload of a room is the fields of the model.
ROOM_FIELDS = tuple(RoomConfig.model_fields)


def _selector(name: str, config: Any = None) -> Any:
    """Return one selector of Home Assistant.

    The selector classes carry a generic parameter that the strict profile of
    the type checker does not resolve, so the class is looked up on the module
    by name.
    """
    factory: Any = getattr(selector, name)
    return factory(config)


def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> Any:
    """Return a box selector for one numeric field."""
    config = selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return _selector("NumberSelector", config)


SOURCE_SCHEMA = probatio.Schema(
    {
        probatio.Required(SOURCE_ENTITY): _selector("EntitySelector"),
        probatio.Optional(SOURCE_WEIGHT, default=1.0): probatio.Coerce(float),
    }
)


def _valve_field(candidates: Sequence[ValveCandidate]) -> Any:
    """Return the valve field: a multi-select over the valves the instance holds."""
    options: list[selector.SelectOptionDict] = [
        {"value": candidate.entity_id, "label": candidate.name} for candidate in candidates
    ]
    return _selector(
        "SelectSelector", selector.SelectSelectorConfig(options=options, multiple=True)
    )


def room_schema(candidates: Sequence[ValveCandidate]) -> probatio.Schema:
    """Return the room form of a subentry flow, over the valves of the instance."""
    return probatio.Schema(
        {
            probatio.Required(CONF_NAME): _selector("TextSelector"),
            probatio.Required(CONF_VALVES): _valve_field(candidates),
            probatio.Required(CONF_SOURCES): probatio.All(cv.ensure_list, [SOURCE_SCHEMA]),
            probatio.Optional(CONF_AREA_ID): _selector("AreaSelector"),
            probatio.Optional(CONF_CALIBRATION, default=CALIBRATION_EXTERNAL): _selector(
                "SelectSelector",
                selector.SelectSelectorConfig(options=[CALIBRATION_EXTERNAL, CALIBRATION_NONE]),
            ),
            probatio.Optional(CONF_CALIBRATION_SCALE, default=DEFAULT_CALIBRATION_SCALE): _number(
                1, 1000, 1
            ),
            probatio.Optional(CONF_VALVE_MODE, default=VALVE_MODE_MODULATE): _selector(
                "SelectSelector",
                selector.SelectSelectorConfig(options=[VALVE_MODE_MODULATE, VALVE_MODE_BOOST]),
            ),
            probatio.Optional(CONF_OPEN_OFFSET, default=DEFAULT_OPEN_OFFSET): _number(
                0, 5, 0.5, "°C"
            ),
            probatio.Optional(CONF_MIN_TEMP, default=5.0): _number(5, 20, 0.5, "°C"),
            probatio.Optional(CONF_MAX_TEMP, default=30.0): _number(15, 35, 0.5, "°C"),
            probatio.Optional(CONF_HYSTERESIS, default=DEFAULT_HYSTERESIS): _number(
                0, 2, 0.1, "°C"
            ),
            probatio.Optional(CONF_OFF_SETPOINT, default=DEFAULT_OFF_SETPOINT): _number(
                5, 12, 0.5, "°C"
            ),
            probatio.Optional(
                CONF_VALVE_COMMAND_INTERVAL_SECONDS,
                default=DEFAULT_VALVE_COMMAND_INTERVAL_SECONDS,
            ): _number(0, 3600, 1, "s"),
            probatio.Optional(CONF_WINDOW_SENSORS, default=list): _selector(
                "EntitySelector", selector.EntitySelectorConfig(multiple=True)
            ),
            probatio.Optional(CONF_DEMAND_FALLBACK_SENSORS, default=list): _selector(
                "EntitySelector", selector.EntitySelectorConfig(multiple=True)
            ),
            probatio.Optional(CONF_OUTDOOR_SOURCES, default=list): _selector(
                "EntitySelector", selector.EntitySelectorConfig(multiple=True)
            ),
            probatio.Optional(CONF_LOCKOUT_TEMPERATURE): probatio.Any(
                None, _number(-20, 40, 0.5, "°C")
            ),
        },
        extra=probatio.ALLOW_EXTRA,
    )


# A simulated house derives the valve and the source from the room id, so its form is the name.
SIM_ROOM_SCHEMA = probatio.Schema({probatio.Required(CONF_NAME): _selector("TextSelector")})

HOUSE_SCHEMA = probatio.Schema(
    {
        probatio.Optional(CONF_NAME): _selector("TextSelector"),
        probatio.Required(CONF_BOILER_ENTITY, default=DEFAULT_BOILER_ENTITY): _selector(
            "EntitySelector", selector.EntitySelectorConfig(domain="climate")
        ),
        probatio.Required(
            CONF_BOILER_ON_TEMPERATURE, default=DEFAULT_BOILER_ON_TEMPERATURE
        ): _number(5, 32, 0.5, "°C"),
        probatio.Required(
            CONF_BOILER_OFF_TEMPERATURE, default=DEFAULT_BOILER_OFF_TEMPERATURE
        ): _number(5, 32, 0.5, "°C"),
        probatio.Required(CONF_OUTDOOR_SOURCES, default=list(DEFAULT_OUTDOOR_SOURCES)): _selector(
            "EntitySelector", selector.EntitySelectorConfig(multiple=True)
        ),
        probatio.Required(CONF_LOCKOUT_TEMPERATURE, default=DEFAULT_LOCKOUT_TEMPERATURE): _number(
            -20, 40, 0.5, "°C"
        ),
        probatio.Required(CONF_LOCKOUT_HYSTERESIS, default=DEFAULT_LOCKOUT_HYSTERESIS): _number(
            0, 10, 0.5, "°C"
        ),
        probatio.Required(CONF_MIN_ON_SECONDS, default=DEFAULT_MIN_ON_SECONDS): _number(
            0, 3600, 1, "s"
        ),
        probatio.Required(CONF_MIN_OFF_SECONDS, default=DEFAULT_MIN_OFF_SECONDS): _number(
            0, 3600, 1, "s"
        ),
        probatio.Required(
            CONF_UPDATE_INTERVAL_SECONDS, default=DEFAULT_UPDATE_INTERVAL_SECONDS
        ): _number(10, 600, 1, "s"),
        probatio.Required(CONF_DRY_RUN, default=DEFAULT_DRY_RUN): _selector("BooleanSelector"),
        probatio.Required(CONF_MASTER_ENABLED, default=True): _selector("BooleanSelector"),
        probatio.Required(CONF_SIMULATION, default=False): _selector("BooleanSelector"),
    }
)


def _room_payload(user_input: dict[str, Any], room_id: str) -> dict[str, Any]:
    """Build the room model payload from one form result."""
    payload = {key: value for key, value in user_input.items() if key in ROOM_FIELDS}
    payload[CONF_ROOM_ID] = room_id
    return payload


def _error_key(error: ValidationError) -> str:
    """Map one model failure to the error key of the form."""
    for detail in error.errors():
        message = str(detail.get("msg", ""))
        if "claimed by" in message or "is used by" in message:
            return "shared_entity"
    return "invalid_room"


def _validate_house(options: dict[str, Any]) -> tuple[HouseConfig | None, str | None]:
    """Validate one full options payload, and name the error key of a failure."""
    try:
        return HouseConfig.model_validate(options), None
    except ValidationError as error:
        return None, _error_key(error)


class RoomClimateConfigFlow(ConfigFlow, domain=DOMAIN):
    """The house entry of one installation."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types of this integration: one room each."""
        return {SUBENTRY_TYPE_ROOM: RoomSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the house entry from the house form."""
        if user_input is not None:
            config, error = _validate_house({**user_input, CONF_ROOMS: []})
            if config is not None:
                return self.async_create_entry(
                    title=config.name, data=config.model_dump(mode="json")
                )
            return self.async_show_form(
                step_id="user",
                data_schema=HOUSE_SCHEMA,
                errors={"base": error or "invalid_room"},
            )
        return self.async_show_form(step_id="user", data_schema=HOUSE_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> RoomClimateOptionsFlow:
        """Return the options flow that edits the house settings."""
        return RoomClimateOptionsFlow(config_entry)


class RoomSubentryFlow(ConfigSubentryFlow):
    """Add one room to the house, or change the room that is already there."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add one room to the house."""
        return await self._async_step_room("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change the valves and the sources of one room."""
        if self._reconfigure_subentry_id not in self._get_entry().subentries:
            return self.async_abort(reason="invalid_subentry")
        return await self._async_step_room("reconfigure", user_input)

    async def _async_step_room(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """Show the room form, and save the room it carries."""
        entry = self._get_entry()
        exclude = self._exclude(step_id)
        if entry.options.get(CONF_SIMULATION):
            return self._step_simulated(entry, step_id, user_input)
        candidates = available_valves(self.hass, entry, exclude_subentry_id=exclude)
        if not candidates:
            return self.async_abort(reason="no_valves")
        if user_input is None:
            return self.async_show_form(
                step_id=step_id, data_schema=self._form_schema(candidates, step_id)
            )
        name = str(user_input.get(CONF_NAME, ""))
        payload = _room_payload(user_input, slugify(name))
        error = self._room_error(entry, payload, exclude)
        if error is None:
            return self._finish_room(entry, step_id, payload, name)
        return self.async_show_form(
            step_id=step_id,
            data_schema=self._form_schema(candidates, step_id),
            errors={"base": error},
        )

    def _step_simulated(
        self, entry: ConfigEntry, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """Show the name only, and derive the virtual valve and source from the room id."""
        if user_input is None:
            return self.async_show_form(step_id=step_id, data_schema=SIM_ROOM_SCHEMA)
        name = str(user_input.get(CONF_NAME, ""))
        room_id = slugify(name)
        payload: dict[str, Any] = {
            CONF_ROOM_ID: room_id,
            CONF_NAME: name,
            CONF_VALVES: [sim_valve_entity(room_id)],
            CONF_SOURCES: [{SOURCE_ENTITY: sim_source_entity(room_id)}],
        }
        error = self._room_error(entry, payload, self._exclude(step_id))
        if error is None:
            return self._finish_room(entry, step_id, payload, name)
        return self.async_show_form(
            step_id=step_id, data_schema=SIM_ROOM_SCHEMA, errors={"base": error}
        )

    def _exclude(self, step_id: str) -> str | None:
        """The room that a reconfigure flow must not treat as another room."""
        if step_id != "reconfigure":
            return None
        return self._reconfigure_subentry_id

    def _form_schema(self, candidates: Sequence[ValveCandidate], step_id: str) -> probatio.Schema:
        """The room form, pre-filled with the room that is being changed."""
        schema = room_schema(candidates)
        if step_id != "reconfigure":
            return schema
        return self.add_suggested_values_to_schema(
            schema, dict(self._get_reconfigure_subentry().data)
        )

    @staticmethod
    def _room_error(entry: ConfigEntry, payload: dict[str, Any], exclude: str | None) -> str | None:
        """Return the error key of one room form result, or None when it is sound.

        The house model claims the valves and the sources of its rooms, so a room
        that takes one already in use is refused here.
        """
        rooms = [
            dict(subentry.data)
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_ROOM and subentry.subentry_id != exclude
        ]
        rooms.append(payload)
        return _validate_house({**dict(entry.options), CONF_ROOMS: rooms})[1]

    def _finish_room(
        self, entry: ConfigEntry, step_id: str, payload: dict[str, Any], name: str
    ) -> SubentryFlowResult:
        """Create the room, or write the change into the room that is already there."""
        if step_id == "reconfigure":
            return self.async_update_and_abort(
                entry, self._get_reconfigure_subentry(), title=name, data=payload
            )
        return self.async_create_entry(title=name, data=payload)


class RoomClimateOptionsFlow(OptionsFlowWithConfigEntry):
    """The house settings of one house, which the options carry."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit the house settings, the only thing the options carry."""
        if user_input is not None:
            config, error = _validate_house({**dict(self.options), **user_input, CONF_ROOMS: []})
            if config is not None:
                saved = config.model_dump(mode="json")
                saved.pop(CONF_ROOMS, None)
                return self.async_create_entry(data=saved)
            return self.async_show_form(
                step_id="init",
                data_schema=self._house_schema(),
                errors={"base": error or "invalid_room"},
            )
        return self.async_show_form(step_id="init", data_schema=self._house_schema())

    def _house_schema(self) -> probatio.Schema:
        """The house form, pre-filled with the current options."""
        return self.add_suggested_values_to_schema(HOUSE_SCHEMA, self.options)
