"""Draaien met: python -m unittest discover -s tests (met app/ op het pad)."""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from parking import iter_stream_frames  # noqa: E402
from stream import Backoff, FrameGrabber, Waiting  # noqa: E402


def part(frame: bytes) -> bytes:
    return b"--ffmpeg\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"


def jpeg(n: int) -> bytes:
    return b"\xff\xd8beeld" + str(n).encode() + b"\xff\xd9"


class FrameIteratorTests(unittest.TestCase):
    def test_yields_every_frame_in_order(self):
        data = b"".join(part(jpeg(i)) for i in range(5))
        frames = iter_stream_frames(io.BytesIO(data))
        self.assertEqual([next(frames) for _ in range(5)], [jpeg(i) for i in range(5)])

    def test_frames_split_over_reads(self):
        class OneByte(io.BytesIO):
            def read1(self, size=-1):
                return super().read1(1)

        frames = iter_stream_frames(OneByte(part(jpeg(1)) + part(jpeg(2))))
        self.assertEqual([next(frames), next(frames)], [jpeg(1), jpeg(2)])

    def test_garbage_does_not_pile_up(self):
        frames = iter_stream_frames(io.BytesIO(b"x" * 500 + part(jpeg(1))), max_bytes=1000)
        self.assertEqual(next(frames), jpeg(1))

    def test_limit_counts_per_frame(self):
        data = b"".join(part(jpeg(i) + b"y" * 50) for i in range(30))
        frames = iter_stream_frames(io.BytesIO(data), max_bytes=200)
        self.assertEqual(len([next(frames) for _ in range(30)]), 30)


class BackoffTests(unittest.TestCase):
    def test_doubles_up_to_maximum(self):
        backoff = Backoff(30, 600)
        self.assertEqual([backoff.next() for _ in range(7)], [30, 60, 120, 240, 480, 600, 600])

    def test_reset_starts_over(self):
        backoff = Backoff(30, 600)
        backoff.next(), backoff.next()
        backoff.reset()
        self.assertEqual(backoff.next(), 30)


class FakeStream(io.BytesIO):
    closed_count = 0

    def close(self):
        FakeStream.closed_count += 1
        super().close()


class FrameGrabberTests(unittest.TestCase):
    def make(self, streams, now):
        """Grabber met een lijst van streams of fouten en een klok die de test zet."""
        opened = []

        def open_stream():
            item = streams.pop(0)
            opened.append(item)
            if isinstance(item, Exception):
                raise item
            return item

        grabber = FrameGrabber(open_stream, Backoff(30, 120), clock=lambda: now[0])
        return grabber, opened

    def test_skips_first_frame_and_closes_the_stream(self):
        FakeStream.closed_count = 0
        stream = FakeStream(b"".join(part(jpeg(i)) for i in range(4)))
        grabber, _ = self.make([stream], [100.0])
        self.assertEqual(grabber.grab(), jpeg(1))
        self.assertEqual(FakeStream.closed_count, 1)

    def test_new_connection_per_grab(self):
        first = FakeStream(part(jpeg(1)) + part(jpeg(2)))
        second = FakeStream(part(jpeg(3)) + part(jpeg(4)))
        grabber, opened = self.make([first, second], [100.0])
        self.assertEqual([grabber.grab(), grabber.grab()], [jpeg(2), jpeg(4)])
        self.assertEqual(opened, [first, second])
        self.assertTrue(first.closed and second.closed)

    def test_closes_the_stream_when_reading_fails(self):
        FakeStream.closed_count = 0
        broken = FakeStream(part(jpeg(1)))  # maar één beeld, het eerste wordt overgeslagen
        grabber, _ = self.make([broken], [100.0])
        with self.assertRaises(ValueError):
            grabber.grab()
        self.assertEqual(FakeStream.closed_count, 1)

    def test_waits_longer_after_each_failure(self):
        now = [100.0]
        grabber, opened = self.make([OSError("limiet")] * 4, now)
        waits = []
        for _ in range(4):
            with self.assertRaises(OSError):
                grabber.grab()
            waits.append(grabber.retry_at - now[0])
            now[0] = grabber.retry_at
        self.assertEqual(waits, [30, 60, 120, 120])
        self.assertEqual(len(opened), 4)

    def test_no_new_attempt_while_waiting(self):
        now = [100.0]
        grabber, opened = self.make([OSError("limiet")], now)
        with self.assertRaises(OSError):
            grabber.grab()
        now[0] = 120.0  # nog 10 s te wachten
        with self.assertRaises(Waiting) as caught:
            grabber.grab()
        self.assertIn("limiet", str(caught.exception))
        self.assertEqual(len(opened), 1)

    def test_success_resets_the_wait(self):
        now = [100.0]
        good = FakeStream(part(jpeg(1)) + part(jpeg(2)))
        grabber, _ = self.make([OSError("weg"), OSError("weg"), good, OSError("weg")], now)
        for _ in range(2):
            with self.assertRaises(OSError):
                grabber.grab()
            now[0] = grabber.retry_at
        self.assertEqual(grabber.grab(), jpeg(2))
        self.assertIsNone(grabber.retry_at)
        with self.assertRaises(OSError):
            grabber.grab()
        # na het gelukte beeld begint de wachttijd opnieuw bij 30 s
        self.assertEqual(grabber.retry_at - now[0], 30)


if __name__ == "__main__":
    unittest.main()
