# Insync MQTT Bridge for Home Assistant

MQTT bridge for [Insync Headless](https://www.insynchq.com/) that publishes synchronization status and controls to Home Assistant through MQTT Discovery.

The bridge is designed for Linux hosts running Insync Headless and a reachable MQTT broker (for example, the Mosquitto add-on in Home Assistant).

## Features

- Global Insync synchronization status.
- Per-account status using `insync-headless status -f <sync-folder>`.
- Per-account last **observed** transition to `SYNCED`, persisted locally across bridge restarts.
- Current file operations, including upload/download direction and percentage when present in CLI output.
- Insync error list and an error-count sensor.
- Home Assistant controls to pause/resume synchronization, start/stop Insync, and retry all errors.
- Home Assistant MQTT Discovery.
- Retains the original device identifier and original entity unique IDs used by this bridge.

## Requirements

- Linux host with Insync Headless installed and configured.
- Python 3.9 or newer.
- `paho-mqtt` 2.x.
- MQTT broker accessible from the host.
- A Home Assistant instance with the MQTT integration configured.

Install the Python dependency on Debian/Ubuntu:

```bash
sudo apt update
sudo apt install python3-paho-mqtt
```

Alternatively, install from `requirements.txt` in a virtual environment.

## Installation

### 1. Install the bridge

```bash
mkdir -p ~/.local/bin ~/.config/insync-mqtt ~/.local/state/insync-mqtt
install -m 700 insync_mqtt_bridge.py ~/.local/bin/insync_mqtt_bridge.py
```

### 2. Configure MQTT

Create the configuration file:

```bash
nano ~/.config/insync-mqtt/config.json
```

Use `config.example.json` as a template:

```json
{
  "host": "192.168.1.10",
  "port": 1883,
  "username": "insync_bridge",
  "password": "CHANGE_ME",
  "base_topic": "insync",
  "discovery_prefix": "homeassistant"
}
```

Set restrictive permissions:

```bash
chmod 700 ~/.config/insync-mqtt
chmod 600 ~/.config/insync-mqtt/config.json
```

Do not commit your real `config.json` or MQTT password to Git.

### 3. Configure Google Drive accounts

Edit the `ACCOUNTS` list near the top of `insync_mqtt_bridge.py`.

Each entry contains:

- `email`: account label shown in Home Assistant.
- `path`: absolute path to that account's local Insync sync folder.
- `id`: stable, unique identifier used in MQTT topics and entity IDs. Use lowercase letters, digits and underscores; do not change it after deployment unless you intend to create new Home Assistant entities.

Example:

```python
ACCOUNTS = [
    {
        "email": "person@example.com",
        "path": "/home/person/Insync/person@example.com",
        "id": "person_example",
    },
]
```

The Linux user running the bridge must be able to execute `insync-headless` and read the configured sync folders.

### 4. Install the systemd user service

Copy the service file:

```bash
mkdir -p ~/.config/systemd/user
install -m 644 systemd/insync-mqtt-bridge.service \
  ~/.config/systemd/user/insync-mqtt-bridge.service
```

Reload systemd and enable the bridge:

```bash
systemctl --user daemon-reload
systemctl --user enable --now insync-mqtt-bridge.service
```

To keep the user service running after logout and start it at boot:

```bash
sudo loginctl enable-linger "$USER"
```

### 5. Check the service

```bash
systemctl --user status insync-mqtt-bridge.service --no-pager
journalctl --user -u insync-mqtt-bridge.service -n 50 --no-pager
```

## Home Assistant entities

The bridge publishes MQTT Discovery entities under the `homeassistant` discovery prefix by default.

Existing entities retained for compatibility:

- `sensor.insync_proxmox_status`
- `sensor.insync_proxmox_accounts`
- `sensor.insync_proxmox_activity`
- `sensor.insync_proxmox_errors`
- `switch.insync_proxmox_sync`
- Buttons for start, stop and retrying errors

Additional entities are created for every configured account:

- Account synchronization status.
- Last observed successful synchronization timestamp.

There are also sensors for the last error reported by the Insync CLI.

Exact entity IDs can be affected by existing Home Assistant entity registry entries and naming conflicts.

## MQTT topics

Default base topic: `insync`.

| Topic | Purpose |
|---|---|
| `insync/availability` | Bridge availability (`online` / `offline`) |
| `insync/status` | Global Insync status |
| `insync/accounts` | Account list and per-account status attributes |
| `insync/accounts/count` | Number of accounts returned by Insync |
| `insync/activity` | Current operation details |
| `insync/activity/count` | Number of current operations |
| `insync/errors` | Error list |
| `insync/errors/count` | Number of errors |
| `insync/last_error` | Last item in the CLI error list |
| `insync/sync/state` | Pause/resume switch state |
| `insync/sync/set` | Pause/resume command (`ON` / `OFF`) |
| `insync/button/start/press` | Start command (`PRESS`) |
| `insync/button/stop/press` | Stop command (`PRESS`) |
| `insync/button/retry_errors/press` | Retry all errors (`PRESS`) |
| `insync/account/<id>/status` | Per-account status |
| `insync/account/<id>/attributes` | Per-account metadata and matching errors |
| `insync/account/<id>/last_success` | Last observed transition to `SYNCED` |

## Timestamp semantics

The bridge does not obtain a historical “last sync” timestamp from Google Drive or Insync. It records the local time when it observes an account transition from a previously recorded non-`SYNCED` state to `SYNCED`.

Consequences:

- The timestamp is persisted in `~/.local/state/insync-mqtt/state.json`.
- On the first run, an account that is already `SYNCED` has no known transition timestamp, so the entity reports `unknown`.
- The timestamp is the bridge's observation time, not a server-side Google Drive timestamp.
- If the bridge is stopped while synchronization starts and finishes, that transition may not be observed.

## Security and privacy

- Keep `~/.config/insync-mqtt/config.json` private (`0600`).
- Do not commit passwords, tokens, personal account data, or local filesystem paths.
- MQTT credentials should have only the permissions needed for the bridge's topics.
- The bridge publishes local sync-folder paths in per-account attributes. Review this before exposing MQTT topics to untrusted clients.

## Troubleshooting

View logs:

```bash
journalctl --user -u insync-mqtt-bridge.service -f
```

Check Insync directly:

```bash
insync-headless status
insync-headless status -f "/path/to/sync-folder"
insync-headless error list
```

Restart the bridge:

```bash
systemctl --user restart insync-mqtt-bridge.service
```

## Limitations

- Parsing of `insync-headless status` and `insync-headless error list` depends on the CLI's human-readable output. Changes in Insync CLI formatting may require parser updates.
- File operations are reported as Insync presents them; the CLI output does not always identify the Google Drive account for each operation.
- The sync switch represents pause/resume state. An account can be in `ERROR` while the switch remains `ON`.
- This project is an independent community integration and is not affiliated with or endorsed by Insync or Google.

## License

This project is licensed under the [MIT License](LICENSE).

You are free to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the software, subject to the conditions in the license.
