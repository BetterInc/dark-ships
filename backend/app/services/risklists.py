"""External risk-list importers.

Currently implemented:
- OFAC SDN (US sanctions) - ~1,500 vessel entries, IMO extractable from remarks.

Designed to be pluggable; candidates for later importers: EU shadow-fleet
designations (Reg. 833/2014 Annex XLII), the Combined IUU Vessel List
(iuu-vessels.org, non-commercial), UANI and KSE tracker exports.
"""

import csv
import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import delete, func, select

from ..db import SessionLocal
from ..models import RiskListEntry

logger = logging.getLogger(__name__)

OFAC_URLS = [
    "https://sanctionslistservice.ofac.treas.gov/api/download/sdn.csv",
    "https://www.treasury.gov/ofac/downloads/sdn.csv",
]
IMO_RE = re.compile(r"IMO\s+(\d{7})")

# The FCDO UK Sanctions List is the authoritative register for ALL UK
# designations incl. specified ships (~657 vessels). The older OFSI ConList
# only held ~81 DPRK ships and is frozen. Open Government Licence (free, incl.
# commercial). Line 1 is "Report Date:", so the real header is line 2.
UK_FCDO_URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv"
UK_IMO_RE = re.compile(r"(\d{7})")


def valid_imo(imo: str) -> bool:
    """IMO check digit: sum(d1..d6 x 7..2) mod 10 == d7."""
    if not imo or len(imo) != 7 or not imo.isdigit():
        return False
    return sum(int(d) * w for d, w in zip(imo[:6], range(7, 1, -1))) % 10 == int(imo[6])


async def import_uk() -> int:
    """UK FCDO Sanctions List (the authoritative register incl. specified
    ships, ~657 vessels). Open Government Licence - free, commercial ok.
    Header is on line 2; vessel rows carry an 'IMO number' column."""
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        resp = await client.get(UK_FCDO_URL)
        resp.raise_for_status()
        text = resp.text

    reader = csv.reader(io.StringIO(text))
    next(reader, None)          # line 1: "Report Date: ..."
    header = next(reader, None)  # line 2: real header
    if not header:
        return 0
    cols = {h.strip(): i for i, h in enumerate(header)}
    imo_c = cols.get("IMO number")
    name_c = cols.get("Name 6")
    regime_c = cols.get("Regime Name")
    flag_c = cols.get("Flag")
    ntype_c = cols.get("Name Type")
    if imo_c is None:
        logger.warning("UK FCDO CSV: no 'IMO number' column - skipping")
        return 0

    now = datetime.now(timezone.utc)
    best: dict[str, dict] = {}  # imo -> row (prefer Primary Name)
    for row in reader:
        if imo_c >= len(row):
            continue
        m = UK_IMO_RE.search(row[imo_c] or "")
        if not m or not valid_imo(m.group(1)):
            continue
        imo = m.group(1)
        is_primary = ntype_c is not None and ntype_c < len(row) and \
            (row[ntype_c] or "").strip().lower().startswith("primary")
        if imo in best and not is_primary:
            continue
        best[imo] = {
            "name": (row[name_c].strip() if name_c is not None and name_c < len(row) else "")[:128] or None,
            "flag": (row[flag_c].strip() if flag_c is not None and flag_c < len(row) else "")[:64] or None,
            "program": (row[regime_c].strip() if regime_c is not None and regime_c < len(row) else "UK")[:128] or "UK",
        }

    entries = [RiskListEntry(source="uk", imo=imo, name=v["name"], flag=v["flag"],
                             program=v["program"], imported_at=now)
               for imo, v in best.items()]
    async with SessionLocal() as session:
        await session.execute(delete(RiskListEntry).where(RiskListEntry.source == "uk"))
        session.add_all(entries)
        await session.commit()
    logger.info("UK import: %d vessels", len(entries))
    return len(entries)


def _field(value: str) -> str | None:
    value = (value or "").strip()
    return None if value in ("", "-0-") else value


