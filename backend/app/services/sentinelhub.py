"""Sentinel-1 chip fetch via the Copernicus Data Space Sentinel Hub API.

Instead of downloading the ~1.7 GB GRD product, the Process API renders a
small AOI server-side: we request a ~3x3 km float32 VV sigma0 chip (10 m/px)
around the vessel's claimed position for the exact acquisition we already
matched in the catalogue. That keeps a check at ~350 KB and needs no local
SAR toolchain.

Auth is an OAuth2 client-credentials flow against the CDSE identity server
(free account -> user settings -> OAuth clients). Without credentials the
SAR-detection job simply skips - the human Copernicus Browser links keep
working as before.
"""

import asyncio
import io
import logging
import math
import time
from datetime import datetime, timedelta, timezone

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

TOKEN_URL = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE"
             "/protocol/openid-connect/token")
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

CHIP_HALF_KM = 1.5   # chip covers claimed position +/- this -> 3x3 km
CHIP_PX = 300        # 3 km / 300 px = 10 m/px (native GRD IW pixel spacing)

# sigma0 VV as raw float32 so the detector sees calibrated backscatter, not
# a visualisation. dataMask marks pixels outside the swath (0 = no data).
EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["VV", "dataMask"] }],
    output: { bands: 1, sampleType: "FLOAT32" },
  };
}
function evaluatePixel(s) {
  return [s.dataMask === 1 ? s.VV : -1.0];
}
"""

_token_cache: tuple[float, str] | None = None  # (expiry monotonic, token)
_token_lock = asyncio.Lock()


async def _token(client: httpx.AsyncClient) -> str:
    global _token_cache
    async with _token_lock:
        if _token_cache and time.monotonic() < _token_cache[0]:
            return _token_cache[1]
        s = get_settings()
        resp = await client.post(TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": s.cdse_sh_client_id,
            "client_secret": s.cdse_sh_client_secret,
        })
        resp.raise_for_status()
        payload = resp.json()
        # refresh a minute early so an in-flight request never carries a token
        # that expires mid-processing
        _token_cache = (time.monotonic() + payload.get("expires_in", 600) - 60,
                        payload["access_token"])
        return _token_cache[1]


def chip_bbox(lat: float, lon: float, half_km: float = CHIP_HALF_KM) -> list[float]:
    """[lonMin, latMin, lonMax, latMax] of a half_km-radius box around a point."""
    dlat = half_km / 111.32
    dlon = half_km / (111.32 * max(0.05, math.cos(math.radians(lat))))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]


async def fetch_s1_chip(lat: float, lon: float, acquired_at: datetime | None = None,
                        t_from: datetime | None = None, t_to: datetime | None = None,
                        ) -> "tuple | None":
    """Fetch a float32 VV sigma0 chip of (lat, lon). Pass acquired_at to
    isolate one catalogued acquisition (a one-hour window around it), or an
    explicit [t_from, t_to] window to render the most recent acquisition in
    that range (used for the persistent-target reference pass). Returns
    (numpy 2D array, metres_per_pixel) or None when the chip can't be
    produced (no credentials, API error, or the AOI is outside every swath)."""
    import numpy as np
    import tifffile

    if not get_settings().sar_detection_enabled:
        return None

    if acquired_at is not None:
        t_from = acquired_at - timedelta(minutes=30)
        t_to = acquired_at + timedelta(minutes=30)
    assert t_from is not None and t_to is not None
    t0 = t_from.astimezone(timezone.utc)
    t1 = t_to.astimezone(timezone.utc)
    bbox = chip_bbox(lat, lon)
    body = {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    "timeRange": {
                        "from": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to": t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                    "mosaickingOrder": "mostRecent",
                },
                "processing": {
                    "backCoeff": "SIGMA0_ELLIPSOID",
                    "orthorectify": True,
                },
            }],
        },
        "output": {
            "width": CHIP_PX, "height": CHIP_PX,
            "responses": [{"identifier": "default",
                           "format": {"type": "image/tiff"}}],
        },
        "evalscript": EVALSCRIPT,
    }

    async with httpx.AsyncClient(timeout=90) as client:
        try:
            token = await _token(client)
            resp = await client.post(
                PROCESS_URL, json=body,
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "image/tiff"})
            resp.raise_for_status()
        except Exception:
            logger.exception("Sentinel Hub chip fetch failed (%.4f, %.4f @ %s)",
                             lat, lon, acquired_at)
            return None

    try:
        # tifffile, not Pillow: these TIFFs are deflate-compressed with the
        # floating-point predictor, which Pillow silently decodes into garbage
        arr = tifffile.imread(io.BytesIO(resp.content)).astype(np.float32)
    except Exception:
        logger.exception("Could not decode Sentinel Hub TIFF chip")
        return None
    if arr.ndim != 2 or arr.size == 0:
        return None
    # metres per pixel from the actual bbox height (fixed 3 km / 300 px, but
    # derive it so a CHIP_* tweak can't silently skew offsets)
    m_per_px = (bbox[3] - bbox[1]) * 111_320.0 / arr.shape[0]
    return arr, m_per_px


# Sentinel-2 true colour: cloud-free daylight only, but a real colour photo of
# the hull - a human-friendly companion to the radar chip. The Process API
# renders the PNG for us; leastCC picks the clearest scene in the window and we
# reject the tile if too much of it is cloud (the SAR verdict is the truth, the
# optical is a bonus when the sky cooperated).
S2_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02", "B03", "B04", "dataMask"] }],
    output: { bands: 4 },
  };
}
function evaluatePixel(s) {
  // ships on open water reflect almost nothing, so lift the shadows with a
  // gamma curve (not just linear gain, which would blow out bright coast);
  // clamp keeps land/quays from clipping to pure white
  var gamma = function (v) { return Math.min(1.0, Math.pow(v * 3.2, 0.65)); };
  return [gamma(s.B04), gamma(s.B03), gamma(s.B02), s.dataMask];
}
"""


