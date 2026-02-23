"""Camera platform for OnlyCat."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityDescription
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .data.event import Event, EventUpdate
from .image import IMAGE_BASEURL

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .api import OnlyCatApiClient
    from .data.__init__ import OnlyCatConfigEntry
    from .data.device import Device

ENTITY_DESCRIPTION = CameraEntityDescription(
    key="OnlyCat",
    name="Last activity video",
    translation_key="onlycat_event_video",
)

VIDEO_BASEURL = "https://gateway.onlycat.com/sharing/video/"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OnlyCatConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the camera platform."""
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
    if events and len(events) > 0:
        events.sort(key=lambda e: e.get("timestamp"), reverse=True)
        for entity in entities:
            for event in events:
                if event.get("deviceId") == entity.device.device_id:
                    await entity.update_event(Event.from_api_response(event))
                    break


class OnlyCatCamera(Camera):
    """OnlyCat camera class for recent video events."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info to map to a device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.device_id)},
            name=self.device.description,
            serial_number=self.device.device_id,
        )

    def __init__(
        self,
        hass: HomeAssistant,
        device: Device,
        api_client: OnlyCatApiClient,
    ) -> None:
        """Initialize the camera class."""
        Camera.__init__(self)
        self.entity_description = ENTITY_DESCRIPTION
        self.device: Device = device
        self._current_event: Event = Event()
        self._attr_unique_id = (
            device.device_id.replace("-", "_").lower() + "_last_activity_video"
        )
        self.entity_id = "camera." + self._attr_unique_id
        self._image_url: str | None = None
        self._stream_url: str | None = None
        self._api_client = api_client
        
        api_client.add_event_listener("eventUpdate", self.on_event_update)
        api_client.add_event_listener("deviceEventUpdate", self.on_event_update)

    async def on_event_update(self, data: dict) -> None:
        """Handle event update event."""
        if data["deviceId"] != self.device.device_id:
            return
        event_update = EventUpdate.from_api_response(data)
        _LOGGER.debug(
            "Processing event update for camera: %s: %s",
            self.device.device_id,
            str(data),
        )
        if event_update.event_id != self._current_event.event_id:
            self._current_event = event_update.event
            self._current_event.device_id = event_update.device_id
            self._current_event.event_id = event_update.event_id
        self._current_event.update_from(event_update.event)
        self._update_urls()
        self.async_write_ha_state()

    async def update_event(self, event: Event) -> None:
        """Update with event data."""
        _LOGGER.debug(
            "Updating event for device camera %s: %s", self.device.device_id, str(event)
        )
        self._current_event = event
        self._update_urls()
        self.async_write_ha_state()

    def _update_urls(self) -> None:
        """Update internal URLs based on the current event."""
        if not self._current_event or not self._current_event.event_id:
            return

        frame_to_show = (
            self._current_event.poster_frame_index
            if self._current_event.poster_frame_index is not None
            else self._current_event.frame_count / 2
            if self._current_event.frame_count is not None
            else 1
        )
        
        self._image_url = (
            IMAGE_BASEURL
            + str(self._current_event.device_id)
            + "/"
            + str(self._current_event.event_id)
            + "/"
            + str(int(frame_to_show))
        )
        
        if self._current_event.access_token:
            self._stream_url = (
                VIDEO_BASEURL
                + f"{self._current_event.device_id}/{self._current_event.event_id}"
                + f"?t={self._current_event.access_token}"
            )
        else:
            self._stream_url = None

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        return self._stream_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return bytes of camera image."""
        if not self._image_url:
            return None
            
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(self._image_url) as response:
                response.raise_for_status()
                return await response.read()
        except Exception as err:
            _LOGGER.error("Error fetching onlycat camera image: %s", err)
            return None
