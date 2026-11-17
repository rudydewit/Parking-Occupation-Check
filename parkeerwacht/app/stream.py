"""Per controle één kort camerabeeld, met oplopende wachttijd bij een storing.

Geschiedenis:
- 0.2.x opende elke controle een stream, zonder wachttijd bij een storing. Bij de
  Tapo-camera bleven die sessies soms hangen, tot de camera vol zat ("limiet
  bereikt voor het afspelen van video's", 6 november 2026).
- 0.3.0 hield daarom één stream open. Home Assistant zet die stream dan continu om
  naar MJPEG, en daarbij liep het geheugen van HA Core met zo'n 1 % per uur op,
  tot de Pi vastliep (15 november 2026).
- 0.4.0 opent weer per controle een stream, leest één vers beeld en sluit de
  verbinding direct. Valt het beeld weg, dan wacht de grabber steeds langer voor
  hij het opnieuw probeert, zodat de camera tijd krijgt om oude sessies op te
  ruimen en niet vol raakt.
- 0.5.0 haalt het beeld standaard bij een eigen go2rtc (zie go2rtc.py), die één
  vaste verbinding met de camera openhoudt. De FrameGrabber krijgt dan een
  fetch-functie in plaats van een stream; de wachttijd werkt hetzelfde.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from parking import read_stream_frame

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


class Waiting(Exception):
    """Er is een storing en de wachttijd tot de volgende poging loopt nog."""


class FrameGrabber:
    """Opent per aanroep een stream, geeft één vers beeld en sluit de stream weer.

    open_stream geeft een bestand-achtig object met read1 (zoals urlopen) en moet
    een time-out hebben, zodat een stilgevallen stream een fout geeft.
    Of geef fetch: een functie die zelf één JPEG-beeld teruggeeft (go2rtc).
    """

    def __init__(
        self,
        open_stream: Optional[Callable[[], object]] = None,
        backoff: Optional[Backoff] = None,
        clock: Callable[[], float] = time.monotonic,
        fetch: Optional[Callable[[], bytes]] = None,
    ):
        if (open_stream is None) == (fetch is None):
            raise ValueError("geef open_stream of fetch")
        self._open = open_stream
        self._fetch = fetch or self._from_stream
        self.backoff = backoff or Backoff()
        self._clock = clock
        self.error: Optional[str] = None
        self.retry_at: Optional[float] = None
        self.grabs = 0

    def grab(self) -> bytes:
        """Eén vers JPEG-beeld. Geeft Waiting zolang de wachttijd na een storing loopt."""
        now = self._clock()
        if self.retry_at is not None and now < self.retry_at:
            raise Waiting(f"{self.error}; volgende poging over {duration(self.retry_at - now)}")
        try:
            frame = self._fetch()
        except Exception as err:
            wait = self.backoff.next()
            self.error = str(err)
            self.retry_at = self._clock() + wait
            log.warning(
                "Camerabeeld: %s. Poging %s mislukt, volgende over %s",
                err, self.backoff.attempts, duration(wait),
            )
            raise
        if self.backoff.attempts:
            log.info("Camerabeeld weer gelukt na %s mislukte pogingen", self.backoff.attempts)
        self.backoff.reset()
        self.error = None
        self.retry_at = None
        self.grabs += 1
        return frame

    def _from_stream(self) -> bytes:
        stream = self._open()
        try:
            return read_stream_frame(stream, skip=1)  # het eerste beeld kan onvolledig of oud zijn
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
