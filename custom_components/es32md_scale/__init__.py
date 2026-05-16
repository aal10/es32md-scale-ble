"""ES-32MD Scale BLE integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import struct
import uuid
from datetime import date, datetime
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_register_callback,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.const import Platform

from .const import (
    ACTION_CONFIRM_PREFIX,
    ACTION_DENY_PREFIX,
    CONF_CONFIRMATION_TIMEOUT,
    CONF_HEIGHT_UNIT,
    CONF_SCALE_MAC,
    CONF_USER_AUTO_ASSIGN,
    CONF_USER_BIRTH_DATE,
    CONF_USER_GENDER,
    CONF_USER_GARMIN_EMAIL,
    CONF_USER_GARMIN_PASSWORD,
    CONF_USER_HEIGHT,
    CONF_USER_IS_ATHLETE,
    CONF_USER_NAME,
    CONF_USER_NOTIFY_TARGET,
    CONF_USER_SLUG,
    CONF_USER_WEIGHT_RANGE_MAX,
    CONF_USER_WEIGHT_RANGE_MIN,
    CONF_USERS,
    CONF_WEIGHT_UNIT,
    DATA_PENDING,
    DATA_SENSORS,
    DEFAULT_CONFIRMATION_TIMEOUT,
    DOMAIN,
    HEIGHT_UNIT_CM,
    HEIGHT_UNIT_IN,
    MAC_HEADER_LENGTH,
    MANUFACTURER_ID,
    PAYLOAD_DATA_LENGTH,
    PAYLOAD_STATUS_BYTE,
    PAYLOAD_WEIGHT_LOW,
    RENPHO_HEADER,
    STABLE_STATUSES,
    WEIGHT_UNIT_KG,
    WEIGHT_UNIT_LBS,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USER_NAME): cv.string,
        vol.Optional(CONF_USER_SLUG): cv.string,
        vol.Required(CONF_USER_HEIGHT): vol.Coerce(float),
        vol.Required(CONF_USER_BIRTH_DATE): cv.string,
        vol.Required(CONF_USER_GENDER): vol.In(["male", "female"]),
        vol.Optional(CONF_USER_IS_ATHLETE, default=False): cv.boolean,
        vol.Required(CONF_USER_WEIGHT_RANGE_MIN): vol.Coerce(float),
        vol.Required(CONF_USER_WEIGHT_RANGE_MAX): vol.Coerce(float),
        vol.Optional(CONF_USER_NOTIFY_TARGET): cv.string,
        vol.Optional(CONF_USER_AUTO_ASSIGN, default=False): cv.boolean,
        vol.Optional(CONF_USER_GARMIN_EMAIL): cv.string,
        vol.Optional(CONF_USER_GARMIN_PASSWORD): cv.string,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_SCALE_MAC): cv.string,
                vol.Optional(CONF_WEIGHT_UNIT, default=WEIGHT_UNIT_LBS): vol.In(
                    [WEIGHT_UNIT_KG, WEIGHT_UNIT_LBS]
                ),
                vol.Optional(CONF_HEIGHT_UNIT, default=HEIGHT_UNIT_IN): vol.In(
                    [HEIGHT_UNIT_CM, HEIGHT_UNIT_IN]
                ),
                vol.Optional(
                    CONF_CONFIRMATION_TIMEOUT, default=DEFAULT_CONFIRMATION_TIMEOUT
                ): vol.Coerce(int),
                vol.Required(CONF_USERS): vol.All(cv.ensure_list, [USER_SCHEMA]),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def _calculate_age(birth_date_str: str) -> int:
    birth = datetime.strptime(birth_date_str, "%Y-%m-%d").date()
    today = date.today()
    return (
        today.year
        - birth.year
        - ((today.month, today.day) < (birth.month, birth.day))
    )


def _height_to_cm(height: float, unit: str) -> float:
    if unit == HEIGHT_UNIT_IN:
        return height * 2.54
    return float(height)


def _calculate_bmi(weight_kg: float, height_cm: float) -> float:
    height_m = height_cm / 100.0
    if height_m <= 0:
        return 0.0
    return round(weight_kg / (height_m ** 2), 1)


def _calculate_body_fat(weight_kg, height_cm, age, is_male, is_athlete):
    bmi = _calculate_bmi(weight_kg, height_cm)
    sex = 1.0 if is_male else 0.0
    body_fat = (1.20 * bmi) + (0.23 * age) - (10.8 * sex) - 5.4
    if is_athlete:
        body_fat -= 5.0
    return round(max(0.0, min(60.0, body_fat)), 1)


def _calculate_bmr(weight_kg, height_cm, age, is_male):
    if is_male:
        bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + 5
    else:
        bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) - 161
    return round(max(0.0, bmr), 0)


def _calculate_body_water(body_fat_pct):
    lean_pct = 100.0 - body_fat_pct
    return round(max(0.0, min(80.0, lean_pct * 0.732)), 1)


def _build_measurements(weight_kg, user, weight_unit, height_unit):
    height_cm = _height_to_cm(user[CONF_USER_HEIGHT], height_unit)
    age = _calculate_age(user[CONF_USER_BIRTH_DATE])
    is_male = user[CONF_USER_GENDER] == "male"
    is_athlete = user.get(CONF_USER_IS_ATHLETE, False)
    bmi = _calculate_bmi(weight_kg, height_cm)
    body_fat_pct = _calculate_body_fat(weight_kg, height_cm, age, is_male, is_athlete)
    lean_mass_kg = round(weight_kg * (1 - body_fat_pct / 100), 2)
    fat_mass_kg = round(weight_kg * (body_fat_pct / 100), 2)
    water_pct = _calculate_body_water(body_fat_pct)
    bmr = _calculate_bmr(weight_kg, height_cm, age, is_male)
    if weight_unit == WEIGHT_UNIT_LBS:
        display_weight = round(weight_kg * 2.20462, 1)
        display_lean = round(lean_mass_kg * 2.20462, 1)
        display_fat = round(fat_mass_kg * 2.20462, 1)
    else:
        display_weight = round(weight_kg, 2)
        display_lean = round(lean_mass_kg, 2)
        display_fat = round(fat_mass_kg, 2)
    return {
        "weight": display_weight,
        "bmi": bmi,
        "body_fat": body_fat_pct,
        "lean_mass": display_lean,
        "fat_mass": display_fat,
        "body_water": water_pct,
        "bmr": bmr,
    }


def _decode_payload(service_info, scale_mac):
    target_mac = scale_mac.upper().replace("-", ":")
    _LOGGER.debug("_decode_payload: address=%s target=%s match=%s",
        service_info.address.upper(), target_mac,
        service_info.address.upper() == target_mac)
    if service_info.address.upper() != target_mac:
        return None
    manufacturer_data = service_info.manufacturer_data
    _LOGGER.debug("manufacturer_data keys: %s types: %s",
        list(manufacturer_data.keys()),
        [type(k).__name__ for k in manufacturer_data.keys()])
    raw = manufacturer_data.get(MANUFACTURER_ID) or manufacturer_data.get(str(MANUFACTURER_ID))
    _LOGGER.debug("raw lookup result: %s", raw.hex() if isinstance(raw, (bytes, bytearray)) else raw)
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = bytes.fromhex(raw)
        except ValueError:
            return None
    if len(raw) < MAC_HEADER_LENGTH + PAYLOAD_DATA_LENGTH:
        return None
    if raw[:2] != RENPHO_HEADER:
        return None
    data = raw[MAC_HEADER_LENGTH: MAC_HEADER_LENGTH + PAYLOAD_DATA_LENGTH]
    status = data[PAYLOAD_STATUS_BYTE]
    if status not in STABLE_STATUSES:
        return None
    weight_raw = struct.unpack_from("<H", data, PAYLOAD_WEIGHT_LOW)[0]
    weight_kg = weight_raw / 100.0
    return weight_kg if weight_kg > 0 else None


def _match_user(weight_kg, users):
    candidates = [
        u for u in users
        if u[CONF_USER_WEIGHT_RANGE_MIN] <= weight_kg <= u[CONF_USER_WEIGHT_RANGE_MAX]
    ]
    if not candidates:
        _LOGGER.warning("No user matched weight %.2f kg", weight_kg)
        return None
    if len(candidates) == 1:
        return candidates[0]
    def distance(u):
        mid = (u[CONF_USER_WEIGHT_RANGE_MIN] + u[CONF_USER_WEIGHT_RANGE_MAX]) / 2
        return abs(weight_kg - mid)
    return min(candidates, key=distance)


async def _sync_to_garmin(hass, user, weight_kg, measurements):
    email = user.get(CONF_USER_GARMIN_EMAIL)
    password = user.get(CONF_USER_GARMIN_PASSWORD)
    if not email or not password:
        return
    from datetime import datetime as dt
    cache_key = f"garmin_client_{email}"
    def _do_sync():
        from garminconnect import Garmin
        client = hass.data[DOMAIN].get(cache_key)
        if client is None:
            client = Garmin(email, password)
            client.login()
            hass.data[DOMAIN][cache_key] = client
        try:
            timestamp = dt.now().strftime("%Y-%m-%dT%H:%M:%S")
            client.add_body_composition(
                timestamp=timestamp,
                weight=weight_kg,
                percent_fat=measurements.get("body_fat"),
                bmi=measurements.get("bmi"),
            )
        except Exception as err:
            _LOGGER.warning("Garmin sync failed, retrying: %s", err)
            hass.data[DOMAIN].pop(cache_key, None)
            client = Garmin(email, password)
            client.login()
            hass.data[DOMAIN][cache_key] = client
            timestamp = dt.now().strftime("%Y-%m-%dT%H:%M:%S")
            client.add_body_composition(
                timestamp=timestamp,
                weight=weight_kg,
                percent_fat=measurements.get("body_fat"),
                bmi=measurements.get("bmi"),
            )
    try:
        await hass.async_add_executor_job(_do_sync)
        _LOGGER.info("Synced to Garmin for %s: %.2f kg", user[CONF_USER_NAME], weight_kg)
        notify_target = user.get(CONF_USER_NOTIFY_TARGET)
        if notify_target:
            parts = notify_target.split(".", 1)
            if len(parts) == 2:
                display_weight = measurements.get("weight", round(weight_kg, 2))
                unit_label = "lbs" if abs(display_weight - weight_kg) > 1 else "kg"
                await hass.services.async_call(
                    parts[0], parts[1],
                    {
                        "title": "Garmin Sync \u2713",
                        "message": (
                            f"Uploaded to Garmin Connect: {display_weight} {unit_label}, "
                            f"{measurements.get('body_fat')}% body fat, BMI {measurements.get('bmi')}"
                        ),
                        "data": {"tag": "es32md_garmin_sync", "persistent": False},
                    },
                )
    except Exception as err:
        _LOGGER.error("Garmin sync failed for %s: %s", user[CONF_USER_NAME], err)
        notify_target = user.get(CONF_USER_NOTIFY_TARGET)
        if notify_target:
            parts = notify_target.split(".", 1)
            if len(parts) == 2:
                try:
                    await hass.services.async_call(
                        parts[0], parts[1],
                        {
                            "title": "Garmin Sync Failed",
                            "message": f"Could not upload to Garmin Connect: {err}",
                            "data": {"tag": "es32md_garmin_sync"},
                        },
                    )
                except Exception:
                    pass


def _push_to_sensors(hass, slug, measurements):
    sensors = hass.data[DOMAIN].get(DATA_SENSORS, {})
    for sensor_key, sensor in sensors.items():
        if sensor_key.startswith(slug + "_"):
            metric = sensor_key[len(slug) + 1:]
            if metric in measurements:
                sensor.update_value(measurements[metric])


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    if DOMAIN not in config:
        return True
    domain_config = config[DOMAIN]
    scale_mac = domain_config[CONF_SCALE_MAC]
    weight_unit = domain_config[CONF_WEIGHT_UNIT]
    height_unit = domain_config[CONF_HEIGHT_UNIT]
    confirmation_timeout = domain_config.get(CONF_CONFIRMATION_TIMEOUT, DEFAULT_CONFIRMATION_TIMEOUT)
    users = domain_config[CONF_USERS]
    for user in users:
        if CONF_USER_SLUG not in user:
            user[CONF_USER_SLUG] = user[CONF_USER_NAME].lower().replace(" ", "_")
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][DATA_SENSORS] = {}
    hass.data[DOMAIN][DATA_PENDING] = {}
    hass.data[DOMAIN]["last_reading_time"] = 0.0
    hass.data[DOMAIN]["config"] = {
        CONF_SCALE_MAC: scale_mac,
        CONF_WEIGHT_UNIT: weight_unit,
        CONF_HEIGHT_UNIT: height_unit,
        CONF_USERS: users,
    }
    await async_load_platform(hass, Platform.SENSOR, DOMAIN, {}, config)

    async def _send_confirmation_notifications(reading_id, user, measurements, weight_unit):
        notify_target = user.get(CONF_USER_NOTIFY_TARGET)
        if not notify_target:
            return
        weight = measurements["weight"]
        unit_label = "lbs" if weight_unit == WEIGHT_UNIT_LBS else "kg"
        user_name = user[CONF_USER_NAME]
        slug = user[CONF_USER_SLUG]
        confirm_action = f"{ACTION_CONFIRM_PREFIX}{reading_id}_{slug}"
        deny_action = f"{ACTION_DENY_PREFIX}{reading_id}_{slug}"
        parts = notify_target.split(".", 1)
        if len(parts) != 2:
            _LOGGER.error("Invalid notify_target format: %s", notify_target)
            return
        notify_domain, notify_service = parts
        await hass.services.async_call(
            notify_domain, notify_service,
            {
                "title": "Scale Reading",
                "message": (
                    f"New reading: {weight} {unit_label}\n"
                    f"Body fat: {measurements['body_fat']}%  |  BMI: {measurements['bmi']}\n"
                    f"Is this you, {user_name}?"
                ),
                "data": {
                    "actions": [
                        {"action": confirm_action, "title": "Yes, it's me"},
                        {"action": deny_action, "title": "Not me"},
                    ],
                    "tag": f"es32md_{reading_id}",
                    "persistent": False,
                },
            },
        )
        _LOGGER.info("Sent confirmation notification to %s", user_name)

    async def _dismiss_notifications(reading_id, users):
        for user in users:
            notify_target = user.get(CONF_USER_NOTIFY_TARGET)
            if not notify_target:
                continue
            try:
                parts = notify_target.split(".", 1)
                if len(parts) != 2:
                    continue
                notify_domain, notify_service = parts
                await hass.services.async_call(
                    notify_domain, notify_service,
                    {"message": "clear_notification", "data": {"tag": f"es32md_{reading_id}"}},
                )
            except Exception:
                pass

    async def _handle_confirmation_timeout(reading_id, candidate_users):
        await asyncio.sleep(confirmation_timeout)
        pending = hass.data[DOMAIN].get(DATA_PENDING, {})
        if reading_id not in pending:
            return
        del pending[reading_id]
        await _dismiss_notifications(reading_id, candidate_users)
        _LOGGER.info("Reading %s discarded after %ds timeout", reading_id, confirmation_timeout)

    @callback
    def _handle_mobile_app_notification_action(event) -> None:
        action = event.data.get("action", "")
        if action.startswith(ACTION_CONFIRM_PREFIX):
            remainder = action[len(ACTION_CONFIRM_PREFIX):]
            if len(remainder) < 38:
                return
            reading_id = remainder[:36]
            slug = remainder[37:]
            pending = hass.data[DOMAIN].get(DATA_PENDING, {})
            if reading_id not in pending:
                return
            entry = pending.pop(reading_id)
            measurements = entry["measurements_by_slug"].get(slug)
            candidate_users = entry["candidate_users"]
            if measurements is None:
                return
            _push_to_sensors(hass, slug, measurements)
            _LOGGER.info("Measurement confirmed by %s", slug)
            confirmed_user = next((u for u in candidate_users if u[CONF_USER_SLUG] == slug), None)
            if confirmed_user:
                hass.async_create_task(
                    _sync_to_garmin(hass, confirmed_user, entry["weight_kg"], measurements)
                )
            hass.async_create_task(_dismiss_notifications(reading_id, candidate_users))
        elif action.startswith(ACTION_DENY_PREFIX):
            remainder = action[len(ACTION_DENY_PREFIX):]
            if len(remainder) < 38:
                return
            slug = remainder[37:]
            _LOGGER.debug("%s denied the measurement", slug)

    hass.bus.async_listen("mobile_app_notification_action", _handle_mobile_app_notification_action)

    @callback
    def _bluetooth_callback(service_info: BluetoothServiceInfoBleak, change: BluetoothChange) -> None:
        import time
        _LOGGER.debug("BLE callback fired: address=%s", service_info.address)
        weight_kg = _decode_payload(service_info, scale_mac)
        if weight_kg is None:
            return
        now = time.monotonic()
        last = hass.data[DOMAIN].get("last_reading_time", 0.0)
        if now - last < 30.0:
            _LOGGER.debug("Debouncing reading %.2f kg", weight_kg)
            return
        hass.data[DOMAIN]["last_reading_time"] = now
        user = _match_user(weight_kg, users)
        if user is None:
            return
        slug = user[CONF_USER_SLUG]
        _LOGGER.info("Stable measurement: %.2f kg -> candidate: %s", weight_kg, slug)
        if user.get(CONF_USER_AUTO_ASSIGN, False):
            measurements = _build_measurements(weight_kg, user, weight_unit, height_unit)
            _push_to_sensors(hass, slug, measurements)
            _LOGGER.info("Auto-assigned measurement to %s", user[CONF_USER_NAME])
            return
        candidate_users = [
            u for u in users
            if (
                u[CONF_USER_WEIGHT_RANGE_MIN] <= weight_kg <= u[CONF_USER_WEIGHT_RANGE_MAX]
                and not u.get(CONF_USER_AUTO_ASSIGN, False)
                and u.get(CONF_USER_NOTIFY_TARGET)
            )
        ]
        if not candidate_users:
            measurements = _build_measurements(weight_kg, user, weight_unit, height_unit)
            _push_to_sensors(hass, slug, measurements)
            return
        reading_id = str(uuid.uuid4())
        measurements_by_slug = {
            u[CONF_USER_SLUG]: _build_measurements(weight_kg, u, weight_unit, height_unit)
            for u in candidate_users
        }
        hass.data[DOMAIN][DATA_PENDING][reading_id] = {
            "weight_kg": weight_kg,
            "measurements_by_slug": measurements_by_slug,
            "candidate_users": candidate_users,
        }
        async def _notify_and_timeout() -> None:
            for candidate in candidate_users:
                await _send_confirmation_notifications(
                    reading_id, candidate,
                    measurements_by_slug[candidate[CONF_USER_SLUG]],
                    weight_unit,
                )
            await _handle_confirmation_timeout(reading_id, candidate_users)
        hass.async_create_task(_notify_and_timeout())

    cancel = async_register_callback(
        hass, _bluetooth_callback,
        {"address": scale_mac.upper()},
        BluetoothScanningMode.PASSIVE,
    )
    hass.data[DOMAIN]["cancel_bluetooth"] = cancel
    _LOGGER.info("ES-32MD Scale BLE ready. MAC: %s | Users: %s | Timeout: %ds",
        scale_mac, ", ".join(u[CONF_USER_NAME] for u in users), confirmation_timeout)
    return True
