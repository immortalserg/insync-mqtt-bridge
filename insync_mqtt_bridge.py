#!/usr/bin/python3
"""MQTT bridge for Insync Headless and Home Assistant."""

import json
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

CONFIG = Path.home() / ".config/insync-mqtt/config.json"
cfg = json.loads(CONFIG.read_text())

HOST = cfg["host"]
PORT = cfg["port"]
USERNAME = cfg["username"]
PASSWORD = cfg["password"]
BASE = cfg.get("base_topic", "insync")
DISCOVERY = cfg.get("discovery_prefix", "homeassistant")

# Edit these entries to match the local Insync sync-folder paths.
ACCOUNTS = [
    {
        "email": "drive_one@example.com",
        "path": "/home/username/Insync/drive_one",
        "id": "drive_one",
    },
    {
        "email": "drive_two@example.com",
        "path": "/home/username/Insync/drive_two",
        "id": "drive_two",
    },
    {
        "email": "drive_three@example.com",
        "path": "/home/username/Insync/drive_three",
        "id": "drive_three",
    },
]

DEVICE = {
    "identifiers": ["insync_proxmox"],
    "name": "Insync — Proxmox",
    "manufacturer": "Insync",
    "model": "Insync Headless",
    "sw_version": "3.2.12.10784",
}

STATE_FILE = Path.home() / ".local/state/insync-mqtt/state.json"
POLL_INTERVAL = 10

connected = threading.Event()
state_lock = threading.Lock()


def command(args, input_text=None, timeout=15):
    """Run an Insync CLI command and return combined output."""
    try:
        result = subprocess.run(
            ["insync-headless"] + args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout + result.stderr
    except Exception as exc:
        return f"ERROR: {exc}"


def get_status():
    """Read global Insync status, account list and current file operations."""
    output = command(["status"], timeout=15)
    accounts = []
    status = "UNKNOWN"
    operations = []

    in_accounts = False
    in_files = False

    for raw in output.splitlines():
        line = raw.strip()

        if line == "Accounts:":
            in_accounts = True
            continue

        if line.startswith("Sync status:"):
            in_accounts = False
            status = line.split(":", 1)[1].strip().upper()
            continue

        if line == "Syncing files:":
            in_files = True
            continue

        if in_accounts and line:
            accounts.append(line)

        if in_files and line:
            operations.append(line)

    return {
        "status": status,
        "accounts": accounts,
        "operations": operations,
        "raw": output[-3000:],
    }


def get_account_status(account):
    """Read status for one local sync-folder root."""
    output = command(["status", "-f", account["path"]], timeout=15)
    lines = [line.strip() for line in output.splitlines() if line.strip()]

    if not lines:
        return "UNKNOWN"

    status = lines[0].upper()

    if status in ("SYNCED", "SYNCING", "ERROR", "PAUSED"):
        return status

    if "doesn't seem to be running" in output.lower():
        return "STOPPED"

    return "UNKNOWN"


def get_errors():
    """Return errors listed by the Insync CLI."""
    output = command(
        ["error", "list"],
        input_text="0\n",
        timeout=10,
    )
    return re.findall(r"(?m)^\s*\d+\s*-\s*(.+)$", output)


def parse_operations(lines):
    """Convert Insync operation lines into Home Assistant attributes."""
    result = []

    for line in lines:
        item = {
            "file": line,
            "raw": line,
            "direction": None,
            "progress": None,
            "details": None,
        }

        match = re.match(
            r"^(.*?)\s+-\s+(uploading|downloading)\s+(.*)$",
            line,
            re.IGNORECASE,
        )

        if match:
            item["file"] = match.group(1)
            item["direction"] = match.group(2).lower()
            item["details"] = match.group(3)

            percent = re.search(r"(\d+(?:\.\d+)?)%", match.group(3))
            if percent:
                item["progress"] = float(percent.group(1))

        elif " - scanning" in line.lower():
            item["file"] = line.rsplit(" - scanning", 1)[0]
            item["direction"] = "scanning"

        result.append(item)

    return result


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"accounts": {}}


