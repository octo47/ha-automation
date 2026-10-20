"""The device and cycle helpers the entity platforms share."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import RoomClimateCoordinator
from .models import RoomCommand, RoomConfig


def house_device(coordinator: RoomClimateCoordinator) -> DeviceInfo:
    """Return the device that carries the entities of the house."""
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.entry.entry_id)},
        name=coordinator.config.name,
    )


def room_device(coordinator: RoomClimateCoordinator, room: RoomConfig) -> DeviceInfo:
    """Return the device that carries the entities of one room."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_{room.room_id}")},
        name=f"{coordinator.config.name} {room.name}",
    )


def room_command(coordinator: RoomClimateCoordinator, room_id: str) -> RoomCommand | None:
    """Return the commands of the last cycle for one room."""
    output = coordinator.output
    if output is None:
        return None
    return next((command for command in output.rooms if command.room_id == room_id), None)
