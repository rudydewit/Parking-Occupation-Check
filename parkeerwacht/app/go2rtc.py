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
from collections import deque
from typing import Callable, Deque, List, Optional

from stream import Backoff, duration

log = logging.getLogger("parkeerwacht")

STREAM = "camera"
API = "127.0.0.1:1984"
BINARY = "/usr/local/bin/go2rtc"
# Verbreekt de camera de verbinding steeds direct, dan probeert go2rtc het binnen
# een fractie van een seconde opnieuw, zonder einde (17 november 2026: zo'n 20 keer
# per seconde). Zoveel fouten binnen zo'n korte tijd = de vaste verbinding stoppen
# en pas na een oplopende wachttijd opnieuw.
STORM_ERRORS = 5
STORM_SECONDS = 10.0


def mask(text: str) -> str:
    """Haalt gebruikersnaam, wachtwoord en inloggegevens uit regels, voor het logboek."""
    text = re.sub(r"(\w+://)[^/@\s]+@", r"\1****@", text)
    return re.sub(r"(Authorization:\s*\w+)\s+[^\r\n]*", r"\1 ****", text, flags=re.I)


def source(rtsp_url: str, via: str = "rtsp") -> str:
    """Bron voor go2rtc.

    rtsp: go2rtc verbindt zelf, zonder tweerichtingsaudio (daar hebben we niets aan,
    en sommige camera's slaan erop af).
    ffmpeg: go2rtc laat één doorlopend ffmpeg-proces verbinden, zoals Home Assistant
    dat doet; de video wordt alleen doorgegeven, niet opnieuw gecodeerd.
    """
    if via == "ffmpeg":
        return f"ffmpeg:{rtsp_url}#video=copy#audio=copy"
    return rtsp_url if "#" in rtsp_url else rtsp_url + "#backchannel=0"


def config(rtsp_url: str, api: str = API, trace: bool = False, via: str = "rtsp") -> dict:
    """go2rtc-instellingen: alleen de API op localhost, geen andere poorten naar buiten."""
    return {
        "api": {"listen": api},
        # ffmpeg levert zijn stream via RTSP aan go2rtc af, dus die luistert wel, maar
        # alleen binnen de add-on
        "rtsp": {"listen": "127.0.0.1:8554"},
        "webrtc": {"listen": ""},
        "srtp": {"listen": ""},
        # trace toont het RTSP-gesprek met de camera, om een weigerende camera te onderzoeken
        "log": {"level": "info", "rtsp": "trace", "streams": "trace"} if trace else {"level": "info"},
        "streams": {STREAM: [source(rtsp_url, via)]},
        # video én audio: de Tapo C325WB sluit de verbinding direct na PLAY als
        # alleen het videospoor is opgezet (17 november 2026)
        "preload": {STREAM: "video&audio"},
    }


