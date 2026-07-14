"""Ship detection in a Sentinel-1 sigma0 chip.

Metal hulls are strong radar reflectors: on a calm-to-moderate sea a ship is
tens of times brighter than the water around it. We use a CFAR (constant
false-alarm rate) threshold under the standard exponential model for sea
clutter intensity: P(X > t) = exp(-t/mu) gives t = mu * -ln(Pfa), with the
clutter mean estimated robustly from the median (mu = med/ln 2) so the ships
in view can't inflate their own threshold. A small chip is almost entirely
sea, so the estimate holds even at a busy anchorage. Contiguous bright
pixels are grouped into targets; each target's offset from the chip centre
(= the claimed AIS position) is reported in metres.

Honesty notes: this measures "bright radar target", not "identified hull" -
breakwaters, small islets and off-swath artefacts can reflect too, which is
why the Copernicus Browser link stays next to every automated verdict. We
never extrapolate: a chip that is mostly no-data yields verdict None, not a
guess.
"""

import io
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MIN_VALID_FRACTION = 0.35  # chip must actually contain this much swath data
PFA = 1e-7                 # per-pixel false alarms: ~0.01 px per 300x300 chip
MIN_TARGET_PX = 3          # >= 3 px at 10 m/px ~= a 30 m object
MATCH_RADIUS_M = 500.0     # target within this of the claim = hull present


@dataclass
class Detection:
    offset_m: float
    area_px: int
    peak_db: float
    y_px: float = 0.0  # component centroid, for cross-pass comparison
    x_px: float = 0.0


@dataclass
class ChipResult:
    valid: bool                 # enough swath data to judge at all
    detections: list[Detection] = field(default_factory=list)
    clutter_db: float = 0.0     # sea-clutter median, for context/debugging

    @property
    def hull_detected(self) -> bool:
        return any(d.offset_m <= MATCH_RADIUS_M for d in self.detections)

    @property
    def nearest_offset_m(self) -> float | None:
        return min((d.offset_m for d in self.detections), default=None)


def detect_ships(chip, m_per_px: float) -> ChipResult:
    """chip: 2D float32 sigma0 (linear power, < 0 = no data)."""
    import math

    import numpy as np

    valid = chip > 0
    if valid.mean() < MIN_VALID_FRACTION:
        return ChipResult(valid=False)

    med = float(np.median(chip[valid]))
    if med <= 0:
        return ChipResult(valid=False)
    # exponential-clutter CFAR: mean from the (ship-immune) median, then the
    # threshold that keeps the per-pixel false-alarm rate at PFA
    mu = med / math.log(2)
    thr = mu * -math.log(PFA)  # ~= median + 13.7 dB

    db = np.full(chip.shape, -40.0, dtype=np.float32)
    db[valid] = 10.0 * np.log10(np.maximum(chip[valid], 1e-6))
    mask = (chip > thr) & valid
    cy, cx = (chip.shape[0] - 1) / 2.0, (chip.shape[1] - 1) / 2.0

    detections: list[Detection] = []
    seen = np.zeros(chip.shape, dtype=bool)
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if seen[y0, x0]:
            continue
        # flood fill one connected component (8-neighbour)
        stack = [(y0, x0)]
        seen[y0, x0] = True
        comp: list[tuple[int, int]] = []
        while stack:
            y, x = stack.pop()
            comp.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if (0 <= ny < chip.shape[0] and 0 <= nx < chip.shape[1]
                            and mask[ny, nx] and not seen[ny, nx]):
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if len(comp) < MIN_TARGET_PX:
            continue
        my = sum(p[0] for p in comp) / len(comp)
        mx = sum(p[1] for p in comp) / len(comp)
        offset = float(((my - cy) ** 2 + (mx - cx) ** 2) ** 0.5) * m_per_px
        peak = float(max(db[y, x] for y, x in comp))
        detections.append(Detection(offset_m=round(offset, 1),
                                    area_px=len(comp), peak_db=round(peak, 1),
                                    y_px=my, x_px=mx))

    detections.sort(key=lambda d: d.offset_m)
    return ChipResult(valid=True, detections=detections,
                      clutter_db=round(10.0 * math.log10(med), 1))


PERSIST_RADIUS_M = 150.0  # same-spot tolerance across passes (geocoding jitter)


def target_is_persistent(current: ChipResult, reference: ChipResult,
                         m_per_px: float) -> bool | None:
    """Was the target confirming this claim ALSO bright on a pass weeks
    earlier? A hull that 'never moves' across weeks is more likely a fixed
    structure (wind turbine, platform, islet) - or a very long-anchored ship;
    we report the measurement, the UI words the ambiguity. None = the
    reference pass couldn't be judged."""
    if not reference.valid:
        return None
    at_claim = [d for d in current.detections if d.offset_m <= MATCH_RADIUS_M]
    if not at_claim:
        return None
    tgt = min(at_claim, key=lambda d: d.offset_m)
    return any(
        ((r.y_px - tgt.y_px) ** 2 + (r.x_px - tgt.x_px) ** 2) ** 0.5 * m_per_px
        <= PERSIST_RADIUS_M
        for r in reference.detections)


def _encode_png_gray(gray) -> bytes:
    """Minimal 8-bit grayscale PNG encoder (stdlib only - saves a Pillow dep
    for the one image format we ever write)."""
    import struct
    import zlib

    h, w = gray.shape
    raw = b"".join(b"\x00" + gray[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)  # 8-bit grayscale
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def render_chip_png(chip) -> bytes:
    """Grayscale PNG of the chip for the human eye: -25..0 dB stretched to
    0..255, no annotations drawn (the image stays exactly what the satellite
    measured)."""
    import numpy as np

    valid = chip > 0
    db = np.full(chip.shape, -30.0, dtype=np.float32)
    db[valid] = 10.0 * np.log10(np.maximum(chip[valid], 1e-6))
    scaled = np.clip((db + 25.0) / 25.0, 0.0, 1.0)
    return _encode_png_gray(np.ascontiguousarray((scaled * 255).astype(np.uint8)))
