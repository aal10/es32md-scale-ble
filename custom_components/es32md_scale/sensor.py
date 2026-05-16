"""Sensor platform for ES-32MD Scale BLE integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfMass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_SCALE_MAC, CONF_USER_NAME, CONF_USER_SLUG, CONF_USERS,
    CONF_WEIGHT_UNIT, DATA_SENSORS, DOMAIN, WEIGHT_UNIT_KG, WEIGHT_UNIT_LBS,
)

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPES = [
    {"key": "weight", "name": "Weight", "icon": "mdi:scale-bathroom",
     "device_class": SensorDeviceClass.WEIGHT, "state_class": SensorStateClass.MEASUREMENT,
     "unit_fn": lambda wu: UnitOfMass.POUNDS if wu == WEIGHT_UNIT_LBS else UnitOfMass.KILOGRAMS},
    {"key": "bmi", "name": "BMI", "icon": "mdi:human",
     "device_class": None, "state_class": SensorStateClass.MEASUREMENT,
     "unit_fn": lambda wu: "kg/m\u00b2"},
    {"key": "body_fat", "name": "Body Fat", "icon": "mdi:water-percent",
     "device_class": None, "state_class": SensorStateClass.MEASUREMENT,
     "unit_fn": lambda wu: PERCENTAGE},
    {"key": "lean_mass", "name": "Lean Mass", "icon": "mdi:arm-flex",
     "device_class": SensorDeviceClass.WEIGHT, "state_class": SensorStateClass.MEASUREMENT,
     "unit_fn": lambda wu: UnitOfMass.POUNDS if wu == WEIGHT_UNIT_LBS else UnitOfMass.KILOGRAMS},
    {"key": "fat_mass", "name": "Fat Mass", "icon": "mdi:scale-unbalanced",
     "device_class": SensorDeviceClass.WEIGHT, "state_class": SensorStateClass.MEASUREMENT,
     "unit_fn": lambda wu: UnitOfMass.POUNDS if wu == WEIGHT_UNIT_LBS else UnitOfMass.KILOGRAMS},
    {"key": "body_water", "name": "Body Water", "icon": "mdi:water",
     "device_class": None, "state_class": SensorStateClass.MEASUREMENT,
     "unit_fn": lambda wu: PERCENTAGE},
    {"key": "bmr", "name": "BMR", "icon": "mdi:fire",
     "device_class": None, "state_class": SensorStateClass.MEASUREMENT,
     "unit_fn": lambda wu: "kcal/day"},
]


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if DOMAIN not in hass.data:
        _LOGGER.error("ES-32MD Scale integration not initialized")
        return
    domain_config = hass.data[DOMAIN].get("config", {})
    users = domain_config.get(CONF_USERS, [])
    weight_unit = domain_config.get(CONF_WEIGHT_UNIT, WEIGHT_UNIT_LBS)
    scale_mac = domain_config.get(CONF_SCALE_MAC, "Unknown")
    entities = []
    for user in users:
        for sensor_def in SENSOR_TYPES:
            sensor = ES32MDSensor(
                hass=hass,
                user_name=user[CONF_USER_NAME],
                user_slug=user[CONF_USER_SLUG],
                scale_mac=scale_mac,
                metric_key=sensor_def["key"],
                metric_name=sensor_def["name"],
                unit=sensor_def["unit_fn"](weight_unit),
                icon=sensor_def["icon"],
                device_class=sensor_def["device_class"],
                state_class=sensor_def["state_class"],
            )
            entities.append(sensor)
            hass.data[DOMAIN].setdefault(DATA_SENSORS, {})[f"{user[CONF_USER_SLUG]}_{sensor_def['key']}"] = sensor
    async_add_entities(entities, update_before_add=False)


class ES32MDSensor(RestoreEntity, SensorEntity):
    def __init__(self, hass, user_name, user_slug, scale_mac, metric_key, metric_name, unit, icon, device_class, state_class):
        self.hass = hass
        self._user_name = user_name
        self._user_slug = user_slug
        self._scale_mac = scale_mac
        self._metric_key = metric_key
        self._metric_name = metric_name
        self._unit = unit
        self._icon = icon
        self._device_class = device_class
        self._state_class = state_class
        self._state: float | None = None

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._scale_mac.replace(':', '').lower()}_{self._user_slug}_{self._metric_key}"

    @property
    def name(self):
        return f"{self._user_name} {self._metric_name}"

    @property
    def native_value(self):
        return self._state

    @property
    def native_unit_of_measurement(self):
        return self._unit

    @property
    def icon(self):
        return self._icon

    @property
    def device_class(self):
        return self._device_class

    @property
    def state_class(self):
        return self._state_class

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self._scale_mac)},
            name="ES-32MD Scale",
            manufacturer="Renpho",
            model="ES-32MD",
        )

    @property
    def extra_state_attributes(self):
        return {"user": self._user_name, "scale_mac": self._scale_mac}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable"):
            try:
                self._state = float(last_state.state)
            except ValueError:
                pass

    @callback
    def update_value(self, value: float) -> None:
        self._state = value
        self.async_write_ha_state()
