"""AI ship detection on SAR chips (stage 1 of the verification pipeline).

Runs a YOLOv11m detector fine-tuned on the SSDD SAR ship dataset (MIT, from
ioEclipse/SAR-SHIP-DETECTION, converted to ONNX so no pickled code ships)
over the sigma0 chip. Compared to the classical CFAR thresholder this
actually recognises SHIPS: it ignores harbor buildings and radar sidelobe
crosses, and separates neighbouring vessels - validated against our own
ground-truth chips (confirmed hulls, empty sea, Port Said harbor).

The detector returns the same ChipResult/Detection shapes as
sardetect.detect_ships, so the job logic (offset gate, persistence,
size check) is identical for both. CFAR remains the fallback when the
model file is absent.
"""

import functools
import logging
import math
import os

from ..config import get_settings
from .sardetect import ChipResult, Detection, MIN_VALID_FRACTION

logger = logging.getLogger(__name__)

MODEL_INPUT_PX = 640
CONF_THRESHOLD = 0.25
NMS_IOU = 0.5


MODEL_S3_KEY = "models/sar_ship_yolov11m.onnx"


def _fetch_model(path: str) -> bool:
    """Pull the ONNX model from the S3 bucket (MinIO locally, Wasabi in prod)
    into the local cache path. The 80 MB artifact lives in object storage,
    not in git or the image."""
    from ..config import get_settings as _gs
    from .chipstore import _client

    s = _gs()
    if not s.cold_storage_enabled:
        return False
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        _client().download_file(s.s3_bucket, MODEL_S3_KEY, path)
        logger.info("SAR detection model fetched from s3://%s/%s",
                    s.s3_bucket, MODEL_S3_KEY)
        return True
    except Exception:
        logger.warning("SAR detection model not available at s3://%s/%s - "
                       "falling back to CFAR", s.s3_bucket, MODEL_S3_KEY)
        return False


@functools.lru_cache(maxsize=1)
def _session():
    """ONNX Runtime session, or None when the model can't be sourced."""
    path = get_settings().sar_model_path
    if not os.path.exists(path) and not _fetch_model(path):
        return None
    try:
        import onnxruntime as ort

        return ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    except Exception:
        logger.exception("Could not load SAR detection model (%s)", path)
        return None


def model_available() -> bool:
    return _session() is not None


def _to_model_input(chip):
    """sigma0 chip -> the exact rendering the model was validated on:
    -25..0 dB stretched to 0..255 grayscale, replicated to 3 channels,
    nearest-neighbour resized to 640x640, NCHW float32 0..1."""
    import numpy as np

    valid = chip > 0
    db = np.full(chip.shape, -30.0, dtype=np.float32)
    db[valid] = 10.0 * np.log10(np.maximum(chip[valid], 1e-6))
    gray = np.clip((db + 25.0) / 25.0, 0.0, 1.0)
    idx_y = (np.arange(MODEL_INPUT_PX) * chip.shape[0] / MODEL_INPUT_PX).astype(int)
    idx_x = (np.arange(MODEL_INPUT_PX) * chip.shape[1] / MODEL_INPUT_PX).astype(int)
    resized = gray[np.ix_(idx_y, idx_x)]
    return np.repeat(resized[None, None, :, :], 3, axis=1).astype(np.float32)


def _nms(boxes, scores, iou_thr):
    """Plain numpy non-max suppression; returns kept indices."""
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


def detect_ships_ml(chip, m_per_px: float) -> ChipResult | None:
    """YOLO pass over the chip. Returns None when the model isn't available
    (caller falls back to CFAR). Detection.length_m is the box's long side -
    the measured size used by the can-it-be-that-ship gate."""
    import numpy as np

    sess = _session()
    if sess is None:
        return None
    valid = chip > 0
    if float(valid.mean()) < MIN_VALID_FRACTION:
        return ChipResult(valid=False)  # chip mostly outside the swath

    out = sess.run(None, {sess.get_inputs()[0].name: _to_model_input(chip)})[0]
    # YOLO detect head: (1, 4+nc, anchors) with xywh in model pixels
    pred = out[0].T  # (anchors, 4+nc)
    conf = pred[:, 4:].max(axis=1)
    sel = conf >= CONF_THRESHOLD
    pred, conf = pred[sel], conf[sel]

    scale = chip.shape[0] / MODEL_INPUT_PX  # square chips, square model input
    cy0, cx0 = (chip.shape[0] - 1) / 2.0, (chip.shape[1] - 1) / 2.0
    detections: list[Detection] = []
    if len(pred):
        cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
        for i in _nms(boxes, conf, NMS_IOU):
            bx, by = float(cx[i]) * scale, float(cy[i]) * scale
            bw, bh = float(w[i]) * scale, float(h[i]) * scale
            offset = math.hypot(by - cy0, bx - cx0) * m_per_px
            y0, y1 = max(0, int(by - bh / 2)), min(chip.shape[0], int(by + bh / 2) + 1)
            x0, x1 = max(0, int(bx - bw / 2)), min(chip.shape[1], int(bx + bw / 2) + 1)
            patch = chip[y0:y1, x0:x1]
            peak = float(10.0 * np.log10(max(float(patch.max()), 1e-6))) if patch.size else -30.0
            detections.append(Detection(
                offset_m=round(offset, 1),
                area_px=int(round(bw * bh)),
                peak_db=round(peak, 1),
                y_px=by, x_px=bx,
                length_m=round(max(bw, bh) * m_per_px, 1),
                confidence=round(float(conf[i]), 2),
            ))
    detections.sort(key=lambda d: d.offset_m)
    med = float(np.median(chip[valid]))
    return ChipResult(valid=True, detections=detections,
                      clutter_db=round(10.0 * math.log10(max(med, 1e-9)), 1))
