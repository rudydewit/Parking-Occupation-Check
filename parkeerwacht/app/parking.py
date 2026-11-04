"""Herkenning, meetlat en statuslogica: geen netwerk, zodat dit los te testen is."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw

VEHICLE_CLASSES = {2, 5, 7}  # COCO: car, bus, truck
INPUT_SIZE = 416
MIN_BRIGHTNESS = 10  # gemiddeld 0-255; donkerder zegt het beeld niets over de laadzone
FRAME_WIDTH = 1280  # de meetlat rekent in beeldpunten van een beeld van 1280x720
FRAME_HEIGHT = 720
FRONT_OVERHANG = 0.7  # meter van voorwiel tot neus, de onderkant van een box is meestal het voorwiel

# Meetlat voor deze camera in deze stand, bepaald uit de voegen van de stoeptegels.
# Verdraait de camera, dan kloppen deze waarden niet meer.
LENS = -0.37  # delingsmodel, straal in halve beeldbreedtes
LENS_CENTER = np.array([640.0, 360.0])
KERB_POINT = np.array([394.824, 362.381])  # punt op de rechte stoeprand, na lenscorrectie
KERB_DIRECTION = np.array([0.777720, -0.628611])  # langs de stoeprand, van de camera af
KERB_NORMAL = np.array([0.628611, 0.777720])  # dwars op de stoeprand, naar de stoep
CROSS_VANISHING = (-1000.0, -1150.0)  # waar de voegen dwars op de rand samenkomen, in langs/dwars
TILE_FIT = (0.016746026, 8.44155, -0.001159529)  # tegelnummer = (a u + b) / (1 + c u)
FRAME_EDGE_TILE = -3.777  # tegelnummer waar de stoeprand de onderrand van het beeld raakt
WHEEL_OFFSET = -8.1  # beeldpunten tussen stoeprand en wielen van een geparkeerde auto


class KerbRuler:
    """Rekent beeldpunten om naar meters langs de stoeprand; 0 m is de onderrand van het beeld."""

    def __init__(self, tile_size: float):
        self.tile_size = tile_size
        along = np.arange(-1100.0, 800.0)
        pixels = self.distort(KERB_POINT + along[:, None] * KERB_DIRECTION + WHEEL_OFFSET * KERB_NORMAL)
        inside = (pixels[:, 0] >= 0) & (pixels[:, 1] >= 0) & (pixels[:, 1] < FRAME_HEIGHT)
        self._line_along, self._line_pixels = along[inside], pixels[inside]

    @staticmethod
    def undistort(points) -> np.ndarray:
        offset = (np.asarray(points, dtype=float) - LENS_CENTER) / LENS_CENTER[0]
        return LENS_CENTER + LENS_CENTER[0] * offset / (1 + LENS * (offset ** 2).sum(-1, keepdims=True))

    @staticmethod
    def distort(points) -> np.ndarray:
        offset = (np.asarray(points, dtype=float) - LENS_CENTER) / LENS_CENTER[0]
        radius = np.sqrt((offset ** 2).sum(-1, keepdims=True)) + 1e-9
        distorted = (1 - np.sqrt(np.maximum(1 - 4 * LENS * radius ** 2, 0))) / (2 * LENS * radius)
        return LENS_CENTER + LENS_CENTER[0] * offset * distorted / radius

    def metres(self, along) -> np.ndarray:
        a, b, c = TILE_FIT
        return ((a * along + b) / (1 + c * along) - FRAME_EDGE_TILE) * self.tile_size

    def kerb_pixels(self, start: float, end: float, steps: int = 40) -> np.ndarray:
        """Beeldpunten van de stoeprand tussen twee afstanden in meters."""
        a, b, c = TILE_FIT
        tiles = np.linspace(start, end, steps) / self.tile_size + FRAME_EDGE_TILE
        along = (tiles - b) / (a - tiles * c)
        return self.distort(KERB_POINT + along[:, None] * KERB_DIRECTION)

    def _on_kerb(self, along: float) -> float:
        """Volgt de dwarsrichting van de wielenlijn naar de stoeprand en geeft meters."""
        vanishing_along, vanishing_across = CROSS_VANISHING
        return float(self.metres(along + (vanishing_along - along) * -WHEEL_OFFSET / (vanishing_across - WHEEL_OFFSET)))

    def span(self, box: Sequence[float]) -> Optional[Tuple[float, float]]:
        """Stuk stoeprand dat een auto inneemt, of None als hij niet tegen deze stoeprand staat."""
        x1, _, x2, y2 = box
        corner = self.undistort((min(x2, FRAME_WIDTH - 1.0), min(y2, FRAME_HEIGHT - 1.0))) - KERB_POINT
        if corner @ KERB_NORMAL < 0:
            return None  # de rechteronderhoek blijft aan de straatkant: rijbaan of overkant
        rear = self._on_kerb(self._line_along[np.abs(self._line_pixels[:, 0] - x2).argmin()])
        if x1 <= 2 or y2 >= FRAME_HEIGHT - 2:
            return -np.inf, rear  # de neus valt buiten beeld
        front = self._on_kerb(self._line_along[np.abs(self._line_pixels[:, 1] - y2).argmin()]) - FRONT_OVERHANG
        return min(front, rear), max(front, rear)


@dataclass(frozen=True)
class ChargeZone:
    start: float  # meter vanaf de onderrand van het beeld
    end: float
    min_free: float  # zoveel meter aaneengesloten moet vrij zijn


@dataclass
class Vehicle:
    box: Tuple[float, float, float, float]
    confidence: float
    span: Optional[Tuple[float, float]]  # None: staat niet tegen deze stoeprand


@dataclass
class Reading:
    occupied: Optional[bool]  # None: beeld is niet te beoordelen
    free: float  # grootste vrije stuk in meters
    gap: Optional[Tuple[float, float]]
    vehicles: List[Vehicle]


def read_stream_frame(stream, skip: int = 1, max_bytes: int = 20_000_000) -> bytes:
    """Leest een JPEG uit een MJPEG-stream; het eerste beeld kan nog onvolledig zijn."""
    buffer = b""
    total = 0
    while total <= max_bytes:
        start = buffer.find(b"\xff\xd8")
        end = buffer.find(b"\xff\xd9", start + 2) if start >= 0 else -1
        if end >= 0:
            if skip == 0:
                return buffer[start:end + 2]
            skip -= 1
            buffer = buffer[end + 2:]
            continue
        chunk = stream.read1(65536)
        if not chunk:
            raise ValueError("stream stopte zonder compleet beeld")
        buffer += chunk
        total += len(chunk)
    raise ValueError("geen compleet beeld in de stream gevonden")


def stream_token(state: dict) -> str:
    """Token uit de status van een camera in Home Assistant; een onbeschikbare camera heeft er geen."""
    token = state.get("attributes", {}).get("access_token")
    if not token:
        raise ValueError(f"camera niet beschikbaar (status: {state.get('state')})")
    return token


def largest_gap(spans: Sequence[Tuple[float, float]], zone: ChargeZone) -> Tuple[float, float]:
    """Grootste stuk van de zone waar geen auto staat; een lege lengte als alles vol staat."""
    position, best = zone.start, (zone.start, zone.start)
    for front, rear in sorted(spans):
        front = min(front, zone.end)
        if front - position > best[1] - best[0]:
            best = (position, front)
        position = max(position, min(rear, zone.end))
    return (position, zone.end) if zone.end - position > best[1] - best[0] else best


class Detector:
    def __init__(self, model_path: str):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        self.session = ort.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        grids, strides = [], []
        for stride in (8, 16, 32):
            size = INPUT_SIZE // stride
            xs, ys = np.meshgrid(np.arange(size), np.arange(size))
            grids.append(np.stack((xs, ys), 2).reshape(-1, 2))
            strides.append(np.full((size * size, 1), stride))
        self.grid, self.strides = np.concatenate(grids), np.concatenate(strides)

    def vehicles(self, image: Image.Image, min_confidence: float) -> List[Tuple[Tuple[float, ...], float]]:
        ratio = min(INPUT_SIZE / image.height, INPUT_SIZE / image.width)
        width, height = int(image.width * ratio), int(image.height * ratio)
        canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, dtype=np.float32)
        canvas[:height, :width] = np.asarray(image.resize((width, height), Image.BILINEAR), dtype=np.float32)
        # YOLOX verwacht BGR met waarden 0-255
        batch = canvas[:, :, ::-1].transpose(2, 0, 1)[None].copy()
        output = self.session.run(None, {self.input_name: batch})[0][0]
        centers = (output[:, :2] + self.grid) * self.strides
        sizes = np.exp(output[:, 2:4]) * self.strides
        boxes = np.concatenate((centers - sizes / 2, centers + sizes / 2), 1) / ratio
        scores = (output[:, 4, None] * output[:, 5:])[:, sorted(VEHICLE_CLASSES)].max(1)
        keep = scores >= min_confidence
        boxes, scores = boxes[keep], scores[keep]
        return [(tuple(float(v) for v in boxes[i]), float(scores[i])) for i in _nms(boxes, scores)]

    def read(self, image: Image.Image, ruler: KerbRuler, zone: ChargeZone, min_confidence: float) -> Reading:
        if np.asarray(image.convert("L")).mean() < MIN_BRIGHTNESS:
            return Reading(None, 0.0, None, [])
        scale = FRAME_WIDTH / image.width
        found = [
            Vehicle(box, confidence, ruler.span([v * scale for v in box]))
            for box, confidence in self.vehicles(image, min_confidence)
        ]
        gap = largest_gap([v.span for v in found if v.span], zone)
        free = gap[1] - gap[0]
        return Reading(free < zone.min_free, free, gap, found)


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float = 0.45) -> List[int]:
    order, keep = scores.argsort()[::-1], []
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        current, rest = order[0], order[1:]
        keep.append(int(current))
        x1 = np.maximum(boxes[current, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[current, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[current, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[current, 3], boxes[rest, 3])
        overlap = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
        order = rest[overlap / (areas[current] + areas[rest] - overlap + 1e-6) <= threshold]
    return keep


class OccupancyTracker:
    """Wisselt pas na een aantal gelijke metingen op rij, zodat passanten niets doen."""

    def __init__(self, confirm_occupied: int, confirm_free: int):
        self.confirm_occupied = confirm_occupied
        self.confirm_free = confirm_free
        self.state: Optional[bool] = None
        self._streak = 0

    def reset(self) -> None:
        self.state = None
        self._streak = 0

    def update(self, occupied: Optional[bool]) -> bool:
        """Verwerkt een meting en geeft True terug als de status wisselt."""
        if occupied is None:
            return False
        if self.state is None:
            self.state = occupied
            return True
        if occupied == self.state:
            self._streak = 0
            return False
        self._streak += 1
        if self._streak < (self.confirm_occupied if occupied else self.confirm_free):
            return False
        self.state = occupied
        self._streak = 0
        return True


def annotate(image: Image.Image, ruler: KerbRuler, zone: ChargeZone, reading: Reading) -> Image.Image:
    """Tekent de laadzone in rood, het grootste vrije stuk daarbinnen in groen en de auto's."""
    result = image.copy()
    draw = ImageDraw.Draw(result)
    scale = image.width / FRAME_WIDTH

    def kerb_line(start: float, end: float, color) -> None:
        draw.line([tuple(p * scale) for p in ruler.kerb_pixels(start, end)], fill=color, width=6)

    kerb_line(zone.start, zone.end, (230, 60, 50))
    if reading.gap and reading.free > 0:
        kerb_line(reading.gap[0], reading.gap[1], (40, 190, 90))
    for vehicle in reading.vehicles:
        counts = vehicle.span and vehicle.span[0] < zone.end and vehicle.span[1] > zone.start
        draw.rectangle(vehicle.box, outline=(230, 60, 50) if counts else (250, 200, 60), width=2)
    return result