def save_state(data):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(STATE_FILE)


persistent_state = load_state()


def update_last_success(account, status):
    """Persist the last observed transition from a non-SYNCED state to SYNCED."""
    account_id = account["id"]
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    with state_lock:
        previous = persistent_state["accounts"].get(account_id, {})
        previous_status = previous.get("status")
        last_success = previous.get("last_success")

        # This is the time observed by the bridge, not a Google Drive server timestamp.
        # On first run, an already-SYNCED account has no known transition timestamp.
        if status == "SYNCED" and previous_status and previous_status != "SYNCED":
            last_success = now

        persistent_state["accounts"][account_id] = {
            "status": status,
            "last_success": last_success,
        }

        save_state(persistent_state)
        return last_success


def publish(topic, payload, retain=True):
    client.publish(topic, str(payload), qos=1, retain=retain)


def publish_json(topic, data):
    publish(topic, json.dumps(data, ensure_ascii=False))


def discovery_config(
    component,
    object_id,
    name,
    state_topic=None,
    value_template=None,
    attributes_topic=None,
    command_topic=None,
    extra=None,
):
    unique_id = f"insync_proxmox_{object_id}"

    data = {
        "name": name,
        "unique_id": unique_id,
        "availability_topic": f"{BASE}/availability",
        "device": DEVICE,
    }

    if state_topic:
        data["state_topic"] = state_topic
    if value_template:
        data["value_template"] = value_template
    if attributes_topic:
        data["json_attributes_topic"] = attributes_topic
    if command_topic:
        data["command_topic"] = command_topic
    if extra:
        data.update(extra)

    publish(
        f"{DISCOVERY}/{component}/{unique_id}/config",
        json.dumps(data, ensure_ascii=False),
    )


def publish_discovery():
    # Keep the original entity IDs/unique IDs for compatibility.
    discovery_config(
        "sensor", "status", "Статус синхронизации",
        f"{BASE}/status",
    )

    discovery_config(
        "sensor", "accounts", "Аккаунты Google Drive",
        f"{BASE}/accounts/count",
        attributes_topic=f"{BASE}/accounts",
        extra={"icon": "mdi:google-drive"},
    )

    discovery_config(
        "sensor", "activity", "Текущие операции",
        f"{BASE}/activity/count",
        attributes_topic=f"{BASE}/activity",
        extra={"icon": "mdi:sync"},
    )

    discovery_config(
        "sensor", "errors", "Ошибки Insync",
        f"{BASE}/errors/count",
        attributes_topic=f"{BASE}/errors",
        extra={"icon": "mdi:alert-circle"},
    )

    discovery_config(
        "sensor", "last_error", "Последняя ошибка Insync",
        f"{BASE}/last_error",
        extra={"icon": "mdi:alert"},
    )

    for account in ACCOUNTS:
        aid = account["id"]

        discovery_config(
            "sensor",
            f"account_{aid}",
            f"Google Drive — {account['email']}",
            f"{BASE}/account/{aid}/status",
            attributes_topic=f"{BASE}/account/{aid}/attributes",
            extra={"icon": "mdi:google-drive"},
        )

        discovery_config(
            "sensor",
            f"last_sync_{aid}",
            f"Последняя успешная синхронизация — {account['email']}",
            f"{BASE}/account/{aid}/last_success",
            extra={
                "device_class": "timestamp",
                "icon": "mdi:clock-check",
            },
        )

    discovery_config(
        "switch", "sync", "Синхронизация Insync",
        f"{BASE}/sync/state",
        command_topic=f"{BASE}/sync/set",
        extra={
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "icon": "mdi:sync",
        },
    )

    for object_id, name in [
        ("start", "Запустить Insync"),
        ("stop", "Остановить Insync"),
        ("retry_errors", "Повторить ошибки Insync"),
    ]:
        discovery_config(
            "button",
            object_id,
            name,
            command_topic=f"{BASE}/button/{object_id}/press",
            extra={"payload_press": "PRESS"},
        )


