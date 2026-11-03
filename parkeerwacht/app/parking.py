"""Herkenning en statuslogica: geen netwerk, zodat dit los te testen is."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw

# Vak B in fracties van het beeld (1280x720: 370,360 600,175 420,75 235,270)
DEFAULT_ZONE = [[0.2891, 0.5], [0.4688, 0.2431], [0.3281, 0.1042], [0.1836, 0.375]]
VEHICLE_CLASSES = {2, 5, 7}  # COCO: car, bus, truck
INPUT_SIZE = 416
MIN_BRIGHTNESS = 10  # gemiddeld 0-255; donkerder zegt het beeld niets over het vak


@dataclass
class Vehicle:
    box: Tuple[float, float, float, float]
    confidence: float
    coverage: float


@dataclass
class Reading:
    occupied: Optional[bool]  # None: beeld is niet te beoordelen
    coverage: float
    confidence: float
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


def validate_zone(points) -> List[List[float]]:
    if not isinstance(points, list) or not 3 <= len(points) <= 12:
        raise ValueError("een vak heeft 3 tot 12 hoekpunten")
    zone = []
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("een hoekpunt bestaat uit x en y")
        x, y = point
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ValueError("hoekpunten moeten getallen zijn")
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("hoekpunten moeten binnen het beeld liggen")
        zone.append([float(x), float(y)])
    return zone


def zone_mask(zone: Sequence[Sequence[float]], width: int, height: int) -> np.ndarray:
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).polygon([(x * width, y * height) for x, y in zone], fill=1)
    return np.asarray(mask, dtype=bool)


def box_coverage(box: Sequence[float], mask: np.ndarray) -> float:
    """Welk deel van het vak door deze box bedekt wordt (0-1)."""
    total = mask.sum()
    if not total:
        return 0.0
    height, width = mask.shape
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    x1, x2 = np.clip([x1, x2], 0, width)
    y1, y2 = np.clip([y1, y2], 0, height)
    return float(mask[y1:y2, x1:x2].sum() / total)


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

    def read(self, image: Image.Image, zone, min_confidence: float, min_coverage: float) -> Reading:
        if np.asarray(image.convert("L")).mean() < MIN_BRIGHTNESS:
            return Reading(None, 0.0, 0.0, [])
        mask = zone_mask(zone, image.width, image.height)
        found = [
            Vehicle(box, confidence, box_coverage(box, mask))
            for box, confidence in self.vehicles(image, min_confidence)
        ]
        best = max(found, key=lambda v: v.coverage, default=None)
        coverage = best.coverage if best else 0.0
        return Reading(coverage >= min_coverage, coverage, best.confidence if best else 0.0, found)


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


def annotate(image: Image.Image, zone, reading: Reading, min_coverage: float) -> Image.Image:
    result = image.copy()
    draw = ImageDraw.Draw(result)
    points = [(x * image.width, y * image.height) for x, y in zone]
    color = (230, 60, 50) if reading.occupied else (40, 190, 90)
    draw.line(points + points[:1], fill=color, width=4)
    for vehicle in reading.vehicles:
        draw.rectangle(vehicle.box, outline=color if vehicle.coverage >= min_coverage else (250, 200, 60), width=2)
    return result
