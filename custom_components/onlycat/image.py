"""Image platform for OnlyCat."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.components.image import ImageEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .data.event import Event

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from .api import OnlyCatApiClient
    from .data import OnlyCatConfigEntry
    from .data.device import Device

_LOGGER = logging.getLogger(__name__)

IMAGE_BASEURL = "https://gateway.onlycat.com/events/"


# -----------------------------------------------------------
# Setup
# -----------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: OnlyCatConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:

    entities: list[OnlyCatLastImage] = []

    for device in entry.runtime_data.devices:
        entities.append(
            OnlyCatLastImage(
                hass=hass,
                device=device,
                api_client=entry.runtime_data.client,
            )
        )

    async_add_entities(entities)

    # Initial Events laden
    events = await entry.runtime_data.client.send_message(
        "getEvents", {"subscribe": True}
    )

    if events:
        events.sort(
            key=lambda e: datetime.fromisoformat(e.get("timestamp")),
            reverse=True,
        )

        for entity in entities:
            for event in events:
                if event.get("deviceId") == entity.device.device_id:
                    entity.set_event(Event.from_api_response(event))
                    break


# -----------------------------------------------------------
# Entity
# -----------------------------------------------------------

class OnlyCatLastImage(ImageEntity):

    _attr_has_entity_name = True
    _attr_name = "Last activity image"
    _attr_content_type = "image/jpeg"
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        device: Device,
        api_client: OnlyCatApiClient,
    ) -> None:
        super().__init__()

        self.hass = hass
        self.device = device
        self._api_client = api_client

        self._current_event: Event | None = None
        self._cached_image: bytes | None = None

        self._attr_unique_id = (
            device.device_id.replace("-", "_").lower()
            + "_last_activity_image"
        )

        # Nur rohes Event-JSON verarbeiten
        api_client.add_event_listener("eventUpdate", self._handle_raw_event)

    # -----------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.device_id)},
            name=self.device.description,
            serial_number=self.device.device_id,
        )

    # -----------------------------------------------------
    # Event Handling (vereinfacht)
    # -----------------------------------------------------

    async def _handle_raw_event(self, data: dict) -> None:
        if data.get("deviceId") != self.device.device_id:
            return

        _LOGGER.warning("Raw image event received: %s", data)

        self.set_event(Event.from_api_response(data))
        self.async_update_ha_state(True)

    def set_event(self, event: Event) -> None:
        self._current_event = event
        self._cached_image = None

    # -----------------------------------------------------
    # Image Fetching
    # -----------------------------------------------------

    async def async_image(self) -> bytes | None:

        if self._cached_image:
            return self._cached_image

        if not self._current_event or not self._current_event.event_id:
            _LOGGER.warning("No valid event available for image")
            return None

        frame_to_show = (
            self._current_event.poster_frame_index
            if self._current_event.poster_frame_index is not None
            else (
                self._current_event.frame_count // 2
                if self._current_event.frame_count
                else 1
            )
        )

        image_url = (
            f"{IMAGE_BASEURL}"
            f"{self._current_event.device_id}/"
            f"{self._current_event.event_id}/"
            f"{int(frame_to_show)}"
        )

        _LOGGER.warning("Fetching image from %s", image_url)

        session = async_get_clientsession(self.hass)

        try:
            async with session.get(
                image_url,
                headers=self._api_client.get_auth_headers(),
            ) as resp:

                _LOGGER.warning("Image HTTP status: %s", resp.status)

                if resp.status == 200:
                    self._cached_image = await resp.read()
                    return self._cached_image

        except Exception as err:
            _LOGGER.error("Image fetch error: %s", err)

        return None