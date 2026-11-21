"""The config flow of the integration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

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
from .discovery import (
    SensorCandidate,
    ValveCandidate,
    available_temperature_sources,
    available_valves,
    picker_labels,
)
from .models import HouseConfig, RoomConfig, house_settings
from .sim import sim_source_entity, sim_valve_entity

SOURCE_ENTITY = "entity_id"
SOURCE_WEIGHT = "weight"

STEP_USER = "user"
STEP_RECONFIGURE = "reconfigure"
STEP_SOURCES = "sources"
STEP_WEIGHTS = "weights"

SOURCE_WEIGHT_DEFAULT = 1.0
SOURCE_WEIGHT_MIN = 0.1
SOURCE_WEIGHT_MAX = 10.0
SOURCE_WEIGHT_STEP = 0.1

# The room form carries no room id, and the source step orders the sources, so the
# payload of a room is the fields of the model and the flow sets those two itself.
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


def _picker_field(candidates: Sequence[ValveCandidate | SensorCandidate]) -> Any:
    """Return a multi-select over the candidates of the instance.

    The label of an option names its entity in the house, so two candidates of one
    vendor name are told apart, and the value is the entity id, so the operator reads a
    label and the flow stores an entity id.
    """
    labels = picker_labels(candidates)
    options: list[selector.SelectOptionDict] = [
        {"value": candidate.entity_id, "label": labels[candidate.entity_id]}
        for candidate in candidates
    ]
    return _selector(
        "SelectSelector", selector.SelectSelectorConfig(options=options, multiple=True)
    )


def _valve_field(candidates: Sequence[ValveCandidate]) -> Any:
    """Return the valve field: a multi-select over the valves the instance holds."""
    return _picker_field(candidates)


def _source_field(candidates: Sequence[SensorCandidate]) -> Any:
    """Return the source field: a multi-select over the temperature sensors."""
    return _picker_field(candidates)


def _weight_number() -> Any:
    """Return the box number field of one source weight."""
    return _number(SOURCE_WEIGHT_MIN, SOURCE_WEIGHT_MAX, SOURCE_WEIGHT_STEP)


def room_schema(candidates: Sequence[ValveCandidate]) -> probatio.Schema:
    """Return the first room form of a subentry flow, over the valves of the instance."""
    return probatio.Schema(
        {
            probatio.Required(CONF_NAME): _selector("TextSelector"),
            probatio.Required(CONF_VALVES): _valve_field(candidates),
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


def source_schema(candidates: Sequence[SensorCandidate]) -> probatio.Schema:
    """Return the source form of a subentry flow, over the temperature sensors."""
    return probatio.Schema(
        {probatio.Required(CONF_SOURCES): _source_field(candidates)},
        extra=probatio.ALLOW_EXTRA,
    )


def weight_labels(candidates: Sequence[SensorCandidate]) -> dict[str, str]:
    """Return the label of every weight box, and the entity id it stands for.

    The panel labels a field with its name when no translation exists, so the label a
    source carries in the picker labels its box too, and no two boxes of one step read
    the same.
    """
    labels = picker_labels(candidates)
    return {labels[candidate.entity_id]: candidate.entity_id for candidate in candidates}


def weight_schema(labels: Mapping[str, str]) -> probatio.Schema:
    """Return the weight form: one box number field per picked source, default 1."""
    return probatio.Schema(
        {
            probatio.Required(label, default=SOURCE_WEIGHT_DEFAULT): _weight_number()
            for label in labels
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


def _room_payload(
    first_answer: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    room_id: str,
) -> dict[str, Any]:
    """Build the room model payload from the answers of the room form."""
    payload = {key: value for key, value in first_answer.items() if key in ROOM_FIELDS}
    payload[CONF_SOURCES] = [dict(source) for source in sources]
    payload[CONF_ROOM_ID] = room_id
    return payload


def _weight(value: Any) -> float:
    """Return one weight of the form, and the default of one box when it carries none."""
    try:
        return float(value)
    except TypeError, ValueError:
        return SOURCE_WEIGHT_DEFAULT


def _weighted_sources(
    labels: Mapping[str, str], weights: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Build the source list of one room from the picked sources and their weights."""
    return [
        {SOURCE_ENTITY: entity_id, SOURCE_WEIGHT: _weight(weights.get(label))}
        for label, entity_id in labels.items()
    ]