def handle_command(topic, payload):
    if topic == f"{BASE}/sync/set":
        if payload == "ON":
            result = command(["resume"])
        elif payload == "OFF":
            result = command(["pause"])
        else:
            return

    elif topic == f"{BASE}/button/start/press" and payload == "PRESS":
        result = command(["start"])

    elif topic == f"{BASE}/button/stop/press" and payload == "PRESS":
        result = command(["quit"])

    elif topic == f"{BASE}/button/retry_errors/press" and payload == "PRESS":
        result = command(["error", "retry-all"])

    else:
        return

    publish(f"{BASE}/last_command", result[-1500:] or "OK", retain=False)


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print("MQTT connection failed:", reason_code)
        return

    print("Connected to MQTT")
    connected.set()

    client.subscribe([
        (f"{BASE}/sync/set", 1),
        (f"{BASE}/button/+/press", 1),
    ])

    publish_discovery()
    publish(f"{BASE}/availability", "online")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    connected.clear()
    print("MQTT disconnected:", reason_code)


def on_message(client, userdata, message):
    payload = message.payload.decode("utf-8", errors="replace").strip()

    threading.Thread(
        target=handle_command,
        args=(message.topic, payload),
        daemon=True,
    ).start()


client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id="insync_bridge",
)

client.username_pw_set(USERNAME, PASSWORD)
client.will_set(f"{BASE}/availability", "offline", qos=1, retain=True)
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

print("Starting Insync MQTT Bridge")

initial = get_status()
if "doesn't seem to be running" in initial["raw"].lower():
    print(command(["start"]))

client.connect(HOST, PORT, 30)
client.loop_start()

if not connected.wait(15):
    raise SystemExit("MQTT connection timeout")

try:
    while True:
        data = get_status()
        errors = get_errors()

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                account["id"]: executor.submit(get_account_status, account)
                for account in ACCOUNTS
            }
            account_statuses = {
                account["id"]: futures[account["id"]].result()
                for account in ACCOUNTS
            }

        publish(f"{BASE}/status", data["status"])

        publish(f"{BASE}/accounts/count", len(data["accounts"]))
        publish_json(f"{BASE}/accounts", {
            "accounts": data["accounts"],
            "statuses": account_statuses,
        })

        for account in ACCOUNTS:
            aid = account["id"]
            status = account_statuses[aid]
            last_success = update_last_success(account, status)

            account_errors = [
                error for error in errors
                if account["path"] in error
            ]

            publish(f"{BASE}/account/{aid}/status", status)

            publish_json(f"{BASE}/account/{aid}/attributes", {
                "email": account["email"],
                "local_path": account["path"],
                "errors_count": len(account_errors),
                "errors": account_errors,
            })

            publish(
                f"{BASE}/account/{aid}/last_success",
                last_success or "unknown",
            )

        operations = parse_operations(data["operations"])

        publish(f"{BASE}/activity/count", len(operations))
        publish_json(f"{BASE}/activity", {
            "operations": operations,
        })

        publish(f"{BASE}/errors/count", len(errors))
        publish_json(f"{BASE}/errors", {"errors": errors})

        last_error = errors[-1] if errors else "Нет ошибок"
        publish(f"{BASE}/last_error", last_error[:250])

        # The switch represents pause/resume, not error-free synchronization.
        if data["status"] == "PAUSED" or (
            "doesn't seem to be running" in data["raw"].lower()
        ):
            sync_state = "OFF"
        else:
            sync_state = "ON"

        publish(f"{BASE}/sync/state", sync_state)
        time.sleep(POLL_INTERVAL)

except KeyboardInterrupt:
    pass

finally:
    publish(f"{BASE}/availability", "offline")
    client.loop_stop()
    client.disconnect()
