"""Camera platform for OnlyCat."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera
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
VIDEO_BASEURL = "https://gateway.onlycat.com/sharing/video/"


# -----------------------------------------------------------
# Setup
# -----------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: OnlyCatConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OnlyCat camera entities."""

    _LOGGER.warning("OnlyCat camera setup started")

    entities: list[OnlyCatCamera] = []

    for device in entry.runtime_data.devices:
        entities.append(
            OnlyCatCamera(
                hass=hass,
                device=device,
                api_client=entry.runtime_data.client,
            )
        )

    async_add_entities(entities)

    # -------- Initial Events laden --------
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


# -----------------------------------------------------------
# Entity
# -----------------------------------------------------------

class OnlyCatCamera(Camera):
    """OnlyCat camera entity."""

    _attr_has_entity_name = True
    _attr_name = "Last activity video"
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        device: Device,
        api_client: OnlyCatApiClient,
    ) -> None:
        """Initialize camera."""
        super().__init__()

        self.hass = hass
        self.device = device
        self._api_client = api_client

        self._current_event: Event | None = None
        self._image_url: str | None = None
        self._stream_url: str | None = None

        self._attr_unique_id = (
            device.device_id.replace("-", "_").lower()
            + "_last_activity_video"
        )

        api_client.add_event_listener("eventUpdate", self._on_event_update)
        api_client.add_event_listener("deviceEventUpdate", self._on_event_update)

    # -------------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.device_id)},
            name=self.device.description,
            serial_number=self.device.device_id,
        )

    # -------------------------------------------------------
    # Event Handling
    # -------------------------------------------------------

    async def _on_event_update(self, data: dict) -> None:
        if data.get("deviceId") != self.device.device_id:
            return

        event_update = EventUpdate.from_api_response(data)

        if (
            self._current_event is None
            or event_update.event_id != self._current_event.event_id
        ):
            self._current_event = event_update.event

        self._current_event.update_from(event_update.event)

        self._update_urls()
        self.async_write_ha_state()

    async def update_event(self, event: Event) -> None:
        """Initial event load."""
        self._current_event = event
        self._update_urls()
        self.async_write_ha_state()

    # -------------------------------------------------------
    # URL Builder
    # -------------------------------------------------------

    def _update_urls(self) -> None:
        if not self._current_event or not self._current_event.event_id:
            _LOGGER.warning("No event_id available for device %s", self.device.device_id)
            return

        frame_to_show = (
            self._current_event.poster_frame_index
            if self._current_event.poster_frame_index is not None
            else (
                self._current_event.frame_count // 2
                if self._current_event.frame_count
                else 1
            )
        )

        self._image_url = (
            f"{IMAGE_BASEURL}"
            f"{self._current_event.device_id}/"
            f"{self._current_event.event_id}/"
            f"{int(frame_to_show)}"
        )

        if self._current_event.access_token:
            self._stream_url = (
                f"{VIDEO_BASEURL}"
                f"{self._current_event.device_id}/"
                f"{self._current_event.event_id}"
                f"?t={self._current_event.access_token}"
            )
        else:
            self._stream_url = None

        _LOGGER.warning("Image URL: %s", self._image_url)
        _LOGGER.warning("Stream URL: %s", self._stream_url)

    # -------------------------------------------------------
    # Snapshot
    # -------------------------------------------------------

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        if not self._image_url:
            _LOGGER.warning("No image URL available")
            return None

        session = async_get_clientsession(self.hass)

        try:
            async with session.get(
                self._image_url,
                headers=self._api_client.get_auth_headers(),  # wichtig
            ) as resp:
                _LOGGER.warning("Image fetch status: %s", resp.status)

                if resp.status == 200:
                    return await resp.read()

        except Exception as err:
            _LOGGER.error("Camera image fetch error: %s", err)

        return None

    # -------------------------------------------------------
    # Stream
    # -------------------------------------------------------

    async def stream_source(self) -> str | None:
        return self._stream_url