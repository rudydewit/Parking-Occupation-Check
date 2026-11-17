"""Draaien met: python -m unittest discover -s tests (met app/ op het pad)."""
import json
import logging
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from go2rtc import STORM_ERRORS, STREAM, Go2rtc, config, mask, write_config  # noqa: E402
from stream import Backoff, FrameGrabber  # noqa: E402

JPEG = b"\xff\xd8beeld\xff\xd9"
URL = "rtsp://parkeer:geheim@192.168.2.50:554/stream1"


class FakeApi(BaseHTTPRequestHandler):
    """Doet zich voor als de go2rtc-API, met een camera die aan of uit kan."""

    camera_up = True
    preloads: dict = {}
    requests: list = []

    def log_message(self, *args):
        pass

    def _reply(self, code, body=b"", kind="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        FakeApi.requests.append(("GET", url.path, query))
        if url.path == "/api/preload":
            self._reply(200, json.dumps(FakeApi.preloads).encode(), "application/json")
        elif url.path == "/api/frame.jpeg":
            if FakeApi.camera_up and STREAM in FakeApi.preloads:
                self._reply(200, JPEG, "image/jpeg")
            else:
                self._reply(500, f"dial {URL}: i/o timeout".encode())
        else:
            self._reply(404)

    def do_PUT(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        FakeApi.requests.append(("PUT", url.path, query))
        if not FakeApi.camera_up:
            self._reply(500, f"streams: dial {URL}: connection refused".encode())
            return
        FakeApi.preloads[query["src"][0]] = {"query": "video"}
        self._reply(200)


    def do_DELETE(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        FakeApi.requests.append(("DELETE", url.path, query))
        FakeApi.preloads.pop(query["src"][0], None)
        self._reply(200)


EOF_LINE = f"18:17:46.686 WRN github.com/AlexxIT/go2rtc/internal/streams/producer.go:170 > error=EOF url={URL}"


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.api = f"127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeApi.camera_up = True
        FakeApi.preloads = {}
        FakeApi.requests = []
        self.camera = Go2rtc(URL, api=self.api)

    def test_starts_the_fixed_connection_once(self):
        self.assertEqual(self.camera.frame(), JPEG)
        self.assertEqual(self.camera.frame(), JPEG)
        puts = [r for r in FakeApi.requests if r[0] == "PUT"]
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][2], {"src": [STREAM], "video": [""]})

    def test_asks_for_the_frame_in_1280_wide(self):
        self.camera.frame()
        frame = [r for r in FakeApi.requests if r[1] == "/api/frame.jpeg"][0]
        self.assertEqual(frame[2], {"src": [STREAM], "width": ["1280"]})

    def test_unreachable_camera_gives_a_clear_error_without_password(self):
        FakeApi.camera_up = False
        with self.assertRaises(ConnectionError) as caught:
            self.camera.frame()
        self.assertIn("niet bereikbaar", str(caught.exception))
        self.assertNotIn("geheim", str(caught.exception))

    def test_preload_is_restored_when_camera_returns(self):
        FakeApi.camera_up = False
        with self.assertRaises(ConnectionError):
            self.camera.frame()
        FakeApi.camera_up = True
        self.assertEqual(self.camera.frame(), JPEG)

    def test_frame_error_from_go2rtc_hides_password(self):
        FakeApi.preloads = {STREAM: {}}
        FakeApi.camera_up = False  # preload staat, maar go2rtc krijgt geen beeld
        with self.assertRaises(ConnectionError) as caught:
            self.camera.frame()
        self.assertIn("geen beeld", str(caught.exception))
        self.assertNotIn("geheim", str(caught.exception))

    def test_works_with_the_frame_grabber_backoff(self):
        now = [100.0]
        FakeApi.camera_up = False
        grabber = FrameGrabber(fetch=self.camera.frame, backoff=Backoff(30, 120), clock=lambda: now[0])
        with self.assertRaises(ConnectionError):
            grabber.grab()
        self.assertEqual(grabber.retry_at - now[0], 30)
        now[0] = grabber.retry_at
        FakeApi.camera_up = True
        self.assertEqual(grabber.grab(), JPEG)
        self.assertIsNone(grabber.retry_at)


    def test_reconnect_storm_stops_the_fixed_connection(self):
        now = [100.0]
        self.camera._clock = lambda: now[0]
        self.assertEqual(self.camera.frame(), JPEG)
        with self.assertLogs("parkeerwacht", logging.INFO) as logs:
            for i in range(STORM_ERRORS * 3):
                now[0] += 0.05
                self.camera._on_line(EOF_LINE)
        self.assertNotIn(STREAM, FakeApi.preloads)
        text = "\n".join(logs.output)
        self.assertIn("verbreekt de verbinding steeds (EOF)", text)
        self.assertIn("over 2 min", text)
        self.assertNotIn("geheim", text)
        # tijdens de wachttijd spreekt de add-on de camera niet aan
        puts = len([r for r in FakeApi.requests if r[0] == "PUT"])
        with self.assertRaises(ConnectionError) as caught:
            self.camera.frame()
        self.assertIn("nieuwe poging over", str(caught.exception))
        self.assertEqual(len([r for r in FakeApi.requests if r[0] == "PUT"]), puts)
        # daarna wel weer, en een goed beeld zet de wachttijd terug
        now[0] += 121
        self.assertEqual(self.camera.frame(), JPEG)
        self.assertEqual(self.camera.cooldown.attempts, 0)

    def test_storms_wait_longer_each_time(self):
        now = [100.0]
        self.camera._clock = lambda: now[0]
        waits = []
        for _ in range(3):
            with self.assertLogs("parkeerwacht", logging.WARNING):
                for _ in range(STORM_ERRORS):
                    now[0] += 0.05
                    self.camera._on_line(EOF_LINE)
            waits.append(round(self.camera.paused_until - now[0]))
            now[0] = self.camera.paused_until + 1
        self.assertEqual(waits, [120, 240, 480])

    def test_occasional_drops_are_no_storm(self):
        now = [100.0]
        self.camera._clock = lambda: now[0]
        self.camera.frame()
        with self.assertLogs("parkeerwacht", logging.INFO):
            for _ in range(STORM_ERRORS * 2):
                now[0] += 5
                self.camera._on_line(EOF_LINE)
        self.assertIn(STREAM, FakeApi.preloads)
        self.assertIsNone(self.camera.paused_until)


class ConfigTests(unittest.TestCase):
    def test_only_the_api_on_localhost(self):
        cfg = config(URL)
        self.assertEqual(cfg["api"]["listen"], "127.0.0.1:1984")
        for server in ("rtsp", "webrtc", "srtp"):
            self.assertEqual(cfg[server]["listen"], "")
        self.assertEqual(cfg["streams"], {STREAM: [URL + "#backchannel=0"]})
        self.assertEqual(cfg["preload"], {STREAM: "video"})

    def test_config_file_is_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "go2rtc.yaml"
            write_config(path, URL)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["streams"][STREAM], [URL + "#backchannel=0"])

    def test_mask_hides_authorization(self):
        self.assertEqual(mask("Authorization: Basic cGFya2VlcjpnZWhlaW0="), "Authorization: Basic ****")
        self.assertEqual(mask('Authorization: Digest username="parkeer", response="ab"'), "Authorization: Digest ****")

    def test_trace_only_when_asked(self):
        self.assertEqual(config(URL)["log"], {"level": "info"})
        self.assertEqual(config(URL, trace=True)["log"]["rtsp"], "trace")

    def test_mask(self):
        self.assertEqual(mask(f"error url={URL}"), "error url=rtsp://****@192.168.2.50:554/stream1")
        self.assertEqual(mask("rtsp://192.168.2.50/stream1"), "rtsp://192.168.2.50/stream1")


class SupervisorTests(unittest.TestCase):
    def test_restarts_and_masks_the_log(self):
        runs = []

        def command(path):
            runs.append(path)
            return [sys.executable, "-c", f"print('WRN error url={URL}')"]

        with tempfile.TemporaryDirectory() as folder:
            camera = Go2rtc(URL, config_path=Path(folder) / "go2rtc.yaml", restart_delay=0.05, command=command)
            with self.assertLogs("parkeerwacht", logging.INFO) as logs:
                camera.start(wait=0)
                deadline = time.monotonic() + 5
                while len(runs) < 2 and time.monotonic() < deadline:
                    time.sleep(0.02)
                camera.stop()
                time.sleep(0.2)
        self.assertGreaterEqual(len(runs), 2)
        text = "\n".join(logs.output)
        self.assertIn("rtsp://****@192.168.2.50", text)
        self.assertNotIn("geheim", text)
        self.assertIn("go2rtc stopte", text)


if __name__ == "__main__":
    unittest.main()
