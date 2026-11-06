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
from parking import ChargeZone, Detector, KerbRuler, OccupancyTracker, annotate, stream_token
from stream import StreamReader

DATA = Path(os.environ.get("PARKEERWACHT_DATA", "/data"))
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HISTORY_LIMIT = 50
MAX_FAILURES = 3
MAX_FRAME_AGE = 30  # seconden; ouder beeld telt als geen beeld
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

    def __init__(self, data_dir: Path, min_free: float):
        self.lock = threading.Lock()
        self.history_dir = data_dir / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.min_free = min_free
        self.occupied = None
        self.free = 0.0
        self.last_check = 0
        self.error = None
        self.raw_jpeg = None
        self.annotated_jpeg = None

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
                "free": self.free,
                "min_free": self.min_free,
                "last_check": self.last_check,
                "error": self.error,
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


def open_camera_stream(camera: str, core_url: str):
    """Opent de MJPEG-stream van de camera; de StreamReader houdt hem open.

    camera_proxy kan een oud beeld geven (TP-Link: tot 5 minuten), de stream niet.
    Niet via de Supervisor: die leest een antwoord eerst helemaal in, en deze stream stopt nooit.
    De time-out geldt per leesactie: valt de stream 30 s stil, dan volgt een fout.
    """
    state = json.loads(supervisor_get(f"/core/api/states/{camera}"))
    token = urllib.parse.quote(stream_token(state))
    return urllib.request.urlopen(f"{core_url}/api/camera_proxy_stream/{camera}?token={token}", timeout=30)


def to_jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=82)
    return buffer.getvalue()


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    options = json.loads((DATA / "options.json").read_text())
    if options["zone_start_m"] >= options["zone_end_m"]:
        raise ValueError("zone_start_m moet kleiner zijn dan zone_end_m")
    zone = ChargeZone(options["zone_start_m"], options["zone_end_m"], options["min_free_m"])
    ruler = KerbRuler(options["tile_size_m"])
    shared = Shared(DATA, zone.min_free)
    threading.Thread(target=web.serve, args=(shared,), daemon=True).start()

    detector = Detector("yolox_tiny.onnx")
    tracker = OccupancyTracker(options["confirm_occupied"], options["confirm_free"])
    broker = Mqtt()
    reader = StreamReader(lambda: open_camera_stream(options["camera_entity"], options["core_url"])).start()
    used_frame_time = 0.0
    for _ in range(30):  # eerste beeld afwachten, anders begint de eerste controle met een fout
        if reader.latest()[0] is not None:
            break
        time.sleep(1)
    failures = 0
    last_error = None
    checks = 0
    attributes_sent = None
    log.info("Gestart, camera %s, elke %s s", options["camera_entity"], options["interval_seconds"])

    while True:
        started = time.monotonic()
        try:
            raw, frame_time = reader.latest()
            age = time.monotonic() - frame_time
            if raw is None or frame_time <= used_frame_time or age > MAX_FRAME_AGE:
                raise ValueError(f"geen nieuw camerabeeld ({reader.error or 'stream levert niets'})")
            used_frame_time = frame_time
            with shared.lock:
                if raw == shared.raw_jpeg:
                    reader.reconnect()
                    raise ValueError("camera geeft hetzelfde beeld als de vorige keer")
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            detect_started = time.monotonic()
            reading = detector.read(image, ruler, zone, options["min_confidence"])
            checks += 1
            if checks <= 5 or checks % 60 == 0:
                log.info(
                    "Controle %s: beeld %.1f s oud, herkenning %.2f s, vrij %.1f m",
                    checks, age, time.monotonic() - detect_started, reading.free,
                )
            changed = tracker.update(reading.occupied)
            annotated = to_jpeg(annotate(image, ruler, zone, reading))
            with shared.lock:
                shared.occupied = tracker.state
                shared.free = reading.free
                shared.last_check = int(time.time())
                shared.error = None if reading.occupied is not None else "Beeld te donker om te beoordelen"
                shared.raw_jpeg = raw
                shared.annotated_jpeg = annotated
            if failures:
                log.info("Controle weer gelukt na %s mislukte pogingen", failures)
            failures = 0
            last_error = None
            broker.publish(STATUS_TOPIC, "online")
            if changed:
                log.info("Status: %s (vrij %.1f m)", "bezet" if tracker.state else "vrij", reading.free)
                shared.add_history(tracker.state, annotated)
                broker.publish(STATE_TOPIC, "ON" if tracker.state else "OFF")
                broker.publish(PHOTO_TOPIC, annotated)
            # Alleen bij echte veranderingen, om de HA-database niet te vullen
            attributes = {"vrije_ruimte_meter": round(reading.free * 2) / 2}
            if reading.occupied is not None and attributes != attributes_sent:
                broker.publish(ATTRIBUTES_TOPIC, json.dumps(attributes))
                attributes_sent = attributes
        except Exception as err:  # meetlus moet blijven draaien, ook als de camera wegvalt
            failures += 1
            if str(err) != last_error:  # een lange storing niet elke minuut opnieuw melden
                log.warning("Controle mislukt: %s", err)
                last_error = str(err)
            if failures >= MAX_FAILURES:
                # Liever "niet beschikbaar" dan onterecht "vrij"
                broker.publish(STATUS_TOPIC, "offline")
                with shared.lock:
                    shared.error = "Geen camerabeeld"
                    shared.occupied = None
                tracker.reset()
        time.sleep(max(1.0, options["interval_seconds"] - (time.monotonic() - started)))


if __name__ == "__main__":
    run()
