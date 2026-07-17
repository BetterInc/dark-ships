"""Optical (true-colour) ship detection on Sentinel-2 chips.

Radar sees through cloud and dark but misses low-reflectivity hulls (wooden /
fibreglass fishing & smuggling craft) and ships lost in sea clutter. When a
cloud-free daylight Sentinel-2 pass exists, this runs a SEPARATE detector -
a YOLOv8m trained on Sentinel-2 RGB (mayrajeo/marine-vessel-detection-yolov8,
AGPL-3.0, converted to ONNX) - on the colour chip. So a claim radar couldn't
confirm can still be verified in daylight.

Mirrors services/shipdetect.py (S3-fetched model, CPU ONNX), but the input is
a real RGB image, not radar backscatter, so it has its own preprocessing.
Absent model -> returns None and the optical chip stays display-only.
"""

import functools
import io
import logging
import math
import os

from ..config import get_settings
from .sardetect import ChipResult, Detection

logger = logging.getLogger(__name__)

MODEL_INPUT_PX = 640
CONF_THRESHOLD = 0.30   # a touch stricter than SAR - optical false-positives on wake/glint
NMS_IOU = 0.5
MODEL_S3_KEY = "models/optical_ship_yolov8m.onnx"
# a detection this close to the chip centre counts as "at the claimed spot"
MATCH_RADIUS_M = 500.0


def _fetch_model(path: str) -> bool:
    from .chipstore import _client

    s = get_settings()
    if not s.cold_storage_enabled:
        return False
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        _client().download_file(s.s3_bucket, MODEL_S3_KEY, path)
        logger.info("Optical model fetched from s3://%s/%s", s.s3_bucket, MODEL_S3_KEY)
        return True
    except Exception:
        logger.warning("Optical model not available at s3://%s/%s", s.s3_bucket, MODEL_S3_KEY)
        return False


@functools.lru_cache(maxsize=1)
def _session():
    path = get_settings().optical_model_path
    if not os.path.exists(path) and not _fetch_model(path):
        return None
    try:
        import onnxruntime as ort

        return ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    except Exception:
        logger.exception("Could not load optical detection model (%s)", path)
        return None


def optical_model_available() -> bool:
    return _session() is not None


def _nms(boxes, scores, iou_thr):
    import numpy as np

    order = np.argsort(scores)[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_o = ((boxes[order[1:], 2] - boxes[order[1:], 0])
                  * (boxes[order[1:], 3] - boxes[order[1:], 1]))
        iou = inter / np.maximum(area_i + area_o - inter, 1e-9)
        order = order[1:][iou <= iou_thr]
    return keep


def detect_optical(png: bytes, m_per_px: float) -> ChipResult | None:
    """Run the optical detector on a true-colour chip PNG. Returns a ChipResult
    (same shape as the SAR detector) or None when the model isn't available."""
    import numpy as np
    from PIL import Image

    sess = _session()
    if sess is None:
        return None
    try:
        img = Image.open(io.BytesIO(png)).convert("RGB")
    except Exception:
        return ChipResult(valid=False)
    arr = np.asarray(img, dtype=np.float32)
    h, w = arr.shape[:2]
    # a nearly-black tile (out of swath / cloud-masked) carries no image
    if float((arr.max(axis=2) > 25).mean()) < 0.1:
        return ChipResult(valid=False)

    # -> 640x640 NCHW float 0..1 (nearest-neighbour resize, no extra deps)
    iy = (np.arange(MODEL_INPUT_PX) * h / MODEL_INPUT_PX).astype(int)
    ix = (np.arange(MODEL_INPUT_PX) * w / MODEL_INPUT_PX).astype(int)
    inp = (arr[np.ix_(iy, ix)] / 255.0).transpose(2, 0, 1)[None].astype(np.float32)

    out = sess.run(None, {sess.get_inputs()[0].name: inp})[0]
    pred = out[0].T  # (anchors, 4+nc)
    conf = pred[:, 4:].max(axis=1)
    sel = conf >= CONF_THRESHOLD
    pred, conf = pred[sel], conf[sel]

    sx, sy = w / MODEL_INPUT_PX, h / MODEL_INPUT_PX
    cy0, cx0 = (h - 1) / 2.0, (w - 1) / 2.0
    detections: list[Detection] = []
    if len(pred):
        cx, cy, bw, bh = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
        for i in _nms(boxes, conf, NMS_IOU):
            bx, by = float(cx[i]) * sx, float(cy[i]) * sy
            w_m, h_m = float(bw[i]) * sx, float(bh[i]) * sy
            offset = math.hypot(by - cy0, bx - cx0) * m_per_px
            detections.append(Detection(
                offset_m=round(offset, 1),
                area_px=int(round(w_m * h_m)),
                peak_db=0.0,
                y_px=by, x_px=bx,
                length_m=round(max(w_m, h_m) * m_per_px, 1),
                confidence=round(float(conf[i]), 2),
            ))
    detections.sort(key=lambda d: d.offset_m)
    return ChipResult(valid=True, detections=detections)


def optical_hull_at_claim(result: ChipResult | None) -> bool | None:
    """True if the optical detector found a vessel within MATCH_RADIUS_M of the
    claim; False if it looked and found none there; None if unjudgeable/no model."""
    if result is None or not result.valid:
        return None
    return any(d.offset_m <= MATCH_RADIUS_M for d in result.detections)
