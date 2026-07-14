from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VesselCreate(BaseModel):
    mmsi: int = Field(ge=100_000_000, le=999_999_999)
    imo: str | None = None
    name: str | None = None
    flag: str | None = None
    category: str = "other"
    notes: str | None = None
    active: bool = True


class VesselUpdate(BaseModel):
    imo: str | None = None
    name: str | None = None
    flag: str | None = None
    category: str | None = None
    notes: str | None = None
    active: bool | None = None
    followed: bool | None = None


class VesselOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mmsi: int
    imo: str | None
    name: str | None
    flag: str | None
    category: str
    notes: str | None
    active: bool
    pinned: bool
    auto_added: bool
    followed: bool
    risk_score: float
    created_at: datetime


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mmsi: int
    ts: datetime
    lat: float
    lon: float
    sog: float | None
    cog: float | None
    heading: float | None
    nav_status: int | None
    ship_name: str | None
    source: str


class LatestPositionOut(PositionOut):
    category: str | None = None  # watchlist category, None for regional traffic
    vessel_name: str | None = None
    risk_score: float | None = None
    patterns: list[str] = []  # human-readable detected patterns
    notes: str | None = None   # why it's on the watchlist (esp. manual entries)
    ship_type: str | None = None  # tanker / cargo / fishing / ...


class SarMatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    gap_id: int
    source: str
    product_id: str
    product_name: str | None
    acquired_at: datetime
    footprint_wkt: str | None
    quicklook_url: str | None
    browser_url: str | None


class GapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mmsi: int
    gap_start: datetime
    gap_end: datetime | None
    last_lat: float
    last_lon: float
    last_sog: float | None
    first_lat_after: float | None
    first_lon_after: float | None
    distance_nm: float | None
    status: str
    sar_checked_at: datetime | None


class GapWithVesselOut(GapOut):
    vessel_name: str | None = None
    category: str | None = None
    sar_matches: list[SarMatchOut] = []


class PositionCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mmsi: int
    claimed_ts: datetime
    claimed_lat: float
    claimed_lon: float
    source: str
    product_name: str | None
    acquired_at: datetime
    delta_minutes: float
    quicklook_url: str | None
    browser_url: str | None
    # automated SAR detection; all None until the sar_detector job has run
    # (or forever, for sentinel-2 checks and unjudgeable chips)
    hull_detected: bool | None = None
    target_count: int | None = None
    nearest_offset_m: float | None = None
    chip_key: str | None = None  # set = chip PNG available at /api/position-checks/{id}/chip


class RegionOut(BaseModel):
    name: str
    bbox: list[list[float]]
    kind: str = "transit"
