"""Eén vaste verbinding met de camerastream, met oplopende wachttijd bij een storing.

Elke controle een nieuwe stream openen kostte bij de Tapo-camera een streamplek per keer.
Bleven die hangen, dan zat de camera vol ("limiet bereikt voor het afspelen van video's")
en kwam hij pas na een herstart terug. Daarom houdt deze lezer één verbinding open en
bewaart hij steeds het nieuwste beeld. Valt de stream weg, dan wacht hij steeds langer
voor hij het opnieuw probeert, zodat de camera tijd krijgt om oude sessies op te ruimen.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional, Tuple

from parking import iter_stream_frames

log = logging.getLogger("parkeerwacht")


class Backoff:
    """Wachttijden die bij elke mislukte poging verdubbelen, tot een maximum."""

    def __init__(self, first: float = 30.0, maximum: float = 1800.0):
        self.first = first
        self.maximum = maximum
        self.attempts = 0

    def next(self) -> float:
        wait = min(self.first * 2 ** self.attempts, self.maximum)
        self.attempts += 1
        return wait

    def reset(self) -> None:
        self.attempts = 0


def duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    return f"{seconds / 60:.0f} min"


class StreamReader:
    """Leest in een eigen thread de stream en bewaart het nieuwste beeld.

    open_stream geeft een bestand-achtig object met read1 (zoals urlopen) en moet
    een time-out hebben, zodat een stilgevallen stream een fout geeft.
    """

    def __init__(
        self,
        open_stream: Callable[[], object],
        backoff: Optional[Backoff] = None,
        clock: Callable[[], float] = time.monotonic,
        wait: Optional[Callable[[float], bool]] = None,
    ):
        self._open = open_stream
        self.backoff = backoff or Backoff()
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reconnect = threading.Event()
        self._wait = wait or self._stop.wait
        self._frame: Optional[bytes] = None
        self._frame_time = 0.0
        self.error: Optional[str] = None
        self.retry_at: Optional[float] = None
        self.connects = 0
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "StreamReader":
        self._thread = threading.Thread(target=self.run, name="camerastream", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def reconnect(self) -> None:
        """Verbinding opnieuw opbouwen, bijvoorbeeld als het beeld bevroren lijkt."""
        self._reconnect.set()

    def latest(self) -> Tuple[Optional[bytes], float]:
        """Nieuwste beeld en het moment (clock) waarop het binnenkwam."""
        with self._lock:
            return self._frame, self._frame_time

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._read_once()
                raise ValueError("stream gesloten")
            except Exception as err:  # draad moet blijven lopen
                if self._stop.is_set():
                    return
                wait = self.backoff.next()
                self.error = str(err)
                self.retry_at = self._clock() + wait
                log.warning(
                    "Camerastream: %s. Poging %s mislukt, volgende over %s",
                    err, self.backoff.attempts, duration(wait),
                )
                if self._wait(wait):
                    return

    def _read_once(self) -> None:
        self._reconnect.clear()
        stream = self._open()
        try:
            frames = iter_stream_frames(stream)
            next(frames)  # het eerste beeld kan nog onvolledig of oud zijn
            for frame in frames:
                with self._lock:
                    self._frame = frame
                    self._frame_time = self._clock()
                if self.backoff.attempts or self.connects == 0:
                    if self.backoff.attempts:
                        log.info("Camerastream weer verbonden na %s mislukte pogingen", self.backoff.attempts)
                    else:
                        log.info("Camerastream verbonden")
                    self.backoff.reset()
                    self.error = None
                    self.retry_at = None
                    self.connects += 1
                if self._stop.is_set():
                    return
                if self._reconnect.is_set():
                    raise ValueError("beeld veranderde niet meer, verbinding opnieuw opgebouwd")
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