async def import_ofac() -> int:
    """Replace all OFAC entries with a fresh download. Returns vessel count."""
    text = None
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        for url in OFAC_URLS:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                text = resp.text
                break
            except Exception as exc:
                logger.warning("OFAC download failed from %s: %s", url, exc)
    if text is None:
        raise RuntimeError("All OFAC download URLs failed")

    now = datetime.now(timezone.utc)
    entries = []
    # sdn.csv: ent_num, name, type, program, title, call_sign, vess_type,
    #          tonnage, grt, vess_flag, vess_owner, remarks
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 12 or _field(row[2]) != "vessel":
            continue
        remarks = row[11] or ""
        imo_match = IMO_RE.search(remarks)
        entries.append(RiskListEntry(
            source="ofac",
            imo=imo_match.group(1) if imo_match else None,
            name=_field(row[1]),
            callsign=_field(row[5]),
            flag=_field(row[9]),
            program=_field(row[3]),
            imported_at=now,
        ))

    async with SessionLocal() as session:
        await session.execute(delete(RiskListEntry).where(RiskListEntry.source == "ofac"))
        session.add_all(entries)
        await session.commit()
    with_imo = sum(1 for e in entries if e.imo)
    logger.info("OFAC import: %d vessels (%d with IMO)", len(entries), with_imo)
    return len(entries)


OS_URL = "https://data.opensanctions.org/datasets/latest/{slug}/entities.ftm.json"

# OpenSanctions vessel datasets we ingest (all CC BY-NC 4.0 - non-commercial
# only; a commercial deployment needs an OpenSanctions data license).
#   source label  -> (dataset slug, program tag)
OS_VESSEL_DATASETS = {
    "gur": ("ua_war_sanctions", "GUR-shadow-fleet"),
    "parismou": ("paris_mou_banned", "Paris-MoU-port-ban"),
    "tokyomou": ("tokyo_mou_detention", "Tokyo-MoU-detention"),
    "canada": ("ca_dfatd_sema_sanctions", "Canada-SEMA"),
    "australia": ("au_dfat_sanctions", "Australia-DFAT"),
    "switzerland": ("ch_seco_sanctions", "Switzerland-SECO"),
    "un1718": ("un_1718_vessels", "UN-1718-DPRK"),
    "blackseamou": ("black_sea_mou_detention", "Black-Sea-MoU-detention"),
    "abujamou": ("abuja_mou_detention", "Abuja-MoU-detention"),
}


async def import_opensanctions_vessels(source: str, slug: str, program: str) -> int:
    """Generic importer for any OpenSanctions dataset in FollowTheMoney JSON.
    Keeps only schema=Vessel entities with a valid 7-digit IMO."""
    import json as json_mod

    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        resp = await client.get(OS_URL.format(slug=slug))
        resp.raise_for_status()
        text = resp.text

    now = datetime.now(timezone.utc)
    entries = []
    for line in text.splitlines():
        if '"Vessel"' not in line:
            continue
        try:
            ent = json_mod.loads(line)
        except json_mod.JSONDecodeError:
            continue
        if ent.get("schema") != "Vessel":
            continue
        props = ent.get("properties", {})
        raw_imo = next(iter(props.get("imoNumber", [])), "") or ""
        digits = "".join(c for c in str(raw_imo) if c.isdigit())  # strips 'IMO' prefix
        if len(digits) != 7:
            continue
        name = next(iter(props.get("name", [])), None)
        flag = next(iter(props.get("flag", [])), None) or \
            next(iter(props.get("pastFlags", [])), None)
        entries.append(RiskListEntry(
            source=source, imo=digits, name=(name or "")[:128] or None,
            callsign=next(iter(props.get("callSign", [])), None),
            flag=(flag or "")[:64] or None, program=program, imported_at=now,
        ))

    async with SessionLocal() as session:
        await session.execute(delete(RiskListEntry).where(RiskListEntry.source == source))
        session.add_all(entries)
        await session.commit()
    logger.info("%s import: %d vessels", source, len(entries))
    return len(entries)


# EU Annex XLII port-ban shadow-fleet list, published as CSV by the Danish
# Maritime Authority. The Media filename is date-versioned, so scrape the
# landing page for the current link rather than hardcoding one snapshot.
EU_LANDING = ("https://www.dma.dk/growth-and-framework-conditions/maritime-sanctions/"
              "sanctions-against-russia-and-belarus/eu-vessel-designations")
EU_CSV_FALLBACK = ("https://www.dma.dk/Media/639126203301405214/"
                   "ImportversionListOfEUDesignatedVessels240426.csv")


