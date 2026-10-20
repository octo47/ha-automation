"""The config flow of the integration."""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
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
    VALVE_MODE_BOOST,
    VALVE_MODE_MODULATE,
)
from .models import HouseConfig, RoomConfig

SOURCE_ENTITY = "entity_id"
SOURCE_WEIGHT = "weight"

# The simulation flag of the house model, which the house form offers.

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

ROOM_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_NAME): _selector("TextSelector"),
        probatio.Optional(CONF_AREA_ID): _selector("AreaSelector"),
        probatio.Required(CONF_VALVES): _selector(
            "EntitySelector", selector.EntitySelectorConfig(domain="climate", multiple=True)
        ),
        probatio.Required(CONF_SOURCES): probatio.All(cv.ensure_list, [SOURCE_SCHEMA]),
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
        probatio.Optional(CONF_OPEN_OFFSET, default=DEFAULT_OPEN_OFFSET): _number(0, 5, 0.5, "°C"),
        probatio.Optional(CONF_MIN_TEMP, default=5.0): _number(5, 20, 0.5, "°C"),
        probatio.Optional(CONF_MAX_TEMP, default=30.0): _number(15, 35, 0.5, "°C"),
        probatio.Optional(CONF_HYSTERESIS, default=DEFAULT_HYSTERESIS): _number(0, 2, 0.1, "°C"),
        probatio.Optional(CONF_OFF_SETPOINT, default=DEFAULT_OFF_SETPOINT): _number(
            5, 12, 0.5, "°C"
        ),
        probatio.Optional(
            CONF_VALVE_COMMAND_INTERVAL_SECONDS, default=DEFAULT_VALVE_COMMAND_INTERVAL_SECONDS
        ): _number(0, 3600, 1, "s"),
        probatio.Optional(CONF_WINDOW_SENSORS, default=list): _selector(
            "EntitySelector", selector.EntitySelectorConfig(multiple=True)
        ),
        probatio.Optional(CONF_DEMAND_FALLBACK_SENSORS, default=list): _selector(
            "EntitySelector", selector.EntitySelectorConfig(multiple=True)
        ),
    },
    # The room id is derived from the name, so an input that carries one is accepted.
    extra=probatio.ALLOW_EXTRA,
)

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


def _room_choice_schema(rooms: list[dict[str, Any]]) -> probatio.Schema:
    """Return a form that picks one room of the house."""
    choices = {str(room[CONF_ROOM_ID]): str(room[CONF_NAME]) for room in rooms}
    return probatio.Schema({probatio.Required(CONF_ROOM_ID): probatio.In(choices)})


class RoomClimateConfigFlow(ConfigFlow, domain=DOMAIN):
    """The house entry of one installation."""

    VERSION = 1

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
        """Return the options flow that manages the rooms."""
        return RoomClimateOptionsFlow(config_entry)


class RoomClimateOptionsFlow(OptionsFlowWithConfigEntry):
    """Add, edit and remove rooms, and edit the house settings."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        super().__init__(config_entry)
        self._room_id: str | None = None

    def _rooms(self) -> list[dict[str, Any]]:
        """The rooms of the options payload."""
        rooms = self.options.get(CONF_ROOMS, [])
        return [dict(room) for room in rooms]

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the menu of the options flow."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_room", "edit_room", "remove_room", "house_settings"],
        )

    async def async_step_add_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add one room to the house."""
        if user_input is not None:
            name = str(user_input.get(CONF_NAME, ""))
            room = _room_payload(user_input, slugify(name))
            return await self._async_save_rooms([*self._rooms(), room], "add_room")
        return self.async_show_form(step_id="add_room", data_schema=ROOM_SCHEMA)

    async def async_step_edit_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the room to edit."""
        rooms = self._rooms()
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            self._room_id = str(user_input.get(CONF_ROOM_ID, ""))
            room = self._room(self._room_id)
            if room is not None:
                return self.async_show_form(
                    step_id="edit_room_form",
                    data_schema=self.add_suggested_values_to_schema(ROOM_SCHEMA, room),
                )
        return self.async_show_form(
            step_id="edit_room",
            data_schema=_room_choice_schema(rooms),
            errors={"base": "invalid_room"} if user_input is not None else None,
        )

    async def async_step_edit_room_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the room that was chosen."""
        room_id = self._room_id or ""
        if user_input is not None:
            room = _room_payload(user_input, room_id)
            rooms = [
                room if str(existing.get(CONF_ROOM_ID, "")) == room_id else existing
                for existing in self._rooms()
            ]
            return await self._async_save_rooms(rooms, "edit_room_form")
        return self.async_show_form(
            step_id="edit_room_form",
            data_schema=self.add_suggested_values_to_schema(ROOM_SCHEMA, self._room(room_id)),
        )

    async def async_step_remove_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove the room that was chosen."""
        rooms = self._rooms()
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            room_id = str(user_input.get(CONF_ROOM_ID, ""))
            remaining = [room for room in rooms if str(room.get(CONF_ROOM_ID, "")) != room_id]
            if len(remaining) == len(rooms):
                return self.async_show_form(
                    step_id="remove_room",
                    data_schema=_room_choice_schema(rooms),
                    errors={"base": "invalid_room"},
                )
            return await self._async_save_rooms(remaining, "remove_room")
        return self.async_show_form(step_id="remove_room", data_schema=_room_choice_schema(rooms))

    async def async_step_house_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the house settings."""
        if user_input is not None:
            options = {**self.options, **user_input, CONF_ROOMS: self._rooms()}
            config, error = _validate_house(options)
            if config is not None:
                return await self._async_save(config)
            return self.async_show_form(
                step_id="house_settings",
                data_schema=self._house_schema(),
                errors={"base": error or "invalid_room"},
            )
        return self.async_show_form(step_id="house_settings", data_schema=self._house_schema())

    def _house_schema(self) -> probatio.Schema:
        """The house form, pre-filled with the current options."""
        return self.add_suggested_values_to_schema(HOUSE_SCHEMA, self.options)

    def _room(self, room_id: str) -> dict[str, Any] | None:
        """The room payload of one room id."""
        return next(
            (room for room in self._rooms() if str(room.get(CONF_ROOM_ID, "")) == room_id), None
        )

    async def _async_save_rooms(
        self, rooms: list[dict[str, Any]], step_id: str
    ) -> ConfigFlowResult:
        """Validate a room list, save it, and show the form again on a failure."""
        options = {**self.options, CONF_ROOMS: rooms}
        config, error = _validate_house(options)
        if config is None:
            return self.async_show_form(
                step_id=step_id,
                data_schema=ROOM_SCHEMA,
                errors={"base": error or "invalid_room"},
            )
        return await self._async_save(config)

    async def _async_save(self, config: HouseConfig) -> ConfigFlowResult:
        """Save the options, reload the entry, and land the room devices in their areas."""
        options = config.model_dump(mode="json")
        self.hass.config_entries.async_update_entry(self.config_entry, options=options)
        if self.config_entry.state is ConfigEntryState.LOADED:
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            self._assign_areas(config)
        return self.async_create_entry(data=options)

    def _assign_areas(self, config: HouseConfig) -> None:
        """Put the device of every room into the area the room names."""
        registry = dr.async_get(self.hass)
        for room in config.rooms:
            if room.area_id is None:
                continue
            device = registry.async_get_device_by_identifier(
                (DOMAIN, f"{self.config_entry.entry_id}_{room.room_id}"),
                self.config_entry.entry_id,
            )
            if device is not None:
                registry.async_update_device(device.id, area_id=room.area_id)
