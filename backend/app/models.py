from datetime import datetime

from fastapi_users_db_sqlalchemy import (
    SQLAlchemyBaseOAuthAccountTable,
    SQLAlchemyBaseUserTable,
)
from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Identity, Index,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OAuthAccount(SQLAlchemyBaseOAuthAccountTable[int], Base):
    """Google (and future OAuth providers) linkage for a user. Integer PK to
    match the rest of the schema."""

    __tablename__ = "oauth_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    @declared_attr
    def user_id(cls) -> Mapped[int]:
        return mapped_column(Integer, ForeignKey("users.id", ondelete="cascade"), nullable=False)


class User(SQLAlchemyBaseUserTable[int], Base):
    """Application account. Fields email (unique), hashed_password, is_active,
    is_superuser, is_verified come from SQLAlchemyBaseUserTable."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # user | partner | admin. Kept in sync with is_superuser (admin <=> True),
    # which remains the flag fastapi-users' own routers gate on.
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user",
                                      server_default="user")
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        "OAuthAccount", lazy="joined"
    )


class UserVessel(Base):
    """A single vessel on ONE user's private watchlist. Served from our own
    recorded position history (we record every ship at 1/min), so following a
    ship no longer needs a provider slot. Free tier is capped in config
    (free_follow_limit). Shares Base so init_db's create_all builds the table."""

    __tablename__ = "user_vessels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="cascade"), index=True, nullable=False)
    mmsi: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "mmsi", name="uq_user_vessels_user_mmsi"),
    )


class Vessel(Base):
    __tablename__ = "vessels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mmsi: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    imo: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(128))
    flag: Mapped[str | None] = mapped_column(String(64))
    # shadow_fleet | narco | iuu_fishing | sabotage | smuggling | other
    category: Mapped[str] = mapped_column(String(32), default="other")
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # pinned = user-controlled, the engine never touches it; auto_added = placed
    # by the behaviour engine. The watchlist itself is unlimited (world feed
    # tracks every entry); `followed` marks the max-50 ships that also get the
    # dedicated AISstream MMSI-filter connection.
    pinned: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_added: Mapped[bool] = mapped_column(Boolean, default=False)
    followed: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Position(Base):
    # Monthly RANGE-partitioned on `ts` (see jobs/partitions.py). Retention is a
    # DROP of whole month-partitions (instant, no DELETE churn); partitions older
    # than the hot window are first exported to Parquet on S3/R2 (services/cold.py)
    # so long history stays cheap and queryable. Postgres requires the partition
    # key (ts) to be part of the primary key, hence the composite (id, ts).
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    mmsi: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    sog: Mapped[float | None] = mapped_column(Float)
    cog: Mapped[float | None] = mapped_column(Float)
    heading: Mapped[float | None] = mapped_column(Float)
    nav_status: Mapped[int | None] = mapped_column(Integer)
    ship_name: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16), default="region")  # watchlist | region

    __table_args__ = (
        Index("ix_positions_mmsi_ts", "mmsi", "ts", postgresql_using="btree"),
        {"postgresql_partition_by": "RANGE (ts)"},
    )


class LatestPosition(Base):
    """One row per ship: its most recent position, upserted by the ingester on
    every flush. 'Where is everything right now' reads ~30k rows here instead
    of DISTINCT-ON scanning the ever-growing positions history (seconds today,
    worse every day as retention builds toward 90 days)."""

    __tablename__ = "latest_positions"

    mmsi: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    sog: Mapped[float | None] = mapped_column(Float)
    cog: Mapped[float | None] = mapped_column(Float)
    heading: Mapped[float | None] = mapped_column(Float)
    nav_status: Mapped[int | None] = mapped_column(Integer)
    ship_name: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16), default="region")


class AisGap(Base):
    __tablename__ = "ais_gaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mmsi: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    gap_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    gap_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_lat: Mapped[float] = mapped_column(Float, nullable=False)
    last_lon: Mapped[float] = mapped_column(Float, nullable=False)
    last_sog: Mapped[float | None] = mapped_column(Float)
    first_lat_after: Mapped[float | None] = mapped_column(Float)
    first_lon_after: Mapped[float | None] = mapped_column(Float)
    distance_nm: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | closed
    sar_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VesselRegistry(Base):
    """Every vessel ever seen in the feed: our own MMSI <-> IMO <-> name bridge,
    built from AIS ShipStaticData messages. Risk lists identify ships by IMO;
    position reports only carry MMSI - this table connects the two."""

    __tablename__ = "vessel_registry"

    mmsi: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    imo: Mapped[str | None] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    callsign: Mapped[str | None] = mapped_column(String(16))
    ship_type: Mapped[int | None] = mapped_column(Integer)
    destination: Mapped[str | None] = mapped_column(String(64))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_lat: Mapped[float | None] = mapped_column(Float)
    first_lon: Mapped[float | None] = mapped_column(Float)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    static_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last valid MaximumStaticDraught (metres) seen in ShipStaticData. A tanker
    # whose draught swings laden<->ballast offshore has transferred cargo at sea.
    draught: Mapped[float | None] = mapped_column(Float)


