import math

EARTH_RADIUS_NM = 3440.065
KM_PER_NM = 1.852


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return haversine_nm(lat1, lon1, lat2, lon2) * KM_PER_NM


def project_position(lat: float, lon: float, course_deg: float, distance_nm: float) -> tuple[float, float]:
    """Dead-reckon: where a ship ends up from (lat, lon) steering course_deg
    for distance_nm (flat-earth approximation, fine for a few hundred nm)."""
    dlat = distance_nm / 60.0 * math.cos(math.radians(course_deg))
    dlon = distance_nm / (60.0 * max(math.cos(math.radians(lat)), 0.01)) * math.sin(math.radians(course_deg))
    return lat + dlat, lon + dlon


def interior_distance_km(lat: float, lon: float, bboxes: list[list[list[float]]]) -> float:
    """How far a point sits inside any region bbox, measured to the nearest
    edge (km). 0 if outside all boxes. AIS receivers only cover what they
    cover - a ship falling silent near a box edge probably just sailed out,
    so behaviour rules require a healthy interior distance."""
    best = 0.0
    for (lat_min, lon_min), (lat_max, lon_max) in bboxes:
        if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
            continue
        km_per_deg_lon = 111.32 * max(math.cos(math.radians(lat)), 0.01)
        d = min(
            (lat - lat_min) * 111.32,
            (lat_max - lat) * 111.32,
            (lon - lon_min) * km_per_deg_lon,
            (lon_max - lon) * km_per_deg_lon,
        )
        best = max(best, d)
    return best


def search_polygon_wkt(lat: float, lon: float, radius_nm: float) -> str:
    """Square search area around a point as a WKT polygon (EPSG:4326)."""
    dlat = radius_nm / 60.0  # 1 degree of latitude ≈ 60 nm
    coslat = max(math.cos(math.radians(lat)), 0.01)
    dlon = radius_nm / (60.0 * coslat)
    south, north = max(lat - dlat, -89.9), min(lat + dlat, 89.9)
    west, east = lon - dlon, lon + dlon
    ring = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    coords = ",".join(f"{x:.4f} {y:.4f}" for x, y in ring)
    return f"POLYGON(({coords}))"
