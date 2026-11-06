"""Draaien met: python -m unittest discover -s tests (met app/ op het pad)."""
import io
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from parking import iter_stream_frames  # noqa: E402
from stream import Backoff, StreamReader  # noqa: E402


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


class StreamReaderTests(unittest.TestCase):
    def make(self, streams):
        """Reader met een lijst van streams of fouten; wachten wordt alleen genoteerd."""
        waits = []
        opened = []
        done = threading.Event()

        def open_stream():
            if not streams:
                done.set()
                reader.stop()
                raise ValueError("klaar")
            item = streams.pop(0)
            opened.append(item)
            if isinstance(item, Exception):
                raise item
            return item

        def wait(seconds):
            waits.append(seconds)
            return done.is_set()

        reader = StreamReader(open_stream, Backoff(30, 120), clock=lambda: 100.0, wait=wait)
        return reader, waits, opened

    def test_keeps_newest_frame_and_skips_first(self):
        stream = FakeStream(b"".join(part(jpeg(i)) for i in range(4)))
        reader, waits, _ = self.make([stream])
        reader.run()
        self.assertEqual(reader.latest(), (jpeg(3), 100.0))

    def test_waits_longer_after_each_failure(self):
        reader, waits, _ = self.make([OSError("limiet"), OSError("limiet"), OSError("limiet"), OSError("limiet")])
        reader.run()
        self.assertEqual(waits, [30, 60, 120, 120])
        self.assertIn("limiet", reader.error or "klaar")

    def test_success_resets_the_wait(self):
        good = FakeStream(part(jpeg(1)) + part(jpeg(2)))
        reader, waits, _ = self.make([OSError("weg"), OSError("weg"), good, OSError("weg")])
        reader.run()
        # na de gelukte verbinding begint de wachttijd opnieuw bij 30 s
        self.assertEqual(waits[:4], [30, 60, 30, 60])

    def test_closes_the_stream(self):
        FakeStream.closed_count = 0
        reader, _, _ = self.make([FakeStream(part(jpeg(1)) + part(jpeg(2)))])
        reader.run()
        self.assertEqual(FakeStream.closed_count, 1)

    def test_one_connection_for_many_frames(self):
        stream = FakeStream(b"".join(part(jpeg(i)) for i in range(50)))
        reader, _, opened = self.make([stream])
        reader.run()
        self.assertEqual(opened.count(stream), 1)
        self.assertEqual(reader.latest()[0], jpeg(49))

    def test_reconnect_request_closes_and_reopens(self):
        holder = {}

        class Frozen(FakeStream):
            def read1(self, size=-1):
                holder["reader"].reconnect()  # zoals de meetlus doet bij een bevroren beeld
                return super().read1(size)

        first = Frozen(b"".join(part(jpeg(i)) for i in range(5)))
        second = FakeStream(part(jpeg(7)) + part(jpeg(8)))
        reader, waits, opened = self.make([first, second])
        holder["reader"] = reader
        reader.run()
        self.assertEqual(opened[:2], [first, second])
        self.assertTrue(first.closed)
        self.assertEqual(reader.latest()[0], jpeg(8))

if __name__ == "__main__":
    unittest.main()