class DraughtChange(Base):
    """A vessel's reported MaximumStaticDraught changed significantly between two
    valid ShipStaticData broadcasts. Recorded here at detection time (statics
    carry no position); the behaviour rule resolves the location from the
    positions table and applies the STS guardrails."""

    __tablename__ = "draught_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mmsi: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    old_draught: Mapped[float] = mapped_column(Float, nullable=False)
    new_draught: Mapped[float] = mapped_column(Float, nullable=False)


class IdentityChange(Base):
    """A vessel transmitting a different name/IMO/callsign than before -
    the Arconian trick (new identity during an AIS blackout)."""

    __tablename__ = "identity_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mmsi: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    field: Mapped[str] = mapped_column(String(16))  # name | imo | callsign
    old_value: Mapped[str | None] = mapped_column(String(128))
    new_value: Mapped[str | None] = mapped_column(String(128))
    scored: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class RiskEvent(Base):
    """One fired behaviour rule for one vessel; the suggestion score is the
    sum of a vessel's recent events."""

    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mmsi: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    rule: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    details: Mapped[str | None] = mapped_column(Text)  # JSON blob with evidence


class RiskListEntry(Base):
    """Imported external risk list (OFAC sanctions, IUU blacklists, ...)."""

    __tablename__ = "risk_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)  # ofac | iuu | ...
    imo: Mapped[str | None] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(128), index=True)
    callsign: Mapped[str | None] = mapped_column(String(16))
    flag: Mapped[str | None] = mapped_column(String(64))
    program: Mapped[str | None] = mapped_column(String(128))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngestHeartbeat(Base):
    """One row per minute while the ingestor is actually receiving. Gap rules
    only fire when we were provably listening - otherwise a `docker compose
    down` would make every ship look like it went dark."""

    __tablename__ = "ingest_heartbeats"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)


class PositionCheck(Base):
    """A satellite scene that captured a vessel's CLAIMED position at claim
    time. If the ship says it's there, a hull should be visible there -
    one-click human verification via the Copernicus Browser link."""

    __tablename__ = "position_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mmsi: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    claimed_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_lat: Mapped[float] = mapped_column(Float, nullable=False)
    claimed_lon: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16))  # sentinel-1 | sentinel-2
    product_id: Mapped[str] = mapped_column(String(128))
    product_name: Mapped[str | None] = mapped_column(String(256))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delta_minutes: Mapped[float] = mapped_column(Float)  # |claim time - capture time|
    quicklook_url: Mapped[str | None] = mapped_column(Text)
    browser_url: Mapped[str | None] = mapped_column(Text)
    # Automated SAR detection (sentinel-1 checks only). NULL analyzed_at =
    # not analyzed yet; hull_detected NULL after analysis = chip unusable
    # (mostly no-data). Never guessed: values come from measured backscatter.
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hull_detected: Mapped[bool | None] = mapped_column(Boolean)
    target_count: Mapped[int | None] = mapped_column(Integer)   # bright targets in chip
    nearest_offset_m: Mapped[float | None] = mapped_column(Float)  # closest target vs claim
    # target also bright on a pass weeks earlier -> fixed structure or a very
    # long-anchored ship (NULL = no usable reference pass)
    persistent_target: Mapped[bool | None] = mapped_column(Boolean)
    chip_key: Mapped[str | None] = mapped_column(String(256))   # S3 object key of the PNG

    __table_args__ = (
        Index("ix_position_checks_mmsi_product", "mmsi", "product_id", unique=True),
    )


class OilSlick(Base):
    """Oil slick detections from SkyTruth Cerulean (free Sentinel-1 analysis).
    A slick at a rendezvous location is near-conclusive STS evidence."""

    __tablename__ = "oil_slicks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Cerulean id
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    area_m2: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)


class SarMatch(Base):
    __tablename__ = "sar_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gap_id: Mapped[int] = mapped_column(ForeignKey("ais_gaps.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # sentinel-1 | sentinel-2 | gfw
    product_id: Mapped[str] = mapped_column(String(128), index=True)
    product_name: Mapped[str | None] = mapped_column(String(256))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    footprint_wkt: Mapped[str | None] = mapped_column(Text)
    quicklook_url: Mapped[str | None] = mapped_column(Text)
    browser_url: Mapped[str | None] = mapped_column(Text)
