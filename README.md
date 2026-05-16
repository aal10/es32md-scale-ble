# ES-32MD Scale BLE — Home Assistant Integration

A Home Assistant custom integration for the **Renpho ES-32MD** smart scale. Passively listens for BLE advertisements, decodes weight and body composition data, supports multiple users with push notification confirmation, and syncs to Garmin Connect.

## Features

- Passive BLE — no pairing required, works alongside the Renpho app
- Multi-user support with weight-range based matching
- Actionable push notifications — "Is this you?" with Yes/No buttons
- Auto-assign for users who don't need confirmation (e.g. children)
- Garmin Connect sync on confirmation with success/failure notification
- 14 sensors per user — all calibrated to match Renpho app values within ~1%
- Sensor state restored after HA restart
- Works with ESP32 Bluetooth proxies for extended range

## Requirements

- Home Assistant 2023.6.0 or newer
- Bluetooth integration enabled in HA
- HA Companion app on each user's phone (for push notifications)
- Renpho ES-32MD scale

## Installation via HACS

1. Open HACS in your HA sidebar
2. Click the three-dot menu → **Custom repositories**
3. Add `https://github.com/aal10/es32md-scale-ble` as an **Integration**
4. Search for **ES-32MD Scale BLE** and click **Download**
5. Restart Home Assistant

## Manual Installation

1. Copy the `custom_components/es32md_scale` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

Add the following to your `configuration.yaml`:

```yaml
es32md_scale:
  scale_mac: "XX:XX:XX:XX:XX:XX"        # Your scale's MAC address
  weight_unit: lbs                       # lbs or kg
  height_unit: in                        # in or cm
  confirmation_timeout: 300              # Seconds before unconfirmed reading is discarded (default 300)
  users:
    - name: Person 1
      slug: person1                      # Used in entity IDs (optional, auto-generated if omitted)
      height: 70                         # In the unit set by height_unit above
      birth_date: "1990-01-01"           # YYYY-MM-DD
      gender: male                       # male or female
      is_athlete: false                  # true if exercising 5+ hours/week
      weight_range_min: 70               # Always in kg — used for user matching
      weight_range_max: 100
      notify_target: notify.mobile_app_person1_phone
      auto_assign: false
      garmin_email: "person1@example.com"   # Optional — omit if no Garmin account
      garmin_password: "yourpassword"

    - name: Person 2
      slug: person2
      height: 65
      birth_date: "1995-06-15"
      gender: female
      is_athlete: false
      weight_range_min: 50
      weight_range_max: 75
      notify_target: notify.mobile_app_person2_phone
      auto_assign: false

    - name: Child
      slug: child
      height: 45
      birth_date: "2020-03-10"
      gender: male
      is_athlete: false
      weight_range_min: 10
      weight_range_max: 30
      auto_assign: true    # Skip notification, always auto-assigned
```

## Configuration Reference

### Top-level options

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `scale_mac` | Yes | — | BLE MAC address of your scale |
| `weight_unit` | No | `lbs` | Display unit: `lbs` or `kg` |
| `height_unit` | No | `in` | Height unit: `in` or `cm` |
| `confirmation_timeout` | No | `300` | Seconds to wait for confirmation before discarding reading |
| `users` | Yes | — | List of user profiles (see below) |

### Per-user options

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `name` | Yes | — | Display name |
| `slug` | No | auto | Entity ID prefix (e.g. `person1` → `sensor.person1_weight`) |
| `height` | Yes | — | Height in `height_unit` |
| `birth_date` | Yes | — | Date of birth in `YYYY-MM-DD` format |
| `gender` | Yes | — | `male` or `female` — affects body composition formulas |
| `is_athlete` | No | `false` | Set `true` if exercising 5+ hrs/week — adjusts body fat formula |
| `weight_range_min` | Yes | — | Minimum expected weight in **kg** |
| `weight_range_max` | Yes | — | Maximum expected weight in **kg** |
| `notify_target` | No | — | HA notify service for this user (e.g. `notify.mobile_app_my_phone`) |
| `auto_assign` | No | `false` | Skip confirmation notification and assign automatically |
| `garmin_email` | No | — | Garmin Connect account email |
| `garmin_password` | No | — | Garmin Connect account password |

## Finding your notify target

Go to **Developer Tools → Services** and search for `notify.mobile_app` — it will autocomplete all available mobile app notify services.

## Finding your scale MAC address

Enable **debug logging** in HA:

```yaml
logger:
  default: warning
  logs:
    custom_components.es32md_scale: debug
```

Restart HA and step on the scale. The MAC address will appear in the logs.

Alternatively, use the **nRF Connect** app on your phone — scan for BLE devices while standing on the scale and look for the device with a blank name.

## Sensors created

14 sensors are created per user, all calibrated to match Renpho app values:

| Sensor | Unit | Description |
|--------|------|-------------|
| `sensor.<slug>_weight` | lbs / kg | Body weight |
| `sensor.<slug>_bmi` | kg/m² | Body Mass Index |
| `sensor.<slug>_body_fat` | % | Body fat % (Deurenberg formula, matches Renpho within ~1%) |
| `sensor.<slug>_lean_mass` | lbs / kg | Lean body mass |
| `sensor.<slug>_fat_mass` | lbs / kg | Fat mass |
| `sensor.<slug>_body_water` | % | Body water % |
| `sensor.<slug>_bmr` | kcal/day | Basal Metabolic Rate (Katch-McArdle, matches Renpho exactly) |
| `sensor.<slug>_bone_mass` | lbs / kg | Bone mass |
| `sensor.<slug>_protein` | % | Protein % |
| `sensor.<slug>_muscle_mass` | % | Total muscle mass % |
| `sensor.<slug>_skeletal_muscle` | % | Skeletal muscle % |
| `sensor.<slug>_subcutaneous_fat` | % | Subcutaneous fat % |
| `sensor.<slug>_visceral_fat` | 1–30 | Visceral fat rating |
| `sensor.<slug>_metabolic_age` | years | Metabolic age |

## Notes

- Body composition metrics are **calculated** from weight + profile using formulas reverse-engineered from the Renpho app. The ES-32MD only broadcasts weight in its BLE advertisements — body composition values are not transmitted over BLE.
- All metrics match the Renpho app within ~1% based on verified captures.
- `weight_range_min` and `weight_range_max` are always in **kg** regardless of `weight_unit`.
- If two users have overlapping weight ranges, **both** receive a notification and the first to confirm wins.
- Garmin Connect sync uses an unofficial API via the `garminconnect` library. A 429 rate-limit warning on first login is normal — the library retries automatically and caches the session for subsequent weigh-ins.
- MFA on your Garmin account is not supported.

## Bluetooth Range

If your HA server is far from the scale, use an **ESP32 Bluetooth proxy**. Flash one at [https://esphome.github.io/bluetooth-proxies/](https://esphome.github.io/bluetooth-proxies/) and place it near the scale. No config changes needed — HA uses it automatically.

## License

MIT
