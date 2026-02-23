"""Image platform for OnlyCat."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.components.image import ImageEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .data.event import Event, EventUpdate

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from .api import OnlyCatApiClient
    from .data import OnlyCatConfigEntry
    from .data.device import Device

_LOGGER = logging.getLogger(__name__)

IMAGE_BASEURL = "https://gateway.onlycat.com/events/"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OnlyCatConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OnlyCat image entities."""
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

    # Initial events laden
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
                    await entity.update_event(Event.from_api_response(event))
                    break


class OnlyCatLastImage(ImageEntity):
    """OnlyCat last activity image entity."""

    _attr_has_entity_name = True
    _attr_name = "Last activity image"
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        hass: HomeAssistant,
        device: Device,
        api_client: OnlyCatApiClient,
    ) -> None:
        """Initialize entity."""
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

        self._attr_should_poll = False

        api_client.add_event_listener("eventUpdate", self._on_event_update)
        api_client.add_event_listener("deviceEventUpdate", self._on_event_update)

    # ------------------------------------------------------------------
    # Device Info
    # ------------------------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.device_id)},
            name=self.device.description,
            serial_number=self.device.device_id,
        )

    # ------------------------------------------------------------------
    # Event Handling
    # ------------------------------------------------------------------

    async def _on_event_update(self, data: dict) -> None:
        """Handle incoming websocket updates."""
        if data.get("deviceId") != self.device.device_id:
            return

        event_update = EventUpdate.from_api_response(data)

        if (
            self._current_event is None
            or event_update.event_id != self._current_event.event_id
        ):
            self._current_event = event_update.event

        self._current_event.update_from(event_update.event)

        self._cached_image = None

        _LOGGER.debug(
            "New event received for device %s: %s",
            self.device.device_id,
            self._current_event.event_id,
        )

        self.async_write_ha_state()

    async def update_event(self, event: Event) -> None:
        """Initial event load."""
        self._current_event = event
        self._cached_image = None
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Image Fetching
    # ------------------------------------------------------------------

    async def async_image(self) -> bytes | None:
        """Return image bytes to Home Assistant."""

        if self._cached_image:
            return self._cached_image

        if not self._current_event:
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

        _LOGGER.debug("Fetching image from %s", image_url)

        session = async_get_clientsession(self.hass)

        try:
            async with session.get(
                image_url,
                headers=self._api_client.get_auth_headers(),  # falls nötig
            ) as resp:
                if resp.status == 200:
                    self._cached_image = await resp.read()
                    return self._cached_image

                _LOGGER.warning(
                    "Failed to fetch image (%s): HTTP %s",
                    image_url,
                    resp.status,
                )

        except Exception as err:
            _LOGGER.error("Error fetching image: %s", err)

        return None