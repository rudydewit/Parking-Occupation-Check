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
    ChargeZone,
    Detector,
    KerbRuler,
    OccupancyTracker,
    largest_gap,
    read_stream_frame,
    stream_token,
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


class StreamTokenTests(unittest.TestCase):
    def test_returns_token_of_available_camera(self):
        self.assertEqual(stream_token({"state": "idle", "attributes": {"access_token": "abc"}}), "abc")

    def test_unavailable_camera_names_the_cause(self):
        state = {"state": "unavailable", "attributes": {"friendly_name": "Live view"}}
        with self.assertRaisesRegex(ValueError, "camera niet beschikbaar .*unavailable"):
            stream_token(state)


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


ZONE = ChargeZone(2.2, 12.1, 4.5)  # de standaardinstellingen


class RulerTests(unittest.TestCase):
    # Afgelezen voegen langs de stoeprand: tegelnummer en plek op de rand na lenscorrectie
    JOINTS = {0: -504, 3: -270, 6: -102, 9: 23, 12: 116, 15: 191, 18: 254}

    def setUp(self):
        self.ruler = KerbRuler(0.30)

    def test_joints_lie_one_tile_apart(self):
        for tile, along in self.JOINTS.items():
            self.assertAlmostEqual(float(self.ruler.metres(along)), (tile + 3.777) * 0.30, delta=0.03)

    def test_frame_bottom_is_zero(self):
        along = (self.ruler.undistort((57.0, 720.0)) - np.array([394.824, 362.381])) @ np.array([0.77772, -0.628611])
        self.assertAlmostEqual(float(self.ruler.metres(along)), 0.0, delta=0.02)

    def test_lens_correction_round_trip(self):
        for point in ((57.0, 720.0), (640.0, 360.0), (1000.0, 100.0)):
            np.testing.assert_allclose(self.ruler.distort(self.ruler.undistort(point)), point, atol=1e-6)

    def test_kerb_pixels_stay_on_the_kerb(self):
        start, end = self.ruler.kerb_pixels(0.0, 5.0, steps=2)
        self.assertAlmostEqual(start[0], 57, delta=3)
        self.assertAlmostEqual(start[1], 720, delta=3)
        self.assertLess(end[1], start[1])

    def test_car_against_the_kerb_gets_a_span(self):
        front, rear = self.ruler.span((249, 79, 587, 353))  # Twingo
        self.assertAlmostEqual(front, 3.1, delta=0.2)
        self.assertAlmostEqual(rear, 6.6, delta=0.3)

    def test_car_across_the_street_is_ignored(self):
        self.assertIsNone(self.ruler.span((82, 108, 348, 269)))

    def test_car_cut_off_by_the_frame_reaches_past_the_edge(self):
        front, rear = self.ruler.span((0, 381, 185, 687))
        self.assertEqual(front, -np.inf)
        self.assertGreater(rear, 0.5)

    def test_tile_size_scales_the_distances(self):
        small, large = KerbRuler(0.30).span((249, 79, 587, 353))[1], KerbRuler(0.33).span((249, 79, 587, 353))[1]
        self.assertAlmostEqual(large / small, 1.1, places=5)


class GapTests(unittest.TestCase):
    def test_empty_zone_is_all_free(self):
        self.assertEqual(largest_gap([], ZONE), (2.2, 12.1))

    def test_finds_the_largest_stretch(self):
        self.assertEqual(largest_gap([(3.0, 6.0), (7.0, 11.0)], ZONE), (11.0, 12.1))
        self.assertEqual(largest_gap([(8.0, 9.0)], ZONE), (2.2, 8.0))

    def test_cars_outside_the_zone_do_not_count(self):
        self.assertEqual(largest_gap([(0.0, 1.0), (13.0, 16.0)], ZONE), (2.2, 12.1))

    def test_car_over_the_zone_edge_takes_its_part(self):
        self.assertEqual(largest_gap([(-np.inf, 4.0)], ZONE), (4.0, 12.1))
        self.assertEqual(largest_gap([(9.0, 20.0)], ZONE), (2.2, 9.0))

    def test_overlapping_boxes_leave_no_gap(self):
        self.assertEqual(largest_gap([(3.0, 8.0), (5.0, 6.0)], ChargeZone(3.0, 8.0, 4.5)), (3.0, 3.0))

    def test_full_zone_has_no_free_length(self):
        gap = largest_gap([(0.0, 20.0)], ZONE)
        self.assertEqual(gap[1] - gap[0], 0.0)


MODEL = os.environ.get("PARKEERWACHT_MODEL")
SNAPSHOTS = os.environ.get("PARKEERWACHT_SNAPSHOTS", "").split(",")


@unittest.skipUnless(MODEL, "PARKEERWACHT_MODEL niet gezet")
class RealImageTests(unittest.TestCase):
    """Echte snapshots met twee auto's in de laadzone en een auto aan de overkant."""

    @classmethod
    def setUpClass(cls):
        cls.detector = Detector(MODEL)
        cls.ruler = KerbRuler(0.30)

    def readings(self, ruler=None):
        for path in SNAPSHOTS:
            yield path, self.detector.read(Image.open(path).convert("RGB"), ruler or self.ruler, ZONE, 0.35)

    def test_two_parked_cars_mean_occupied(self):
        for path, reading in self.readings():
            self.assertTrue(reading.occupied, path)
            self.assertLess(reading.free, 1.5, path)

    def test_car_across_the_street_does_not_count(self):
        for path, reading in self.readings():
            self.assertEqual(sum(1 for v in reading.vehicles if v.span and v.span[0] < ZONE.end), 2, path)

    def test_one_car_gone_means_free_at_either_tile_size(self):
        for size in (0.30, 0.32):
            for path, reading in self.readings(KerbRuler(size)):
                spans = sorted(v.span for v in reading.vehicles if v.span and v.span[0] < ZONE.end)
                for left in spans:
                    gap = largest_gap([left], ZONE)
                    self.assertGreaterEqual(gap[1] - gap[0], ZONE.min_free, f"{path} tegel {size}")

    def test_black_image_is_not_judged_as_free(self):
        black = Image.fromarray(np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertIsNone(self.detector.read(black, self.ruler, ZONE, 0.35).occupied)


if __name__ == "__main__":
    unittest.main()