def _stored_entity_id(source: Any) -> str | None:
    """Return the entity id a stored source names, or None when it names none.

    The stored payload is JSON, so the source arrives as an untyped mapping.
    """
    if isinstance(source, str):
        return source
    if not isinstance(source, Mapping):
        return None
    stored = cast("Mapping[str, Any]", source)
    entity_id = stored.get(SOURCE_ENTITY)
    return None if entity_id is None else str(entity_id)


def _stored_source_ids(data: Mapping[str, Any]) -> list[str]:
    """Return the entity ids of the sources one stored room claims."""
    return [
        entity_id
        for source in list(data.get(CONF_SOURCES) or [])
        if (entity_id := _stored_entity_id(source)) is not None
    ]


def _chosen_area(candidates: Sequence[ValveCandidate], user_input: Mapping[str, Any]) -> str | None:
    """Return the area of the first valve the operator chose."""
    valves = user_input.get(CONF_VALVES) or ()
    if isinstance(valves, str):
        valves = [valves]
    first = next(iter(valves), None)
    for candidate in candidates:
        if candidate.entity_id == first:
            return candidate.area_id
    return None


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
    """Add one room to the house, or change the room that is already there.

    The panel builds one step from one submission, and the offered sources follow the
    first chosen valve, so the room arrives in two steps: the first takes the name, the
    valves and the tuning, and the second takes the sources. A third step asks the
    weight of every source, and it appears only when the operator picked more than one.
    """

    _first_answer: dict[str, Any] | None = None
    _first_area: str | None = None
    _room_step: str = STEP_USER
    _weight_labels: dict[str, str] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add one room to the house."""
        return await self._async_step_valves(STEP_USER, user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change the valves and the sources of one room."""
        if self._reconfigure_subentry_id not in self._get_entry().subentries:
            return self.async_abort(reason="invalid_subentry")
        return await self._async_step_valves(STEP_RECONFIGURE, user_input)

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Take the temperature sources of the room the first step described."""
        if self._first_answer is None:
            return self.async_abort(reason="invalid_subentry")
        if user_input is None:
            return self._show_sources()
        picked = user_input.get(CONF_SOURCES) or ()
        labels = self._picked_labels(picked)
        if len(labels) > 1:
            self._weight_labels = labels
            return self.async_show_form(step_id=STEP_WEIGHTS, data_schema=weight_schema(labels))
        return self._save_room(_weighted_sources(labels, {}), self._source_form(), STEP_SOURCES)

    async def async_step_weights(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Take the weight of every source the operator picked."""
        labels = self._weight_labels
        if self._first_answer is None or labels is None or len(labels) < 2:
            return self.async_abort(reason="invalid_subentry")
        if user_input is None:
            return self.async_show_form(step_id=STEP_WEIGHTS, data_schema=weight_schema(labels))
        sources = _weighted_sources(labels, user_input)
        return self._save_room(sources, weight_schema(labels), STEP_WEIGHTS)

    async def _async_step_valves(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """Show the first room form, and hold its answer for the source step."""
        entry = self._get_entry()
        if HouseConfig.from_entry(entry).simulation:
            return self._step_simulated(entry, step_id, user_input)
        candidates = available_valves(self.hass, entry, exclude_subentry_id=self._exclude(step_id))
        if not candidates:
            return self.async_abort(reason="no_valves")
        if user_input is None:
            return self.async_show_form(
                step_id=step_id, data_schema=self._valve_schema(candidates, step_id)
            )
        self._room_step = step_id
        self._first_answer = dict(user_input)
        self._first_area = _chosen_area(candidates, user_input)
        return self._show_sources()

    def _show_sources(self) -> SubentryFlowResult:
        """Show the source step, over the sensors the room of the first step reads."""
        return self.async_show_form(step_id=STEP_SOURCES, data_schema=self._source_form())

    def _source_candidates(self) -> tuple[SensorCandidate, ...]:
        """Return the temperature sensors the source step may offer."""
        return available_temperature_sources(
            self.hass,
            self._get_entry(),
            first_area=self._first_area,
            exclude_subentry_id=self._exclude(self._room_step),
        )

    def _source_form(self) -> probatio.Schema:
        """Return the source step, pre-filled with the room that is being changed."""
        candidates = self._source_candidates()
        schema = source_schema(candidates)
        if self._room_step != STEP_RECONFIGURE:
            return schema
        offered = {candidate.entity_id for candidate in candidates}
        stored = _stored_source_ids(self._get_reconfigure_subentry().data)
        return self.add_suggested_values_to_schema(
            schema,
            {CONF_SOURCES: [entity_id for entity_id in stored if entity_id in offered]},
        )

    def _valve_schema(self, candidates: Sequence[ValveCandidate], step_id: str) -> probatio.Schema:
        """Return the first room form, pre-filled with the room that is being changed."""
        schema = room_schema(candidates)
        if step_id != STEP_RECONFIGURE:
            return schema
        return self.add_suggested_values_to_schema(
            schema, dict(self._get_reconfigure_subentry().data)
        )

    def _picked_labels(self, picked: Sequence[str]) -> dict[str, str]:
        """Return the label of every picked source, and the entity id it stands for."""
        chosen = [
            candidate for candidate in self._source_candidates() if candidate.entity_id in picked
        ]
        return weight_labels(chosen)

    def _save_room(
        self,
        sources: Sequence[Mapping[str, Any]],
        schema: probatio.Schema,
        step_id: str,
    ) -> SubentryFlowResult:
        """Validate the full room, save it, and show the step again when it is refused."""
        entry = self._get_entry()
        first_answer = self._first_answer or {}
        name = str(first_answer.get(CONF_NAME, ""))
        payload = _room_payload(first_answer, sources, slugify(name))
        error = self._room_error(entry, payload, self._exclude(self._room_step))
        if error is not None:
            return self.async_show_form(step_id=step_id, data_schema=schema, errors={"base": error})
        return self._finish_room(entry, self._room_step, payload, name)

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

    def _finish_room(
        self, entry: ConfigEntry, step_id: str, payload: dict[str, Any], name: str
    ) -> SubentryFlowResult:
        """Create the room, or write the change into the room that is already there."""
        if step_id == STEP_RECONFIGURE:
            return self.async_update_and_abort(
                entry, self._get_reconfigure_subentry(), title=name, data=payload
            )
        return self.async_create_entry(title=name, data=payload)

    def _exclude(self, step_id: str) -> str | None:
        """The room that a reconfigure flow must not treat as another room."""
        if step_id != STEP_RECONFIGURE:
            return None
        return self._reconfigure_subentry_id

    @staticmethod
    def _room_error(entry: ConfigEntry, payload: dict[str, Any], exclude: str | None) -> str | None:
        """Return the error key of one room form result, or None when it is sound.

        The house model claims the valves and the sources of its rooms, so a room
        that takes one already in use is refused here. The house of the entry is read
        from its settings, and no validation of the stored house can raise, because a
        refusal of the form is an error key and not a failure of the flow.
        """
        rooms = [
            dict(subentry.data)
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_ROOM and subentry.subentry_id != exclude
        ]
        rooms.append(payload)
        return _validate_house({**house_settings(entry), CONF_ROOMS: rooms})[1]


class RoomClimateOptionsFlow(OptionsFlowWithConfigEntry):
    """The house settings of one house, which the options carry."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit the house settings, the only thing the options carry."""
        if user_input is not None:
            config, error = _validate_house(
                {**self._house_settings(), **user_input, CONF_ROOMS: []}
            )
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
        """The house form, pre-filled with the house of the entry."""
        return self.add_suggested_values_to_schema(HOUSE_SCHEMA, self._house_settings())

    def _house_settings(self) -> dict[str, Any]:
        """The settings of the house this flow changes."""
        entry = self.hass.config_entries.async_get_entry(self.handler)
        return house_settings(cast("ConfigEntry", entry))