async def fetch_s2_truecolor(lat: float, lon: float, around: datetime,
                             window_days: int = 10) -> bytes | None:
    """A cloud-free true-colour PNG of the same 3x3 km chip, from the clearest
    Sentinel-2 pass within +/- window_days of `around`. Returns PNG bytes, or
    None when no usable (in-swath, low-cloud) daylight scene exists - which is
    most of the time in cloudy waters, and that's fine."""
    if not get_settings().sar_detection_enabled:
        return None
    t0 = (around - timedelta(days=window_days)).astimezone(timezone.utc)
    t1 = (around + timedelta(days=window_days)).astimezone(timezone.utc)
    bbox = chip_bbox(lat, lon)
    body = {
        "input": {
            "bounds": {"bbox": bbox, "properties": {
                "crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {"from": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  "to": t1.strftime("%Y-%m-%dT%H:%M:%SZ")},
                    "maxCloudCoverage": 40,
                    "mosaickingOrder": "leastCC",
                },
            }],
        },
        "output": {
            "width": CHIP_PX, "height": CHIP_PX,
            "responses": [{"identifier": "default",
                           "format": {"type": "image/png"}}],
        },
        "evalscript": S2_EVALSCRIPT,
    }
    async with httpx.AsyncClient(timeout=90) as client:
        try:
            token = await _token(client)
            resp = await client.post(
                PROCESS_URL, json=body,
                headers={"Authorization": f"Bearer {token}", "Accept": "image/png"})
            resp.raise_for_status()
        except Exception:
            logger.info("No usable Sentinel-2 optical chip (%.4f, %.4f)", lat, lon)
            return None
    # reject a mostly-empty tile (out of swath / all cloud): a 4-channel PNG
    # whose alpha is largely zero carries no usable image
    return _reject_empty_png(resp.content)


def _reject_empty_png(png: bytes) -> bytes | None:
    """Return the PNG only if it contains real imagery (enough non-transparent,
    non-black pixels); else None. Stdlib zlib decode of the alpha channel."""
    import struct
    import zlib

    try:
        pos, w, h, idat = 8, None, None, b""
        while pos < len(png):
            ln = struct.unpack(">I", png[pos:pos + 4])[0]
            tag = png[pos + 4:pos + 8]
            if tag == b"IHDR":
                w, h, bit, col = (*struct.unpack(">II", png[pos + 8:pos + 16]),
                                  png[pos + 16], png[pos + 17])
            elif tag == b"IDAT":
                idat += png[pos + 8:pos + 8 + ln]
            pos += 12 + ln
        if not w or col != 6:  # need RGBA
            return png
        raw = zlib.decompress(idat)
        stride = w * 4 + 1
        opaque = 0
        for y in range(0, h, 8):  # sample every 8th row - cheap
            row = raw[y * stride + 1: (y + 1) * stride]
            opaque += sum(1 for x in range(0, len(row), 4 * 8) if row[x + 3:x + 4] and row[x + 3] > 10)
        return png if opaque > (h // 8) * (w // 8) * 0.2 else None
    except Exception:
        return png
