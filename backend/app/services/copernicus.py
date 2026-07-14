"""Copernicus Data Space overpass matcher.

Searches Sentinel-1 (SAR, works through clouds and at night) and Sentinel-2
(optical) acquisitions that intersect the drift area of an AIS gap. Catalogue
search works without authentication. This module only matches scenes; the
automated ship detection on those scenes lives in jobs/sar_detector.py
(services/sentinelhub.py + services/sardetect.py), and the quicklook +
Copernicus Browser link keep human verification one click away.
"""

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

CATALOGUE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

# (collection, extra product filter, source label)
COLLECTIONS = [
    ("SENTINEL-1", "contains(Name,'GRD')", "sentinel-1"),
    ("SENTINEL-2", "contains(Name,'MSIL1C')", "sentinel-2"),
]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def browser_url(lat: float, lon: float, acquired_at: datetime) -> str:
    # zoom 15 ≈ ship-scale: opens tight on the vessel so a hull is actually
    # visible, not the 110 km wide scene. TRUE-COLOR for S2, VV for S1 radar.
    day = acquired_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return (
        "https://browser.dataspace.copernicus.eu/"
        f"?zoom=15&lat={lat:.4f}&lng={lon:.4f}"
        f"&fromTime={day}T00%3A00%3A00.000Z&toTime={day}T23%3A59%3A59.999Z"
    )


def quicklook_url(product_id: str) -> str:
    return f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value?$format=quicklook"


async def search_scenes(polygon_wkt: str, t_start: datetime, t_end: datetime,
                        center_lat: float, center_lon: float) -> list[dict]:
    """Find S1+S2 scenes intersecting the polygon within [t_start, t_end]."""
    scenes: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for collection, product_filter, source in COLLECTIONS:
            filt = (
                f"Collection/Name eq '{collection}' and {product_filter} and "
                f"OData.CSC.Intersects(area=geography'SRID=4326;{polygon_wkt}') and "
                f"ContentDate/Start gt {_iso(t_start)} and ContentDate/Start lt {_iso(t_end)}"
            )
            params = {
                "$filter": filt,
                "$orderby": "ContentDate/Start asc",
                "$top": "50",
                "$expand": "Assets",
            }
            try:
                resp = await client.get(CATALOGUE_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()
            except Exception:
                logger.exception("Copernicus search failed (%s)", collection)
                continue

            for product in payload.get("value", []):
                acquired_raw = (product.get("ContentDate") or {}).get("Start")
                if not acquired_raw:
                    continue
                acquired = datetime.fromisoformat(acquired_raw.replace("Z", "+00:00"))
                ql = None
                for asset in product.get("Assets") or []:
                    if asset.get("Type") == "QUICKLOOK" and asset.get("DownloadLink"):
                        ql = asset["DownloadLink"]
                        break
                scenes.append({
                    "source": source,
                    "product_id": product["Id"],
                    "product_name": product.get("Name"),
                    "acquired_at": acquired,
                    "footprint_wkt": product.get("Footprint"),
                    "quicklook_url": ql or quicklook_url(product["Id"]),
                    "browser_url": browser_url(center_lat, center_lon, acquired),
                })
    return scenes