async def import_eu() -> int:
    """EU Regulation 833/2014 Annex XLII designated (port-access-banned)
    vessels - the EU shadow-fleet list. Public Danish-authority data."""
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        url = EU_CSV_FALLBACK
        try:
            page = (await client.get(EU_LANDING)).text
            m = re.search(r'href="([^"]*ListOfEUDesignatedVessels[^"]*\.csv)"', page)
            if m:
                url = m.group(1)
                if url.startswith("/"):
                    url = "https://www.dma.dk" + url
        except Exception:
            logger.warning("EU landing-page scrape failed; using fallback CSV URL")
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text

    now = datetime.now(timezone.utc)
    entries = []
    reader = csv.reader(io.StringIO(text), delimiter=";")
    next(reader, None)  # header
    for row in reader:
        if len(row) < 2:
            continue
        name = row[0].strip()
        imo = "".join(c for c in row[1] if c.isdigit())
        if len(imo) != 7:
            continue
        entries.append(RiskListEntry(
            source="eu", imo=imo, name=name[:128] or None,
            program="EU-Annex-XLII", imported_at=now,
        ))

    async with SessionLocal() as session:
        await session.execute(delete(RiskListEntry).where(RiskListEntry.source == "eu"))
        session.add_all(entries)
        await session.commit()
    logger.info("EU import: %d vessels", len(entries))
    return len(entries)


import html as _html

UANI_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "uani_ghost_armada.csv"
UANI_PAGE = "https://www.unitedagainstnucleariran.com/blog/stop-hop-ii-ghost-armada-grows"
WAYBACK_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0 Safari/537.36"}


def _load_uani_seed() -> dict[str, dict]:
    vessels: dict[str, dict] = {}
    if not UANI_SEED_PATH.exists():
        return vessels
    with UANI_SEED_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            imo = "".join(c for c in (row.get("imo") or "") if c.isdigit())
            if valid_imo(imo):
                vessels[imo] = {"name": (row.get("name") or "").strip(),
                                "flag": (row.get("flag") or "").strip()}
    return vessels


