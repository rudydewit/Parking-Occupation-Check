"""Draaien met: python -m unittest discover -s tests (met app/ op het pad)."""
import io
import os
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from PIL import Image  # noqa: E402

from parking import (  # noqa: E402
    DEFAULT_ZONE,
    Detector,
    OccupancyTracker,
    box_coverage,
    read_stream_frame,
    validate_zone,
    zone_mask,
)


def part(frame: bytes) -> bytes:
    return b"--ffmpeg\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"


FIRST, SECOND = b"\xff\xd8first\xff\xd9", b"\xff\xd8second\xff\xd9"


class StreamFrameTests(unittest.TestCase):
    def test_skips_first_frame(self):
        self.assertEqual(read_stream_frame(io.BytesIO(part(FIRST) + part(SECOND))), SECOND)

    def test_can_return_first_frame(self):
        self.assertEqual(read_stream_frame(io.BytesIO(part(FIRST)), skip=0), FIRST)

    def test_frame_split_over_reads(self):
        class OneByte(io.BytesIO):
            def read1(self, size=-1):
                return super().read1(1)

        self.assertEqual(read_stream_frame(OneByte(part(FIRST) + part(SECOND))), SECOND)

    def test_incomplete_stream_fails(self):
        with self.assertRaises(ValueError):
            read_stream_frame(io.BytesIO(part(FIRST) + b"\xff\xd8half"))

    def test_stream_without_frame_end_stops_at_limit(self):
        with self.assertRaises(ValueError):
            read_stream_frame(io.BytesIO(b"\xff\xd8" + b"x" * 500), max_bytes=100)

    def test_endless_http_stream_returns_and_closes(self):
        closed = threading.Event()

        class Endless(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=ffmpeg")
                self.end_headers()
                try:
                    while True:
                        self.wfile.write(part(SECOND))
                        self.wfile.flush()
                except OSError:
                    closed.set()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Endless)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as stream:
            self.assertEqual(read_stream_frame(stream), SECOND)
        self.assertTrue(closed.wait(5), "de stream moet na het lezen sluiten")


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = OccupancyTracker(confirm_occupied=2, confirm_free=3)

    def test_first_reading_sets_state_immediately(self):
        self.assertTrue(self.tracker.update(True))
        self.assertTrue(self.tracker.state)

    def test_passing_car_changes_nothing(self):
        self.tracker.update(False)
        self.assertFalse(self.tracker.update(True))
        self.assertFalse(self.tracker.update(False))
        self.assertFalse(self.tracker.update(True))
        self.assertFalse(self.tracker.state)

    def test_needs_confirmations_to_become_occupied(self):
        self.tracker.update(False)
        self.tracker.update(True)
        self.assertTrue(self.tracker.update(True))
        self.assertTrue(self.tracker.state)

    def test_needs_confirmations_to_become_free(self):
        self.tracker.update(True)
        self.assertFalse(self.tracker.update(False))
        self.assertFalse(self.tracker.update(False))
        self.assertTrue(self.tracker.update(False))
        self.assertFalse(self.tracker.state)

    def test_unjudgeable_image_keeps_state(self):
        self.tracker.update(True)
        self.assertFalse(self.tracker.update(None))
        self.assertTrue(self.tracker.state)

    def test_reset_forgets_state(self):
        self.tracker.update(True)
        self.tracker.reset()
        self.assertIsNone(self.tracker.state)


class ZoneTests(unittest.TestCase):
    def test_accepts_default_zone(self):
        self.assertEqual(validate_zone(DEFAULT_ZONE), DEFAULT_ZONE)

    def test_rejects_bad_zones(self):
        for bad in ("x", [], [[0, 0], [1, 1]], [[0, 0], [1, 1], [2, 0]], [[0, 0], [1, 1], ["a", 0]],
                    [[0, 0], [1, 1], [True, 0]], [[0, 0]] * 13):
            with self.assertRaises(ValueError):
                validate_zone(bad)

    def test_coverage(self):
        mask = zone_mask([[0, 0], [0.5, 0], [0.5, 1], [0, 1]], 100, 100)
        self.assertAlmostEqual(box_coverage((0, 0, 100, 100), mask), 1.0, places=1)
        self.assertAlmostEqual(box_coverage((0, 0, 25, 100), mask), 0.5, places=1)
        self.assertEqual(box_coverage((60, 0, 100, 100), mask), 0.0)
        self.assertEqual(box_coverage((-50, -50, -10, -10), mask), 0.0)


MODEL = os.environ.get("PARKEERWACHT_MODEL")
SNAPSHOTS = os.environ.get("PARKEERWACHT_SNAPSHOTS", "").split(",")


@unittest.skipUnless(MODEL, "PARKEERWACHT_MODEL niet gezet")
class RealImageTests(unittest.TestCase):
    """Echte snapshots waarop de Twingo in vak B staat en vak A leeg is."""

    ZONE_A = [[0.0156, 0.9722], [0.2773, 0.5486], [0.0938, 0.4444], [0, 0.5486], [0, 0.9722]]

    @classmethod
    def setUpClass(cls):
        cls.detector = Detector(MODEL)

    def test_bay_b_is_occupied(self):
        for path in SNAPSHOTS:
            reading = self.detector.read(Image.open(path).convert("RGB"), DEFAULT_ZONE, 0.35, 0.5)
            self.assertTrue(reading.occupied, path)
            self.assertGreater(reading.coverage, 0.9)
            neighbours = sorted(v.coverage for v in reading.vehicles)[:-1]
            self.assertLess(max(neighbours), 0.5, "buurauto mag het vak niet bezet maken")

    def test_empty_bay_a_is_free(self):
        for path in SNAPSHOTS:
            reading = self.detector.read(Image.open(path).convert("RGB"), self.ZONE_A, 0.35, 0.5)
            self.assertFalse(reading.occupied, path)

    def test_black_image_is_not_judged_as_free(self):
        black = Image.fromarray(np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertIsNone(self.detector.read(black, DEFAULT_ZONE, 0.35, 0.5).occupied)


if __name__ == "__main__":
    unittest.main()
