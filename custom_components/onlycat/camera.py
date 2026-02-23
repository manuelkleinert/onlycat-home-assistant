"""Camera platform for OnlyCat."""

from __future__ import annotations

import logging
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OnlyCatConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities = [
        OnlyCatCamera(
            hass=hass,
            device=device,
            api_client=entry.runtime_data.client,
        )
        for device in entry.runtime_data.devices
    ]

    async_add_entities(entities)

    events = await entry.runtime_data.client.send_message(
        "getEvents", {"subscribe": True}
    )

    if events:
        events.sort(key=lambda e: e.get("timestamp"), reverse=True)

        for entity in entities:
            for event in events:
                if event.get("deviceId") == entity.device.device_id:
                    await entity.update_event(Event.from_api_response(event))
                    break


class OnlyCatCamera(Camera):
    """OnlyCat event video camera."""

    _attr_has_entity_name = True
    _attr_name = "Last activity video"

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
        self._image_url: str | None = None
        self._stream_url: str | None = None

        self._attr_unique_id = (
            device.device_id.replace("-", "_").lower()
            + "_last_activity_video"
        )

        self._attr_should_poll = False

        api_client.add_event_listener("eventUpdate", self._on_event_update)
        api_client.add_event_listener("deviceEventUpdate", self._on_event_update)

    # ------------------------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.device_id)},
            name=self.device.description,
            serial_number=self.device.device_id,
        )

    # ------------------------------------------------------------------

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
        self._current_event = event
        self._update_urls()
        self.async_write_ha_state()

    # ------------------------------------------------------------------

    def _update_urls(self) -> None:
        if not self._current_event or not self._current_event.event_id:
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

    # ------------------------------------------------------------------

    async def stream_source(self) -> str | None:
        return self._stream_url

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        if not self._image_url:
            return None

        session = async_get_clientsession(self.hass)

        try:
            async with session.get(
                self._image_url,
                headers=self._api_client.get_auth_headers(),
            ) as response:
                if response.status == 200:
                    return await response.read()

                _LOGGER.warning(
                    "Failed to fetch camera image: %s",
                    response.status,
                )

        except Exception as err:
            _LOGGER.error("Camera image fetch error: %s", err)

        return None