async def _uani_from_wayback() -> dict[str, dict]:
    """Best-effort: pull the latest Wayback snapshot of the Ghost Armada page
    and parse its vessel table. UANI's live site is Cloudflare-gated (direct
    fetch returns 403), so the Internet Archive is the only automatable route.
    Returns {} on any failure - the caller keeps the seed as the floor."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=WAYBACK_UA) as client:
        cdx = ("http://web.archive.org/cdx/search/cdx"
               f"?url={UANI_PAGE}&output=json&limit=3&sort=-timestamp&filter=statuscode:200")
        rows = (await client.get(cdx)).json()
        if len(rows) < 2:
            return {}
        ts = rows[1][1]
        doc = (await client.get(f"http://web.archive.org/web/{ts}id_/{UANI_PAGE}")).text

    cells = [_html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
             for c in re.findall(r"<td[^>]*>(.*?)</td>", doc, re.S | re.I)]
    vessels: dict[str, dict] = {}
    for i, c in enumerate(cells):
        digits = "".join(ch for ch in c if ch.isdigit())
        if valid_imo(digits) and i + 1 < len(cells):
            name = re.split(r"\(EX", cells[i + 1], flags=re.I)[0].strip()
            flag = cells[i + 3] if i + 3 < len(cells) else ""
            vessels[digits] = {"name": name[:120], "flag": flag[:60]}
    return vessels


def _write_uani_seed(vessels: dict[str, dict]) -> None:
    with UANI_SEED_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "imo", "flag"])
        w.writeheader()
        for imo, v in sorted(vessels.items(), key=lambda kv: kv[1]["name"]):
            w.writerow({"name": v["name"], "imo": imo, "flag": v["flag"]})


async def import_uani() -> int:
    """UANI Ghost Armada (Iran shadow fleet). Bundled seed CSV is the floor so
    it always auto-loads; each run also tries a Wayback refresh and MERGES any
    new vessels into the seed (never regresses). UANI's live site is
    Cloudflare-gated, so Wayback is the only automatable path."""
    vessels = _load_uani_seed()
    seed_count = len(vessels)
    try:
        fresh = await _uani_from_wayback()
        added = 0
        for imo, v in fresh.items():
            if imo not in vessels:
                vessels[imo] = v
                added += 1
            elif not vessels[imo]["name"] and v["name"]:
                vessels[imo] = v
        if fresh and added:
            _write_uani_seed(vessels)
            logger.info("UANI Wayback refresh: +%d new vessels (seed now %d)", added, len(vessels))
        elif fresh:
            logger.info("UANI Wayback refresh: no new vessels (%d)", len(vessels))
    except Exception as exc:
        logger.warning("UANI Wayback refresh failed (%s) - using seed of %d", exc, seed_count)

    now = datetime.now(timezone.utc)
    entries = [RiskListEntry(source="uani", imo=imo, name=(v["name"] or None),
                             flag=(v["flag"] or None), program="UANI-Ghost-Armada",
                             imported_at=now)
               for imo, v in vessels.items()]
    async with SessionLocal() as session:
        await session.execute(delete(RiskListEntry).where(RiskListEntry.source == "uani"))
        session.add_all(entries)
        await session.commit()
    logger.info("UANI import: %d vessels", len(entries))
    return len(entries)


IUU_SEARCH_URL = "https://iuu-vessels.org/Home/Search"


def _strip_html(s: str) -> str:
    import html as _h
    return _h.unescape(re.sub(r"<[^>]+>", "", s)).strip()


async def import_iuu() -> int:
    """Combined IUU (illegal fishing) vessel list - the aggregate of all RFMO
    blacklists (CCAMLR, ICCAT, IOTC, WCPFC, ...) from TMT's FACT database,
    served as server-rendered HTML. ~213 listed vessels, ~66 with an IMO (many
    IUU boats legitimately lack one). Non-commercial / due-diligence use."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0"}) as client:
        resp = await client.get(IUU_SEARCH_URL)
        resp.raise_for_status()
        doc = resp.text

    body = re.search(r"<tbody>(.*?)</tbody>", doc, re.S)
    if not body:
        logger.warning("IUU list: no table found - page layout changed?")
        return 0
    now = datetime.now(timezone.utc)
    seen: dict[str, RiskListEntry] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S):
        cells = [_strip_html(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 2:
            continue
        imo = "".join(c for c in cells[1] if c.isdigit())
        if not valid_imo(imo):
            continue
        name = cells[0].split(",")[0].strip()[:128]
        seen[imo] = RiskListEntry(source="iuu", imo=imo, name=name or None,
                                  program="Combined-IUU-RFMO", imported_at=now)

    async with SessionLocal() as session:
        await session.execute(delete(RiskListEntry).where(RiskListEntry.source == "iuu"))
        session.add_all(seen.values())
        await session.commit()
    logger.info("IUU import: %d vessels with IMO", len(seen))
    return len(seen)


# EU "Union IUU vessel list", published as an EU Implementing Regulation on
# EUR-Lex. Official EU legislation, so free for any use incl. commercial - the
# commercial-clean counterpart to the iuu-vessels.org list. The regulation
# number changes ~quarterly, so discover the current one from the Commission's
# illegal-fishing page rather than hardcoding it.
EU_IUU_LANDING = ("https://oceans-and-fisheries.ec.europa.eu/international/"
                  "illegal-fishing_en")
# Fallback CELEX if discovery fails (Implementing Regulation (EU) 2025/1853).
EU_IUU_CELEX_FALLBACK = "32025R1853"
# The EUR-Lex web UI sits behind an AWS WAF JS challenge that blocks plain
# HTTP clients, but the Publications Office "Cellar" repository serves the same
# document by CELEX as XHTML with no challenge - that is the machine-readable
# route we use.
EU_IUU_CELLAR = "http://publications.europa.eu/resource/celex/{celex}"


def _eu_iuu_celex(landing_html: str) -> str | None:
    """Find the current 'Union IUU vessel list' regulation and return its CELEX.
    The Commission page links the list either as an ELI (reg_impl/YEAR/NUM) or a
    CELEX uri; both are converted to a CELEX like '32025R1853'."""
    # the anchor whose visible text is the IUU vessel list
    m = re.search(r'href="([^"]+)"[^>]*>\s*EU IUU vessel list', landing_html, re.I)
    href = m.group(1) if m else landing_html
    eli = re.search(r"reg_impl/(\d{4})/(\d+)", href)
    if eli:
        year, num = eli.group(1), int(eli.group(2))
        return f"3{year}R{num:04d}"
    celex = re.search(r"CELEX[:%3A]+(\d{4}R\d+)", href, re.I)
    if celex:
        return celex.group(1)
    return None


async def import_eu_iuu() -> int:
    """EU Union IUU vessel list - the RFMO illegal-fishing blacklists as adopted
    into EU law by an Implementing Regulation. Official EU legislation (free for
    any use, incl. commercial), so this is the commercial-clean counterpart to
    the non-commercial iuu-vessels.org 'iuu' source. Keeps only vessels with a
    valid IMO (many IUU boats legitimately lack one), deduped by IMO."""
    ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers=ua) as client:
        celex = EU_IUU_CELEX_FALLBACK
        try:
            landing = (await client.get(EU_IUU_LANDING)).text
            found = _eu_iuu_celex(landing)
            if found:
                celex = found
            else:
                logger.warning("EU IUU: could not discover current regulation - "
                               "using fallback CELEX %s", EU_IUU_CELEX_FALLBACK)
        except Exception:
            logger.warning("EU IUU landing-page scrape failed - using fallback CELEX %s",
                           EU_IUU_CELEX_FALLBACK)
        resp = await client.get(EU_IUU_CELLAR.format(celex=celex),
                                headers={"Accept": "application/xhtml+xml",
                                         "Accept-Language": "eng"})
        resp.raise_for_status()
        doc = resp.text

    # ANNEX table columns: IMO/RFMO reference, vessel name, flag, listing RFMOs.
    # The IMO ship-identification number (when the boat has one) is the first
    # '/'-separated token of column 0; the rest are RFMO record numbers.
    now = datetime.now(timezone.utc)
    seen: dict[str, RiskListEntry] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", doc, re.S | re.I):
        cells = [_strip_html(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
        if len(cells) < 2:
            continue
        first = re.match(r"(\d+)", cells[0].split("/")[0].strip())
        imo = first.group(1) if first else ""
        if not valid_imo(imo):
            continue
        name = re.split(r"\s*[\(\[]", cells[1])[0].strip()[:128]
        flag = re.split(r"\s*[\(\[]", cells[2])[0].strip() if len(cells) > 2 else ""
        if flag.lower() in ("unknown", "unknow", "stateless", ""):
            flag = None
        seen[imo] = RiskListEntry(source="eu_iuu", imo=imo, name=name or None,
                                  flag=(flag or None), program="EU-IUU", imported_at=now)

    if not seen:
        logger.warning("EU IUU list: no vessels parsed (CELEX %s) - layout changed?", celex)
        return 0
    async with SessionLocal() as session:
        await session.execute(delete(RiskListEntry).where(RiskListEntry.source == "eu_iuu"))
        session.add_all(seen.values())
        await session.commit()
    logger.info("EU IUU import: %d vessels with IMO (CELEX %s)", len(seen), celex)
    return len(seen)


async def import_all(force: bool = False) -> dict[str, int]:
    """Run all importers. Without force, skip if data is under a day old.

    Freshness is judged per source, not on the single newest row: a run
    killed mid-import (pod roll) would otherwise leave one fresh source
    making the whole partial dataset look up to date for 20h.
    """
    expected = {"ofac", *OS_VESSEL_DATASETS, "eu", "uk", "uani", "iuu", "eu_iuu"}
    async with SessionLocal() as session:
        per_source = dict((await session.execute(
            select(RiskListEntry.source, func.max(RiskListEntry.imported_at))
            .group_by(RiskListEntry.source)
        )).all())
    if per_source.keys() >= expected and not force:
        oldest = min(per_source[s] for s in expected)
        age_hours = (datetime.now(timezone.utc) - oldest).total_seconds() / 3600
        if age_hours < 20:
            logger.info("Risk lists are %.1fh old - skipping import", age_hours)
            return {}
    results = {}
    try:
        results["ofac"] = await import_ofac()
    except Exception:
        logger.exception("OFAC import failed")
    for source, (slug, program) in OS_VESSEL_DATASETS.items():
        try:
            results[source] = await import_opensanctions_vessels(source, slug, program)
        except Exception:
            logger.exception("%s import failed", source)
    for name, fn in (("eu", import_eu), ("uk", import_uk), ("uani", import_uani),
                     ("iuu", import_iuu), ("eu_iuu", import_eu_iuu)):
        try:
            results[name] = await fn()
        except Exception:
            logger.exception("%s import failed", name)
    return results
