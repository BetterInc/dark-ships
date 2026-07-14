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