def write_config(path: Path, rtsp_url: str, api: str = API, trace: bool = False, via: str = "rtsp") -> None:
    # YAML is een superset van JSON, dus go2rtc leest dit gewoon
    path.write_text(json.dumps(config(rtsp_url, api, trace, via), indent=2))
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
        trace: bool = False,
        via: str = "rtsp",
    ):
        self.trace = trace
        self.via = via
        self.rtsp_url = rtsp_url
        self.config_path = config_path
        self.api = api
        self.restart_delay = restart_delay
        self._command = command or (lambda path: [binary, "-config", str(path)])
        self._process: Optional[subprocess.Popen] = None
        self._stopping = False
        self._clock = time.monotonic
        self._errors: Deque[float] = deque(maxlen=STORM_ERRORS)
        self._lock = threading.Lock()
        self.cooldown = Backoff(120, 1800)
        self.paused_until: Optional[float] = None
        self.pause_reason = ""
        self._quiet_until = 0.0

    def start(self, wait: float = 15.0) -> None:
        """Start go2rtc en wacht tot de API antwoordt, zodat de eerste controle niet te vroeg is."""
        write_config(self.config_path, self.rtsp_url, self.api, self.trace, self.via)
        threading.Thread(target=self._supervise, daemon=True).start()
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                self._get("/api", timeout=2)
                return
            except OSError:
                time.sleep(0.2)
        log.warning("go2rtc antwoordt na %.0f s nog niet", wait)

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
                    self._on_line(line)
            code = self._process.wait()
            if self._stopping:
                return
            log.warning("go2rtc stopte (code %s), opnieuw starten over %.0f s", code, self.restart_delay)
            time.sleep(self.restart_delay)

    def _on_line(self, line: str) -> None:
        now = self._clock()
        if "producer.go" in line and "error=" in line:
            if self.paused_until is not None and now < self.paused_until:
                return  # naloop van de storm die al is afgehandeld
            with self._lock:
                self._errors.append(now)
                storm = len(self._errors) == STORM_ERRORS and now - self._errors[0] <= STORM_SECONDS
                if storm:
                    self._errors.clear()
            if storm:
                self._stop_preload(now, line)
                return
            if now < self._quiet_until:
                return  # na een storm niet elke herhaling loggen
        log.info("go2rtc: %s", mask(line))

    def _stop_preload(self, now: float, line: str) -> None:
        wait = self.cooldown.next()
        reason = re.search(r"error=(\"[^\"]*\"|\S+)", line)
        self.pause_reason = f"camera verbreekt de verbinding steeds ({reason.group(1).strip(chr(34)) if reason else 'fout'})"
        self.paused_until = now + wait
        self._quiet_until = now + 5
        log.warning("%s; vaste verbinding gestopt, nieuwe poging over %s", self.pause_reason, duration(wait))
        try:
            query = urllib.parse.urlencode({"src": STREAM})
            request = urllib.request.Request(f"http://{self.api}/api/preload?{query}", method="DELETE")
            with urllib.request.urlopen(request, timeout=5):
                pass
        except OSError as err:
            log.warning("go2rtc: vaste verbinding stoppen mislukt (%s), go2rtc wordt herstart", err)
            if self._process and self._process.poll() is None:
                self._process.kill()

    def _get(self, path: str, timeout: float) -> bytes:
        with urllib.request.urlopen(f"http://{self.api}{path}", timeout=timeout) as response:
            return response.read()

    def ensure_preload(self) -> None:
        """Zet de vaste verbinding (terug) aan als die ontbreekt.

        go2rtc zet de preload bij het starten maar één keer; is de camera dan niet
        bereikbaar, dan blijft hij weg. Een eenmaal gestarte verbinding bouwt go2rtc
        na een storing zelf opnieuw op.
        """
        if self.paused_until is not None:
            left = self.paused_until - self._clock()
            if left > 0:
                raise ConnectionError(f"{self.pause_reason}; nieuwe poging over {duration(left)}")
        preloads = json.loads(self._get("/api/preload", timeout=5) or b"{}")
        if STREAM in preloads:
            return
        query = urllib.parse.urlencode({"src": STREAM, "video": "", "audio": ""})
        request = urllib.request.Request(f"http://{self.api}/api/preload?{query}", method="PUT")
        try:
            with urllib.request.urlopen(request, timeout=30):
                pass
        except urllib.error.HTTPError as err:
            raise ConnectionError(f"camera niet bereikbaar via go2rtc: {mask(err.read().decode(errors='replace').strip())}") from None
        log.info("go2rtc: vaste verbinding met de camera gestart")
        self.paused_until = None

    def frame(self, width: int = 1280, timeout: float = 15) -> bytes:
        """Eén vers JPEG-beeld uit de vaste verbinding."""
        self.ensure_preload()
        query = urllib.parse.urlencode({"src": STREAM, "width": width})
        try:
            data = self._get(f"/api/frame.jpeg?{query}", timeout=timeout)
        except urllib.error.HTTPError as err:
            raise ConnectionError(f"go2rtc gaf geen beeld: {mask(err.read().decode(errors='replace').strip())}") from None
        if not data.startswith(b"\xff\xd8"):
            raise ValueError("go2rtc gaf geen JPEG terug")
        self.cooldown.reset()  # pas een echt beeld bewijst dat de verbinding goed is
        return data
