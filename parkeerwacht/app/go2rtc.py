"""Eigen go2rtc in de add-on: één vaste verbinding met de camera.

Tot en met 0.4.0 kwam het beeld via Home Assistant. Home Assistant start voor elk
beeld van de TP-Link-camera een ffmpeg-proces, en dat sluit niet netjes af
("Timeout while waiting of FFmpeg", één keer per controle). De camera houdt die
verbindingen open en zat na ongeveer 28 uur weer vol (17 november 2026).

go2rtc houdt met "preload" één RTSP-verbinding met de camera open en bouwt die
zelf opnieuw op als hij wegvalt. Een controle vraagt één JPEG bij go2rtc op; dat
kost de camera geen nieuwe sessie. go2rtc pakt de video alleen uit op het moment
dat er een beeld wordt gevraagd, dus de vaste verbinding kost weinig rekenkracht.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional

log = logging.getLogger("parkeerwacht")

STREAM = "camera"
API = "127.0.0.1:1984"
BINARY = "/usr/local/bin/go2rtc"


def mask(text: str) -> str:
    """Haalt gebruikersnaam en wachtwoord uit adressen, voor het logboek."""
    return re.sub(r"(\w+://)[^/@\s]+@", r"\1****@", text)


def config(rtsp_url: str, api: str = API) -> dict:
    """go2rtc-instellingen: alleen de API op localhost, geen andere poorten naar buiten."""
    return {
        "api": {"listen": api},
        "rtsp": {"listen": ""},
        "webrtc": {"listen": ""},
        "srtp": {"listen": ""},
        "log": {"level": "info"},
        "streams": {STREAM: [rtsp_url]},
        "preload": {STREAM: "video"},
    }


def write_config(path: Path, rtsp_url: str, api: str = API) -> None:
    # YAML is een superset van JSON, dus go2rtc leest dit gewoon
    path.write_text(json.dumps(config(rtsp_url, api), indent=2))
    path.chmod(0o600)


class Go2rtc:
    """Start go2rtc, start het opnieuw als het stopt, en vraagt beelden op."""

    def __init__(
        self,
        rtsp_url: str,
        config_path: Path = Path("/tmp/go2rtc.yaml"),
        binary: str = BINARY,
        api: str = API,
        restart_delay: float = 10.0,
        command: Optional[Callable[[Path], List[str]]] = None,
    ):
        self.rtsp_url = rtsp_url
        self.config_path = config_path
        self.api = api
        self.restart_delay = restart_delay
        self._command = command or (lambda path: [binary, "-config", str(path)])
        self._process: Optional[subprocess.Popen] = None
        self._stopping = False

    def start(self) -> None:
        write_config(self.config_path, self.rtsp_url, self.api)
        threading.Thread(target=self._supervise, daemon=True).start()

    def stop(self) -> None:
        self._stopping = True
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def _supervise(self) -> None:
        while not self._stopping:
            self._process = subprocess.Popen(
                self._command(self.config_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in self._process.stdout:
                line = line.strip()
                if line:
                    log.info("go2rtc: %s", mask(line))
            code = self._process.wait()
            if self._stopping:
                return
            log.warning("go2rtc stopte (code %s), opnieuw starten over %.0f s", code, self.restart_delay)
            time.sleep(self.restart_delay)

    def _get(self, path: str, timeout: float) -> bytes:
        with urllib.request.urlopen(f"http://{self.api}{path}", timeout=timeout) as response:
            return response.read()

    def ensure_preload(self) -> None:
        """Zet de vaste verbinding (terug) aan als die ontbreekt.

        go2rtc zet de preload bij het starten maar één keer; is de camera dan niet
        bereikbaar, dan blijft hij weg. Een eenmaal gestarte verbinding bouwt go2rtc
        na een storing zelf opnieuw op.
        """
        preloads = json.loads(self._get("/api/preload", timeout=5) or b"{}")
        if STREAM in preloads:
            return
        query = urllib.parse.urlencode({"src": STREAM, "video": ""})
        request = urllib.request.Request(f"http://{self.api}/api/preload?{query}", method="PUT")
        try:
            with urllib.request.urlopen(request, timeout=30):
                pass
        except urllib.error.HTTPError as err:
            raise ConnectionError(f"camera niet bereikbaar via go2rtc: {mask(err.read().decode(errors='replace').strip())}") from None
        log.info("go2rtc: vaste verbinding met de camera gestart")

    def frame(self, width: int = 1280, timeout: float = 30) -> bytes:
        """Eén vers JPEG-beeld uit de vaste verbinding."""
        self.ensure_preload()
        query = urllib.parse.urlencode({"src": STREAM, "width": width})
        try:
            data = self._get(f"/api/frame.jpeg?{query}", timeout=timeout)
        except urllib.error.HTTPError as err:
            raise ConnectionError(f"go2rtc gaf geen beeld: {mask(err.read().decode(errors='replace').strip())}") from None
        if not data.startswith(b"\xff\xd8"):
            raise ValueError("go2rtc gaf geen JPEG terug")
        return data
