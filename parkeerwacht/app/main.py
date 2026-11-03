"""Parkeerwacht: haalt camerabeelden op, bepaalt de status en meldt die via MQTT."""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import paho.mqtt.client as mqtt
from PIL import Image

import web
from parking import DEFAULT_ZONE, Detector, OccupancyTracker, annotate, read_stream_frame, validate_zone

DATA = Path(os.environ.get("PARKEERWACHT_DATA", "/data"))
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HISTORY_LIMIT = 50
MAX_FAILURES = 3
STATUS_TOPIC = "parkeerwacht/status"
STATE_TOPIC = "parkeerwacht/bezet"
ATTRIBUTES_TOPIC = "parkeerwacht/attributes"
PHOTO_TOPIC = "parkeerwacht/foto"

log = logging.getLogger("parkeerwacht")


def supervisor_get(path: str) -> bytes:
    request = urllib.request.Request(f"http://supervisor{path}", headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


class Shared:
    """Gedeelde stand tussen de meetlus en de webpagina."""

    def __init__(self, data_dir: Path):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.zone_file = data_dir / "zone.json"
        self.history_dir = data_dir / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.zone = self._load_zone()
        self.zone_changed = False
        self.occupied = None
        self.coverage = 0.0
        self.last_check = 0
        self.error = None
        self.raw_jpeg = None
        self.annotated_jpeg = None

    def _load_zone(self):
        try:
            return validate_zone(json.loads(self.zone_file.read_text()))
        except (OSError, ValueError):
            return DEFAULT_ZONE

    def set_zone(self, zone) -> None:
        with self.lock:
            self.zone = zone
            self.zone_changed = True
        self.zone_file.write_text(json.dumps(zone))
        self.wake.set()

    def history_photo(self, photo_id: str):
        path = self.history_dir / f"{photo_id}.jpg"
        return path.read_bytes() if path.exists() else None

    def add_history(self, occupied: bool, jpeg: bytes) -> None:
        photo_id = int(time.time())
        (self.history_dir / f"{photo_id}.jpg").write_bytes(jpeg)
        (self.history_dir / f"{photo_id}.json").write_text(json.dumps({"occupied": occupied}))
        for old in sorted(self.history_dir.glob("*.jpg"), key=lambda p: int(p.stem))[:-HISTORY_LIMIT]:
            old.unlink()
            old.with_suffix(".json").unlink(missing_ok=True)

    def state_dict(self) -> dict:
        history = []
        for path in sorted(self.history_dir.glob("*.json"), key=lambda p: int(p.stem), reverse=True)[:12]:
            try:
                history.append({"id": int(path.stem), **json.loads(path.read_text())})
            except (OSError, ValueError):
                continue
        with self.lock:
            return {
                "occupied": self.occupied,
                "coverage": self.coverage,
                "last_check": self.last_check,
                "error": self.error,
                "zone": self.zone,
                "history": history,
            }


class Mqtt:
    """Publiceert discovery, status en foto; alles retained, met offline bij wegvallen."""

    def __init__(self):
        config = json.loads(supervisor_get("/services/mqtt"))["data"]
        self.online = False
        self.last = {}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="parkeerwacht")
        self.client.username_pw_set(config.get("username"), config.get("password"))
        self.client.will_set(STATUS_TOPIC, "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect_async(config["host"], int(config["port"]))
        self.client.loop_start()

    def _discovery(self) -> None:
        device = {"identifiers": ["parkeerwacht"], "name": "Parkeerplek", "manufacturer": "Parkeerwacht"}
        common = {"device": device, "availability_topic": STATUS_TOPIC}
        self.client.publish(
            "homeassistant/binary_sensor/parkeerwacht/bezet/config",
            json.dumps({
                **common,
                "name": "Bezet",
                "unique_id": "parkeerwacht_bezet",
                "device_class": "occupancy",
                "state_topic": STATE_TOPIC,
                "json_attributes_topic": ATTRIBUTES_TOPIC,
            }),
            retain=True,
        )
        self.client.publish(
            "homeassistant/image/parkeerwacht/foto/config",
            json.dumps({
                **common,
                "name": "Foto",
                "unique_id": "parkeerwacht_foto",
                "image_topic": PHOTO_TOPIC,
                "content_type": "image/jpeg",
            }),
            retain=True,
        )

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT-verbinding geweigerd: %s", reason_code)
            return
        client.subscribe("homeassistant/status")
        self._discovery()
        for topic, payload in list(self.last.items()):
            client.publish(topic, payload, retain=True)
        log.info("Verbonden met MQTT")

    def _on_message(self, client, userdata, message):
        if message.payload == b"online":
            self._discovery()

    def publish(self, topic: str, payload) -> None:
        self.last[topic] = payload
        self.client.publish(topic, payload, retain=True)


def fetch_image(camera: str, core_url: str) -> tuple:
    # camera_proxy kan een oud beeld geven (TP-Link: tot 5 minuten), de stream niet.
    # Niet via de Supervisor: die leest een antwoord eerst helemaal in, en deze stream stopt nooit.
    state = json.loads(supervisor_get(f"/core/api/states/{camera}"))
    token = urllib.parse.quote(state["attributes"]["access_token"])
    with urllib.request.urlopen(f"{core_url}/api/camera_proxy_stream/{camera}?token={token}", timeout=30) as stream:
        raw = read_stream_frame(stream)
    return Image.open(io.BytesIO(raw)).convert("RGB"), raw


def to_jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=82)
    return buffer.getvalue()


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    options = json.loads((DATA / "options.json").read_text())
    shared = Shared(DATA)
    threading.Thread(target=web.serve, args=(shared,), daemon=True).start()

    detector = Detector("yolox_tiny.onnx")
    tracker = OccupancyTracker(options["confirm_occupied"], options["confirm_free"])
    broker = Mqtt()
    failures = 0
    checks = 0
    attributes_sent = None
    log.info("Gestart, camera %s, elke %s s", options["camera_entity"], options["interval_seconds"])

    while True:
        started = time.monotonic()
        try:
            image, raw = fetch_image(options["camera_entity"], options["core_url"])
            with shared.lock:
                if raw == shared.raw_jpeg:
                    raise ValueError("camera geeft hetzelfde beeld als de vorige keer")
                zone = shared.zone
                if shared.zone_changed:
                    shared.zone_changed = False
                    tracker.reset()
            detect_started = time.monotonic()
            reading = detector.read(image, zone, options["min_confidence"], options["min_coverage"])
            checks += 1
            if checks <= 5 or checks % 60 == 0:
                log.info(
                    "Controle %s: camerabeeld %.1f s, herkenning %.2f s, dekking %.0f%%",
                    checks, detect_started - started, time.monotonic() - detect_started, reading.coverage * 100,
                )
            changed = tracker.update(reading.occupied)
            annotated = to_jpeg(annotate(image, zone, reading, options["min_coverage"]))
            with shared.lock:
                shared.occupied = tracker.state
                shared.coverage = reading.coverage
                shared.last_check = int(time.time())
                shared.error = None if reading.occupied is not None else "Beeld te donker om te beoordelen"
                shared.raw_jpeg = raw
                shared.annotated_jpeg = annotated
            failures = 0
            broker.publish(STATUS_TOPIC, "online")
            if changed:
                log.info("Status: %s (dekking %.0f%%)", "bezet" if tracker.state else "vrij", reading.coverage * 100)
                shared.add_history(tracker.state, annotated)
                broker.publish(STATE_TOPIC, "ON" if tracker.state else "OFF")
                broker.publish(PHOTO_TOPIC, annotated)
            # Alleen bij echte veranderingen, om de HA-database niet te vullen
            attributes = {"dekking_procent": round(reading.coverage * 20) * 5}
            if attributes != attributes_sent:
                broker.publish(ATTRIBUTES_TOPIC, json.dumps(attributes))
                attributes_sent = attributes
        except Exception as err:  # meetlus moet blijven draaien, ook als de camera wegvalt
            failures += 1
            log.warning("Controle mislukt (%s/%s): %s", failures, MAX_FAILURES, err)
            if failures >= MAX_FAILURES:
                # Liever "niet beschikbaar" dan onterecht "vrij"
                broker.publish(STATUS_TOPIC, "offline")
                with shared.lock:
                    shared.error = "Geen camerabeeld"
                    shared.occupied = None
                tracker.reset()
        shared.wake.wait(max(1.0, options["interval_seconds"] - (time.monotonic() - started)))
        shared.wake.clear()


if __name__ == "__main__":
    run()
