"""A read-only probe of the live Room Climate installation.

The probe reads the long-lived token from ``~/.hermes/ha_token`` and calls the read-only
``/api/states`` endpoint of ``http://192.168.1.7:8123``. It prints the Room Climate thermostats
with their attributes, the Hive boiler and its target, the house status sensor, and every valve
feed entity. It sends no service call and it writes to no entity.
"""

from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.request
from typing import Any

BASE_URL = "http://192.168.1.7:8123"
TOKEN_PATH = pathlib.Path.home() / ".hermes" / "ha_token"
TIMEOUT_SECONDS = 15.0

BOILER_ENTITY = "climate.heating_heat"
STATUS_ENTITY = "sensor.room_climate_status"
THERMOSTAT_PREFIX = "climate.room_climate_"
FEED_PREFIX = "number."
FEED_SUFFIX = "_external_measured_room_sensor"


def _token() -> str:
    """Return the long-lived token of the instance."""
    return TOKEN_PATH.read_text().strip()


def _states() -> dict[str, Any]:
    """Return every entity state of the instance, keyed by entity id."""
    request = urllib.request.Request(
        f"{BASE_URL}/api/states", headers={"Authorization": f"Bearer {_token()}"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        payload: list[dict[str, Any]] = json.loads(response.read().decode("utf-8"))
    return {state["entity_id"]: state for state in payload}


def _print_entity(state: dict[str, Any] | None, entity_id: str) -> None:
    """Print the state and the attributes of one entity, or its absence."""
    if state is None:
        print(f"{entity_id}: absent")
        return
    print(f"{entity_id}: {state['state']}")
    attributes: dict[str, Any] = state.get("attributes", {})
    for name in sorted(attributes):
        print(f"    {name} = {attributes[name]!r}")


def main() -> int:
    """Print the live Room Climate state, and return the exit status."""
    print(f"instance: {BASE_URL}")
    if not TOKEN_PATH.is_file():
        print(f"token: absent at {TOKEN_PATH}")
        return 1
    print(f"token: read from {TOKEN_PATH}, not printed")
    try:
        states = _states()
    except (urllib.error.URLError, ValueError) as error:
        print(f"cannot read {BASE_URL}: {error}")
        return 1

    thermostats = sorted(
        entity_id for entity_id in states if entity_id.startswith(THERMOSTAT_PREFIX)
    )
    print(f"\nroom thermostats: {len(thermostats)}")
    for entity_id in thermostats:
        _print_entity(states[entity_id], entity_id)

    print("\nboiler")
    boiler = states.get(BOILER_ENTITY)
    _print_entity(boiler, BOILER_ENTITY)
    if boiler is not None:
        print(f"    target = {boiler['attributes'].get('temperature')}")

    print("\nstatus")
    _print_entity(states.get(STATUS_ENTITY), STATUS_ENTITY)

    feeds = sorted(
        entity_id
        for entity_id in states
        if entity_id.startswith(FEED_PREFIX) and entity_id.endswith(FEED_SUFFIX)
    )
    print(f"\nfeed entities: {len(feeds)}")
    for entity_id in feeds:
        print(f"{entity_id} = {states[entity_id]['state']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
