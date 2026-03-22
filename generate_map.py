import math
import json
import re
from io import BytesIO

import folium
import matplotlib.pyplot as plt
import requests
from branca.element import Element
from PIL import Image
from pyproj import Transformer

from settings import (
    ENABLE_TROLLEYBUS,
    RAPIDRIDE_STOP_EXCLUDE_NEAR_RAIL_OR_STREETCAR_METERS,
    RAPIDRIDE_STOP_MIN_DISTANCE_METERS,
    TROLLEYBUS_STOP_MIN_DISTANCE_METERS,
)


PRIORITY_ORDER = {
    "trolleybus": -1,
    "rapidride": 0,
    "streetcar": 1,
    "light_rail": 2,
}

STOP_MEMBER_ROLES = {
    "stop",
    "platform",
    "station",
    "stop_entry_only",
    "stop_exit_only",
    "platform_entry_only",
    "platform_exit_only",
    "",
}

EXCLUDED_RELATION_NAME_KEYWORDS = {
    "sea underground",
    "satellite transit system",
    "tacoma link",
    "hilltop tacoma link",
    "t line",
}

EXCLUDED_LIGHT_RAIL_STOP_LINK_TYPES = {
    2,  # Tacoma Link
    9,  # Hilltop Tacoma Link Extension
}

LIGHT_RAIL_LINES_URL = (
    "https://gismaps.kingcounty.gov/imagery/rest/services/Transit/"
    "TransitGrievanceTracking/MapServer/15/query"
)
LIGHT_RAIL_STOPS_URL = (
    "https://gismaps.kingcounty.gov/imagery/rest/services/Transit/"
    "TransitGrievanceTracking/MapServer/14/query"
)
RAPIDRIDE_LINES_URL = (
    "https://gismaps.kingcounty.gov/imagery/rest/services/Transit/"
    "TransitGrievanceTracking/MapServer/16/query"
)
RAPIDRIDE_STOPS_URL = (
    "https://gismaps.kingcounty.gov/imagery/rest/services/Transit/"
    "TransitGrievanceTracking/MapServer/13/query"
)
STREETCAR_LINES_URL = (
    "https://data-seattlecitygis.opendata.arcgis.com/api/download/v1/items/"
    "3f53447de4e049d18ecf82cfc175dc87/geojson?layers=0"
)
STREETCAR_STOPS_URL = (
    "https://data-seattlecitygis.opendata.arcgis.com/api/download/v1/items/"
    "b2b4e354334d4dbe8f7925a6fb7e8ec0/geojson?layers=0"
)
RAPIDRIDE_REFS = {"A", "B", "C", "D", "E", "F", "G", "H"}
TROLLEYBUS_LINE_NUMS = {1, 2, 3, 4, 5, 6, 7, 10, 12, 13, 14, 36, 43, 44, 49, 70}
RAPIDRIDE_NUM_TO_REF = {
    # Current King County GIS schema (A-H)
    671: "A",
    672: "B",
    673: "C",
    674: "D",
    675: "E",
    676: "F",
    677: "G",
    678: "H",
}
RAPIDRIDE_ROUTE_NUMS = tuple(RAPIDRIDE_NUM_TO_REF.keys())
WA_NORTH_FEET_TO_WGS84 = Transformer.from_crs("EPSG:2926", "EPSG:4326", always_xy=True)

LINE_COLORS = {
    "line_1": "#46b97a",
    "line_2": "#3a8fd6",
    "trolleybus": "#b07bd8",
    "rapidride": "#ef6a78",
    "streetcar": "#f6ad55",
}

STOP_COLORS = {
    "line_1": "#008f5a",
    "line_2": "#005b9f",
    "trolleybus": "#7a3fb3",
    "rapidride": "#e31837",
    "streetcar": "#f28c28",
}

RAPIDRIDE_STOP_CLUSTER_DISTANCE_METERS = 20
RAPIDRIDE_DIRECTIONAL_PAIR_DISTANCE_METERS = 150
CARDINAL_DIRECTION_PATTERN = re.compile(r"^(?:N|S|E|W|NE|NW|SE|SW)\b\.?\s*", re.IGNORECASE)
TRAILING_CARDINAL_DIRECTION_PATTERN = re.compile(r"\s+\b(?:N|S|E|W|NE|NW|SE|SW)\b\.?$", re.IGNORECASE)
TRAILING_STREET_TYPE_PATTERN = re.compile(
    r"\s+\b(?:"
    r"ST|STREET|AVE|AVENUE|WAY|RD|ROAD|DR|DRIVE|BLVD|BOULEVARD|"
    r"PL|PLACE|LN|LANE|CT|COURT|TER|TERRACE|PKWY|PARKWAY|HWY|HIGHWAY"
    r")\b\.?$",
    re.IGNORECASE,
)


def to_key(value):
    normalized = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower())
    return normalized.strip("_") or "unknown"


def is_excluded_airport_terminal_train(name):
    normalized_name = (name or "").strip().lower()
    return any(keyword in normalized_name for keyword in EXCLUDED_RELATION_NAME_KEYWORDS)


def is_excluded_light_rail_stop(properties):
    link_type = properties.get("LINK_TYPE")
    if link_type in EXCLUDED_LIGHT_RAIL_STOP_LINK_TYPES:
        return True

    stop_text_fields = [
        properties.get("DESCRIPTIO"),
        properties.get("STATION"),
        properties.get("LINE"),
        properties.get("ROUTE"),
    ]
    if any(is_excluded_airport_terminal_train(value) for value in stop_text_fields):
        return True

    station_name = (properties.get("STATION") or "").strip().lower()
    return station_name.startswith("tacoma ")


def lon_lat_to_tile(lon, lat, zoom):
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x_tile = int((lon + 180.0) / 360.0 * n)
    y_tile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return x_tile, y_tile


def tile_to_lon_lat(x_tile, y_tile, zoom):
    n = 2 ** zoom
    lon = x_tile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y_tile / n)))
    lat = math.degrees(lat_rad)
    return lon, lat


def add_tile_background(ax, min_lon, max_lon, min_lat, max_lat, zoom=11):
    min_x, max_y = lon_lat_to_tile(min_lon, min_lat, zoom)
    max_x, min_y = lon_lat_to_tile(max_lon, max_lat, zoom)

    min_x, max_x = sorted((min_x, max_x))
    min_y, max_y = sorted((min_y, max_y))

    width = (max_x - min_x + 1) * 256
    height = (max_y - min_y + 1) * 256
    composite = Image.new('RGB', (width, height), '#f2f2f2')

    for x_tile in range(min_x, max_x + 1):
        for y_tile in range(min_y, max_y + 1):
            tile_url = f"https://basemaps.cartocdn.com/light_all/{zoom}/{x_tile}/{y_tile}.png"
            try:
                response = requests.get(tile_url, timeout=20)
                response.raise_for_status()
                tile = Image.open(BytesIO(response.content)).convert('RGB')
                composite.paste(tile, ((x_tile - min_x) * 256, (y_tile - min_y) * 256))
            except requests.RequestException:
                continue

    west, north = tile_to_lon_lat(min_x, min_y, zoom)
    east, south = tile_to_lon_lat(max_x + 1, max_y + 1, zoom)
    ax.imshow(composite, extent=[west, east, south, north], zorder=0, alpha=0.95)


def fetch_geojson(url, params=None):
    response = requests.get(url, params=params, timeout=90)
    response.raise_for_status()
    return response.json()


def fetch_all_geojson_features(url, params=None, page_size=1000):
    base_params = dict(params or {})
    all_features = []
    offset = 0

    while True:
        page_params = dict(base_params)
        page_params["resultOffset"] = offset
        page_params["resultRecordCount"] = page_size

        page = fetch_geojson(url, params=page_params)
        features = page.get("features", [])
        if not features:
            break

        all_features.extend(features)
        if len(features) < page_size:
            break

        offset += page_size

    return all_features


def split_line_geometry(geometry):
    geom_type = geometry.get("type")
    if geom_type == "LineString":
        return [geometry.get("coordinates", [])]
    if geom_type == "MultiLineString":
        return geometry.get("coordinates", [])
    return []


def to_lat_lon_pairs(line_coords):
    pairs = []
    for x, y in line_coords:
        if x is None or y is None:
            continue
        lon, lat = normalize_to_wgs84(x, y)
        pairs.append([lat, lon])
    return pairs


def normalize_to_wgs84(x, y):
    if abs(x) <= 180 and abs(y) <= 90:
        return x, y
    lon, lat = WA_NORTH_FEET_TO_WGS84.transform(x, y)
    return lon, lat


def is_line2_description(text):
    normalized = (text or "").strip().lower()
    return "east link" in normalized or "2 line" in normalized or "line 2" in normalized


def is_line2_light_rail_stop(properties):
    return any(
        is_line2_description(value)
        for value in (
            properties.get("STATION"),
            properties.get("NAME"),
            properties.get("SEGMENT"),
        )
    ) or properties.get("LINK_TYPE") == 7


def contains_rapidride_ref(value):
    if value is None:
        return False
    tokens = {
        token.strip().upper()
        for token in re.split(r"[;,\s/]+", str(value))
        if token.strip()
    }
    return any(token in RAPIDRIDE_REFS for token in tokens)


def get_rapidride_refs_from_stop_properties(properties):
    route_text_candidates = [
        properties.get("ROUTE_LIST"),
        properties.get("ROUTES"),
        properties.get("ROUTE"),
    ]
    route_tokens = set()
    for value in route_text_candidates:
        route_tokens.update(
            token.strip().upper()
            for token in re.split(r"[;,\s/]+", str(value or ""))
            if token.strip()
        )

    refs = sorted(token for token in route_tokens if token in RAPIDRIDE_REFS)
    return refs


def get_trolleybus_line_nums_from_stop_properties(properties):
    route_text_candidates = [
        properties.get("ROUTE_LIST"),
        properties.get("ROUTES"),
        properties.get("ROUTE"),
    ]
    route_nums = set()
    for value in route_text_candidates:
        for token in re.findall(r"\b\d+\b", str(value or "")):
            route_nums.add(int(token))

    return sorted(route_num for route_num in route_nums if route_num in TROLLEYBUS_LINE_NUMS)


def get_bus_stop_name(properties):
    for key in ("STOP_NAME", "NAME"):
        value = str(properties.get(key) or "").strip()
        if value:
            return value

    cross_street_name = str(
        properties.get("CROSS_STREET_NAME")
        or properties.get("HASTUS_CROSS_STREET_NAME")
        or properties.get("CF_CROSS_STREETNAME")
        or ""
    ).strip()
    if cross_street_name:
        return simplify_rapidride_stop_label(cross_street_name)

    stop_id = properties.get("STOP_ID")
    return f"Stop {stop_id}" if stop_id else ""


def get_rapidride_stop_name(properties):
    def clean_name(value):
        return re.sub(r"\s+", " ", str(value or "")).strip()

    on_street_name = (
        properties.get("ON_STREET_NAME")
        or properties.get("HASTUS_STREET_NAME")
        or ""
    )
    cross_street_name = (
        properties.get("CROSS_STREET_NAME")
        or properties.get("HASTUS_CROSS_STREET_NAME")
        or properties.get("CF_CROSS_STREETNAME")
        or ""
    )

    on_street_name = clean_name(on_street_name)
    cross_street_name = clean_name(cross_street_name)

    primary_name = clean_name(properties.get("STOP_NAME") or properties.get("NAME") or "")
    if primary_name:
        if on_street_name and "&" in primary_name:
            primary_parts = [part.strip() for part in primary_name.split("&") if part.strip()]
            if len(primary_parts) == 2:
                simplified_on_street = normalize_stop_name_for_cluster(
                    simplify_rapidride_street_name(on_street_name)
                )
                first_part = normalize_stop_name_for_cluster(
                    simplify_rapidride_street_name(primary_parts[0])
                )
                second_part = normalize_stop_name_for_cluster(
                    simplify_rapidride_street_name(primary_parts[1])
                )
                if first_part == simplified_on_street:
                    return simplify_rapidride_stop_label(primary_parts[1])
                if second_part == simplified_on_street:
                    return simplify_rapidride_stop_label(primary_parts[0])

        if on_street_name and cross_street_name and (
            normalize_stop_name_for_cluster(primary_name)
            == normalize_stop_name_for_cluster(on_street_name)
        ):
            return simplify_rapidride_stop_label(cross_street_name)
        return simplify_rapidride_stop_label(primary_name)

    if on_street_name and cross_street_name:
        return simplify_rapidride_stop_label(cross_street_name)

    if on_street_name:
        return simplify_rapidride_stop_label(on_street_name)
    if cross_street_name:
        return simplify_rapidride_stop_label(cross_street_name)

    stop_id = properties.get("STOP_ID") or properties.get("OBJECTID")
    if stop_id:
        return f"Stop {stop_id}"

    return ""


def get_rapidride_cross_street(properties):
    def clean_name(value):
        return re.sub(r"\s+", " ", str(value or "")).strip()

    return simplify_rapidride_stop_label(clean_name(
        properties.get("CROSS_STREET_NAME")
        or properties.get("HASTUS_CROSS_STREET_NAME")
        or properties.get("CF_CROSS_STREETNAME")
        or ""
    ))


def simplify_rapidride_street_name(name):
    cleaned = re.sub(r"\s+", " ", str(name or "")).strip()
    if not cleaned:
        return ""

    simplified = CARDINAL_DIRECTION_PATTERN.sub("", cleaned)
    while True:
        updated = TRAILING_CARDINAL_DIRECTION_PATTERN.sub("", simplified)
        updated = TRAILING_STREET_TYPE_PATTERN.sub("", updated)
        updated = re.sub(r"\s+", " ", updated).strip(" ,")
        if updated == simplified:
            break
        simplified = updated

    return simplified or cleaned


def simplify_rapidride_stop_label(label):
    cleaned_label = re.sub(r"\s+", " ", str(label or "")).strip()
    if not cleaned_label:
        return ""

    if "/" in cleaned_label:
        parts = [part.strip() for part in cleaned_label.split("/")]
        simplified_parts = [simplify_rapidride_street_name(part) for part in parts if part.strip()]
        return " / ".join(part for part in simplified_parts if part)

    return simplify_rapidride_street_name(cleaned_label)


def normalize_stop_name_for_cluster(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def distance_meters(lat1, lon1, lat2, lon2):
    earth_radius_m = 6371000
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    d_lat = lat2_rad - lat1_rad
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * (math.sin(d_lon / 2) ** 2)
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))
    return earth_radius_m * c


def register_rapidride_cluster_point(stop_points, cluster_points_by_stop_id, stop_id, lat, lon):
    points = cluster_points_by_stop_id.setdefault(stop_id, [])
    points.append((lat, lon))

    stop = stop_points.get(stop_id)
    if stop is None:
        return

    avg_lat = sum(point_lat for point_lat, _ in points) / len(points)
    avg_lon = sum(point_lon for _, point_lon in points) / len(points)
    stop["coords"] = (avg_lat, avg_lon)


def update_rapidride_merged_stop_name(stop_points, stop_id, incoming_cross_street):
    stop = stop_points.get(stop_id)
    if stop is None:
        return

    incoming_cross_street = (incoming_cross_street or "").strip()
    if not incoming_cross_street:
        return

    existing_cross_streets = stop.setdefault("cross_streets", [])
    normalized_existing = {
        normalize_stop_name_for_cluster(cross_street) for cross_street in existing_cross_streets
    }
    normalized_incoming = normalize_stop_name_for_cluster(incoming_cross_street)

    if normalized_incoming and normalized_incoming not in normalized_existing:
        existing_cross_streets.append(incoming_cross_street)

    if len(existing_cross_streets) >= 2:
        stop["name"] = f"{existing_cross_streets[0]} / {existing_cross_streets[1]}"
    elif len(existing_cross_streets) == 1:
        stop["name"] = existing_cross_streets[0]


def resolve_rapidride_stop_id(
    stop_points,
    cluster_ids_by_name_and_line,
    cluster_ids_by_line,
    cluster_points_by_stop_id,
    fallback_stop_id,
    lat,
    lon,
    stop_name,
    stop_cross_street,
    line_key,
):
    line_existing_ids = cluster_ids_by_line.setdefault(line_key, [])
    normalized_name = normalize_stop_name_for_cluster(stop_name)

    for existing_id in line_existing_ids:
        existing_stop = stop_points.get(existing_id)
        existing_name = normalize_stop_name_for_cluster((existing_stop or {}).get("name", ""))
        for existing_lat, existing_lon in cluster_points_by_stop_id.get(existing_id, []):
            if distance_meters(lat, lon, existing_lat, existing_lon) <= RAPIDRIDE_STOP_CLUSTER_DISTANCE_METERS:
                register_rapidride_cluster_point(
                    stop_points,
                    cluster_points_by_stop_id,
                    existing_id,
                    lat,
                    lon,
                )
                update_rapidride_merged_stop_name(stop_points, existing_id, stop_cross_street)
                return existing_id

            if (
                distance_meters(lat, lon, existing_lat, existing_lon)
                <= RAPIDRIDE_DIRECTIONAL_PAIR_DISTANCE_METERS
            ):
                register_rapidride_cluster_point(
                    stop_points,
                    cluster_points_by_stop_id,
                    existing_id,
                    lat,
                    lon,
                )
                update_rapidride_merged_stop_name(stop_points, existing_id, stop_cross_street)
                return existing_id

    if not normalized_name:
        line_existing_ids.append(fallback_stop_id)
        register_rapidride_cluster_point(
            stop_points,
            cluster_points_by_stop_id,
            fallback_stop_id,
            lat,
            lon,
        )
        return fallback_stop_id

    cluster_key = (line_key, normalized_name)
    existing_ids = cluster_ids_by_name_and_line.setdefault(cluster_key, [])

    for existing_id in existing_ids:
        for existing_lat, existing_lon in cluster_points_by_stop_id.get(existing_id, []):
            if distance_meters(lat, lon, existing_lat, existing_lon) <= RAPIDRIDE_STOP_CLUSTER_DISTANCE_METERS:
                register_rapidride_cluster_point(
                    stop_points,
                    cluster_points_by_stop_id,
                    existing_id,
                    lat,
                    lon,
                )
                update_rapidride_merged_stop_name(stop_points, existing_id, stop_cross_street)
                return existing_id

        existing_stop = stop_points.get(existing_id)
        if existing_stop is not None:
            existing_lat, existing_lon = existing_stop.get("coords", (None, None))
            if existing_lat is not None and existing_lon is not None:
                if distance_meters(lat, lon, existing_lat, existing_lon) <= RAPIDRIDE_STOP_CLUSTER_DISTANCE_METERS:
                    register_rapidride_cluster_point(
                        stop_points,
                        cluster_points_by_stop_id,
                        existing_id,
                        lat,
                        lon,
                    )
                    update_rapidride_merged_stop_name(stop_points, existing_id, stop_cross_street)
                    return existing_id

    existing_ids.append(fallback_stop_id)
    line_existing_ids.append(fallback_stop_id)
    register_rapidride_cluster_point(
        stop_points,
        cluster_points_by_stop_id,
        fallback_stop_id,
        lat,
        lon,
    )
    return fallback_stop_id


def is_point_near_route_segments(lat, lon, segments, threshold=0.0012):
    if not segments:
        return False

    min_lat = lat - threshold
    max_lat = lat + threshold
    min_lon = lon - threshold
    max_lon = lon + threshold
    threshold_sq = threshold * threshold

    for segment in segments:
        coords = segment.get("coords", [])
        for seg_lat, seg_lon in coords:
            if seg_lat < min_lat or seg_lat > max_lat or seg_lon < min_lon or seg_lon > max_lon:
                continue
            d_lat = seg_lat - lat
            d_lon = seg_lon - lon
            if (d_lat * d_lat) + (d_lon * d_lon) <= threshold_sq:
                return True

    return False


def nearest_line_key_for_point(lat, lon, segments, threshold=0.0012):
    nearest_line_key = None
    nearest_distance_sq = None
    threshold_sq = None if threshold is None else threshold * threshold

    for segment in segments:
        line_key = segment.get("line_key")
        if not line_key:
            continue
        for seg_lat, seg_lon in segment.get("coords", []):
            d_lat = seg_lat - lat
            d_lon = seg_lon - lon
            distance_sq = (d_lat * d_lat) + (d_lon * d_lon)
            if threshold_sq is not None and distance_sq > threshold_sq:
                continue
            if nearest_distance_sq is None or distance_sq < nearest_distance_sq:
                nearest_distance_sq = distance_sq
                nearest_line_key = line_key

    return nearest_line_key


def to_streetcar_line_key(route_name):
    normalized = (route_name or "").strip().lower()
    if "first hill" in normalized:
        return "streetcar_first_hill"
    if "south lake union" in normalized:
        return "streetcar_south_lake_union"
    return ""


def normalize_streetcar_stop_name(stop_name):
    cleaned = (stop_name or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+(inbound|outbound)\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def normalize_streetcar_line_display_name(route_name):
    cleaned = re.sub(r"\s+", " ", (route_name or "").strip())
    if cleaned.lower() == "first hill alignment":
        return "First Hill"
    return cleaned


def add_or_update_stop(stop_points, stop_id, coords, name, color, priority, mode_key, line_key):
    existing = stop_points.get(stop_id)
    if existing is None or priority > existing["priority"]:
        stop_points[stop_id] = {
            "coords": coords,
            "name": (name or "").strip(),
            "color": color,
            "priority": priority,
            "mode_key": mode_key,
            "line_key": line_key,
        }


def filter_rapidride_stops_near_other_modes(stop_points, exclusion_distance_meters):
    if exclusion_distance_meters is None or exclusion_distance_meters <= 0:
        return

    non_rapidride_coords = []
    for stop in stop_points.values():
        if stop.get("mode_key") not in {"light_rail", "streetcar"}:
            continue
        lat, lon = stop.get("coords", (None, None))
        if lat is None or lon is None:
            continue
        non_rapidride_coords.append((lat, lon))

    if not non_rapidride_coords:
        return

    rapidride_stop_ids_to_remove = []
    for stop_id, stop in stop_points.items():
        if stop.get("mode_key") != "rapidride":
            continue
        lat, lon = stop.get("coords", (None, None))
        if lat is None or lon is None:
            continue

        if any(
            distance_meters(lat, lon, other_lat, other_lon) <= exclusion_distance_meters
            for other_lat, other_lon in non_rapidride_coords
        ):
            rapidride_stop_ids_to_remove.append(stop_id)

    for stop_id in rapidride_stop_ids_to_remove:
        stop_points.pop(stop_id, None)


def filter_rapidride_stops_by_min_distance(stop_points, min_distance_meters):
    if min_distance_meters is None or min_distance_meters <= 0:
        return

    rapidride_stop_ids = sorted(
        stop_id
        for stop_id, stop in stop_points.items()
        if stop.get("mode_key") == "rapidride"
    )

    kept_points = []
    rapidride_stop_ids_to_remove = []

    for stop_id in rapidride_stop_ids:
        stop = stop_points.get(stop_id, {})
        lat, lon = stop.get("coords", (None, None))
        if lat is None or lon is None:
            continue

        if any(
            distance_meters(lat, lon, kept_lat, kept_lon) <= min_distance_meters
            for kept_lat, kept_lon in kept_points
        ):
            rapidride_stop_ids_to_remove.append(stop_id)
            continue

        kept_points.append((lat, lon))

    for stop_id in rapidride_stop_ids_to_remove:
        stop_points.pop(stop_id, None)


def filter_trolleybus_stops_by_min_distance(stop_points, min_distance_meters):
    if min_distance_meters is None or min_distance_meters <= 0:
        return

    trolleybus_stop_ids = sorted(
        stop_id
        for stop_id, stop in stop_points.items()
        if stop.get("mode_key") == "trolleybus"
    )

    kept_points = []
    trolleybus_stop_ids_to_remove = []

    for stop_id in trolleybus_stop_ids:
        stop = stop_points.get(stop_id, {})
        lat, lon = stop.get("coords", (None, None))
        if lat is None or lon is None:
            continue

        if any(
            distance_meters(lat, lon, kept_lat, kept_lon) <= min_distance_meters
            for kept_lat, kept_lon in kept_points
        ):
            trolleybus_stop_ids_to_remove.append(stop_id)
            continue

        kept_points.append((lat, lon))

    for stop_id in trolleybus_stop_ids_to_remove:
        stop_points.pop(stop_id, None)


def save_stops_geojson(stop_points, output_file):
    features = []
    for stop_id, stop in sorted(stop_points.items()):
        lat, lon = stop.get("coords", (None, None))
        if lat is None or lon is None:
            continue

        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": str(stop_id),
                    "name": stop.get("name", ""),
                    "mode_key": stop.get("mode_key", "unknown"),
                    "line_key": stop.get("line_key", "unknown"),
                    "priority": stop.get("priority"),
                    "color": stop.get("color"),
                },
            }
        )

    feature_collection = {"type": "FeatureCollection", "features": features}
    with open(output_file, "w", encoding="utf-8") as geojson_file:
        json.dump(feature_collection, geojson_file, ensure_ascii=False, indent=2)
    print(f"Transit stops GeoJSON successfully generated and saved to {output_file}")


def collect_light_rail(route_segments, stop_points):
    params = {
        "where": "1=1",
        "outFields": "DESCRIPTIO,STATUS",
        "f": "geojson",
    }
    features = fetch_geojson(LIGHT_RAIL_LINES_URL, params=params).get("features", [])
    for feature in features:
        props = feature.get("properties", {})
        description = props.get("DESCRIPTIO", "")
        if is_excluded_airport_terminal_train(description):
            continue

        is_line2 = is_line2_description(description)
        line_key = "line_2" if is_line2 else "line_1"
        color = LINE_COLORS["line_2"] if is_line2 else LINE_COLORS["line_1"]
        tooltip = f"{description or 'Link Light Rail'} ({props.get('STATUS', '')})"

        for line in split_line_geometry(feature.get("geometry", {})):
            coords = to_lat_lon_pairs(line)
            if not coords:
                continue
            route_segments.append({
                "priority": PRIORITY_ORDER["light_rail"],
                "coords": coords,
                "color": color,
                "weight": 5,
                "tooltip": tooltip,
                "mode_key": "light_rail",
                "line_key": line_key,
            })

    stop_features = fetch_geojson(LIGHT_RAIL_STOPS_URL, params={"where": "1=1", "outFields": "*", "f": "geojson"}).get("features", [])
    for feature in stop_features:
        props = feature.get("properties", {})
        if is_excluded_light_rail_stop(props):
            continue

        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue
        lon, lat = geometry.get("coordinates", [None, None])
        if lon is None or lat is None:
            continue
        lon, lat = normalize_to_wgs84(lon, lat)

        color = STOP_COLORS["line_2"] if is_line2_light_rail_stop(props) else STOP_COLORS["line_1"]
        line_key = "line_2" if color == STOP_COLORS["line_2"] else "line_1"
        stop_id = str(props.get("OBJECTID") or f"lr_{lat}_{lon}")
        stop_name = props.get("STATION") or props.get("DESCRIPTIO") or ""
        add_or_update_stop(
            stop_points,
            stop_id,
            (lat, lon),
            stop_name,
            color,
            PRIORITY_ORDER["light_rail"],
            "light_rail",
            line_key,
        )


def collect_rapidride(route_segments, stop_points):
    route_num_csv = ",".join(str(route_num) for route_num in RAPIDRIDE_ROUTE_NUMS)
    where_clause = f"ROUTE_NUM in ({route_num_csv})"
    features = fetch_geojson(
        RAPIDRIDE_LINES_URL,
        params={"where": where_clause, "outFields": "ROUTE_NUM", "f": "geojson"},
    ).get("features", [])

    for feature in features:
        props = feature.get("properties", {})
        route_num = props.get("ROUTE_NUM")
        route_ref = RAPIDRIDE_NUM_TO_REF.get(route_num)
        if route_ref is None:
            continue

        for line in split_line_geometry(feature.get("geometry", {})):
            coords = to_lat_lon_pairs(line)
            if not coords:
                continue
            route_segments.append({
                "priority": PRIORITY_ORDER["rapidride"],
                "coords": coords,
                "color": LINE_COLORS["rapidride"],
                "weight": 4,
                "tooltip": f"RapidRide {route_ref}",
                "mode_key": "rapidride",
                "line_key": f"rapidride_{route_ref.lower()}",
            })

    stop_features = fetch_all_geojson_features(
        RAPIDRIDE_STOPS_URL,
        params={"where": "1=1", "outFields": "*", "f": "geojson"},
    )
    cluster_ids_by_name_and_line = {}
    cluster_ids_by_line = {}
    cluster_points_by_stop_id = {}

    for feature in stop_features:
        props = feature.get("properties", {})
        if props.get("IN_SERVICE_FLAG") not in {None, "", "Y"}:
            continue

        matched_refs = get_rapidride_refs_from_stop_properties(props)
        if not matched_refs:
            continue

        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue
        lon, lat = geometry.get("coordinates", [None, None])
        if lon is None or lat is None:
            continue
        lon, lat = normalize_to_wgs84(lon, lat)

        line_key = f"rapidride_{matched_refs[0].lower()}"

        stop_name = get_rapidride_stop_name(props)
        stop_cross_street = get_rapidride_cross_street(props)
        fallback_stop_id = str(props.get("STOP_ID") or props.get("OBJECTID") or f"rr_{lat}_{lon}")
        stop_id = resolve_rapidride_stop_id(
            stop_points,
            cluster_ids_by_name_and_line,
            cluster_ids_by_line,
            cluster_points_by_stop_id,
            fallback_stop_id,
            lat,
            lon,
            stop_name,
            stop_cross_street,
            line_key,
        )
        add_or_update_stop(
            stop_points,
            stop_id,
            (lat, lon),
            stop_name,
            STOP_COLORS["rapidride"],
            PRIORITY_ORDER["rapidride"],
            "rapidride",
            line_key,
        )
        if stop_id in stop_points:
            update_rapidride_merged_stop_name(stop_points, stop_id, stop_cross_street)


def collect_trolleybus(route_segments, stop_points):
    route_num_csv = ",".join(str(route_num) for route_num in sorted(TROLLEYBUS_LINE_NUMS))
    where_clause = f"ROUTE_NUM in ({route_num_csv})"
    features = fetch_geojson(
        RAPIDRIDE_LINES_URL,
        params={"where": where_clause, "outFields": "ROUTE_NUM", "f": "geojson"},
    ).get("features", [])

    for feature in features:
        props = feature.get("properties", {})
        route_num = props.get("ROUTE_NUM")
        if route_num not in TROLLEYBUS_LINE_NUMS:
            continue

        for line in split_line_geometry(feature.get("geometry", {})):
            coords = to_lat_lon_pairs(line)
            if not coords:
                continue
            route_segments.append({
                "priority": PRIORITY_ORDER["trolleybus"],
                "coords": coords,
                "color": LINE_COLORS["trolleybus"],
                "weight": 3,
                "tooltip": f"Trolleybus {route_num}",
                "mode_key": "trolleybus",
                "line_key": f"trolleybus_{route_num}",
            })

    stop_features = fetch_all_geojson_features(
        RAPIDRIDE_STOPS_URL,
        params={"where": "1=1", "outFields": "*", "f": "geojson"},
    )
    for feature in stop_features:
        props = feature.get("properties", {})
        if props.get("IN_SERVICE_FLAG") not in {None, "", "Y"}:
            continue

        matched_route_nums = get_trolleybus_line_nums_from_stop_properties(props)
        if not matched_route_nums:
            continue

        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue
        lon, lat = geometry.get("coordinates", [None, None])
        if lon is None or lat is None:
            continue
        lon, lat = normalize_to_wgs84(lon, lat)

        route_num = matched_route_nums[0]
        line_key = f"trolleybus_{route_num}"
        stop_name = get_bus_stop_name(props)
        stop_id = str(props.get("STOP_ID") or props.get("OBJECTID") or f"tb_{route_num}_{lat}_{lon}")

        add_or_update_stop(
            stop_points,
            stop_id,
            (lat, lon),
            stop_name,
            STOP_COLORS["trolleybus"],
            PRIORITY_ORDER["trolleybus"],
            "trolleybus",
            line_key,
        )


def collect_streetcar(route_segments, stop_points):
    features = fetch_geojson(STREETCAR_LINES_URL).get("features", [])
    streetcar_segments = []
    for feature in features:
        props = feature.get("properties", {})
        route_name = props.get("LINE") or props.get("ROUTE") or props.get("NAME") or "Seattle Streetcar"
        route_display_name = normalize_streetcar_line_display_name(route_name)
        line_key = to_streetcar_line_key(route_name)
        if not line_key:
            continue
        for line in split_line_geometry(feature.get("geometry", {})):
            coords = to_lat_lon_pairs(line)
            if not coords:
                continue
            segment = {
                "priority": PRIORITY_ORDER["streetcar"],
                "coords": coords,
                "color": LINE_COLORS["streetcar"],
                "weight": 4,
                "tooltip": route_display_name,
                "mode_key": "streetcar",
                "line_key": line_key,
            }
            route_segments.append(segment)
            streetcar_segments.append(segment)

    stop_features = fetch_geojson(STREETCAR_STOPS_URL).get("features", [])
    for feature in stop_features:
        props = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue
        lon, lat = geometry.get("coordinates", [None, None])
        if lon is None or lat is None:
            continue
        lon, lat = normalize_to_wgs84(lon, lat)

        raw_stop_name = props.get("STOP") or props.get("STOP_NAME") or props.get("NAME") or props.get("STATION") or ""
        stop_name = normalize_streetcar_stop_name(raw_stop_name)
        route_name = (
            props.get("LINE")
            or props.get("ROUTE")
            or props.get("STATION")
            or props.get("LINE_NAME")
            or props.get("ROUTE_NAME")
            or ""
        )
        line_key = to_streetcar_line_key(route_name)
        if not line_key:
            line_key = (
                nearest_line_key_for_point(lat, lon, streetcar_segments)
                or nearest_line_key_for_point(lat, lon, streetcar_segments, threshold=None)
                or "streetcar_unknown"
            )

        if stop_name:
            stop_id = f"sc_{line_key}_{to_key(stop_name)}"
        else:
            stop_id = str(props.get("OBJECTID") or props.get("STOP_ID") or f"sc_{lat}_{lon}")

        add_or_update_stop(
            stop_points,
            stop_id,
            (lat, lon),
            stop_name,
            STOP_COLORS["streetcar"],
            PRIORITY_ORDER["streetcar"],
            "streetcar",
            line_key,
        )


def line_label_sort_key(line_item):
    line_key, label = line_item
    if line_key.startswith("trolleybus_"):
        line_suffix = line_key.split("_", 1)[1]
        if line_suffix.isdigit():
            return (0, int(line_suffix), label.lower())
    return (1, label.lower())


def add_filter_controls(map_object, line_labels):
    category_labels = {
        "light_rail": "Light Rail",
        "streetcar": "Streetcar",
        "rapidride": "RapidRide",
        "trolleybus": "Trolleybus",
    }
    sorted_lines = sorted(line_labels.items(), key=line_label_sort_key)

    lines_by_category = {key: [] for key in category_labels}
    for line_key, label in sorted_lines:
        if line_key in {"line_1", "line_2"}:
            category = "light_rail"
        elif line_key.startswith("streetcar_"):
            category = "streetcar"
        elif line_key.startswith("rapidride_"):
            category = "rapidride"
        elif line_key.startswith("trolleybus_"):
            category = "trolleybus"
        else:
            continue
        lines_by_category[category].append((line_key, label))

    category_section_html = ""
    for category_key, category_label in category_labels.items():
        if not lines_by_category.get(category_key):
            continue
        category_id = f"category-{category_key}"
        line_items_html = "".join(
            (
                f'<div><input type="checkbox" id="line-{line_key}" class="line-toggle" '
                f'data-category="{category_key}" data-line="{line_key}" checked> '
                f'<label for="line-{line_key}">{label}</label></div>'
            )
            for line_key, label in lines_by_category.get(category_key, [])
        )
        category_section_html += (
            '<div style="margin-bottom:8px;">'
            f'<div style="display:flex; align-items:center; gap:4px;">'
            f'<button type="button" class="collapse-toggle" data-target="lines-{category_key}" '
            'style="border:0; background:transparent; padding:0 2px; cursor:pointer; font-size:12px; line-height:1;">▾</button>'
            f'<input type="checkbox" id="{category_id}" class="category-toggle" '
            f'data-category="{category_key}" checked> '
            f'<label for="{category_id}" style="font-weight:600; cursor:pointer;">{category_label}</label></div>'
            f'<div id="lines-{category_key}" style="display:flex; flex-direction:column; gap:3px; margin-left:16px; margin-top:3px;">{line_items_html}</div>'
            '</div>'
        )

    control_html = f"""
    <div id="transit-filter-control" style="
        position: fixed;
        top: 10px;
        right: 10px;
        z-index: 9999;
        background: white;
        border: 1px solid #bbb;
        border-radius: 6px;
        box-shadow: 0 1px 6px rgba(0,0,0,0.25);
        padding: 10px;
        max-height: 80vh;
        overflow-y: auto;
        font-size: 12px;
        line-height: 1.4;
    ">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:6px;">
            <div style="font-weight: 600;">Transit Filters</div>
            <button type="button" id="toggle-filter-panel" style="border:1px solid #bbb; border-radius:4px; background:#fff; padding:1px 6px; font-size:12px; cursor:pointer;">Hide</button>
        </div>
        <div id="transit-filter-body" style="display:flex; flex-direction:column;">
            <div style="margin-bottom:8px;">
                <input type="checkbox" id="toggle-stop-names" checked>
                <label for="toggle-stop-names">Show stop names</label>
            </div>
            {category_section_html}
        </div>
    </div>
    <script>
    (function() {{
        function readEnabledSet(selector, attr) {{
            const enabled = new Set();
            document.querySelectorAll(selector).forEach((checkbox) => {{
                if (checkbox.checked) {{
                    enabled.add(checkbox.getAttribute(attr));
                }}
            }});
            return enabled;
        }}

        function categoryLineCheckboxes(category) {{
            return document.querySelectorAll(`.line-toggle[data-category="${{category}}"]`);
        }}

        function updateCategoryState(category) {{
            const categoryCheckbox = document.querySelector(`.category-toggle[data-category="${{category}}"]`);
            if (!categoryCheckbox) {{
                return;
            }}

            const lineCheckboxes = Array.from(categoryLineCheckboxes(category));
            if (!lineCheckboxes.length) {{
                categoryCheckbox.checked = false;
                categoryCheckbox.indeterminate = false;
                return;
            }}

            const checkedCount = lineCheckboxes.filter((checkbox) => checkbox.checked).length;
            categoryCheckbox.checked = checkedCount > 0;
            categoryCheckbox.indeterminate = checkedCount > 0 && checkedCount < lineCheckboxes.length;
        }}

        function setCategoryLines(category, checked) {{
            categoryLineCheckboxes(category).forEach((checkbox) => {{
                checkbox.checked = checked;
            }});
        }}

        function readStateFromUrl() {{
            const params = new URLSearchParams(window.location.search || '');
            const categories = new Set();
            const lines = new Set();

            const categoriesParam = (params.get('categories') || '').trim();
            if (categoriesParam) {{
                categoriesParam.split(',').map((value) => value.trim()).filter(Boolean).forEach((value) => {{
                    categories.add(value);
                }});
            }}

            const linesParam = (params.get('lines') || '').trim();
            if (linesParam) {{
                linesParam.split(',').map((value) => value.trim()).filter(Boolean).forEach((value) => {{
                    lines.add(value);
                }});
            }}

            const stopNamesParam = params.get('stop_names');
            let showStopNames = null;
            if (stopNamesParam === '1' || stopNamesParam === '0') {{
                showStopNames = stopNamesParam === '1';
            }}

            const panelParam = params.get('panel');
            let panelCollapsed = null;
            if (panelParam === 'hidden' || panelParam === 'shown') {{
                panelCollapsed = panelParam === 'hidden';
            }}

            return {{
                hasCategories: categoriesParam.length > 0,
                hasLines: linesParam.length > 0,
                categories,
                lines,
                showStopNames,
                panelCollapsed,
            }};
        }}

        function writeStateToUrl() {{
            if (typeof window === 'undefined' || !window.history || !window.history.replaceState) {{
                return;
            }}

            const params = new URLSearchParams(window.location.search || '');
            const enabledCategories = Array.from(readEnabledSet('.category-toggle', 'data-category')).sort();
            const enabledLines = Array.from(readEnabledSet('.line-toggle', 'data-line')).sort();
            const stopNameToggle = document.getElementById('toggle-stop-names');
            const showStopNames = !stopNameToggle || stopNameToggle.checked;
            const filterPanelBody = document.getElementById('transit-filter-body');
            const panelCollapsed = Boolean(filterPanelBody && filterPanelBody.style.display === 'none');

            params.set('categories', enabledCategories.join(','));
            params.set('lines', enabledLines.join(','));
            params.set('stop_names', showStopNames ? '1' : '0');
            params.set('panel', panelCollapsed ? 'hidden' : 'shown');

            const nextQuery = params.toString();
            const nextUrl = `${{window.location.pathname}}${{nextQuery ? `?${{nextQuery}}` : ''}}${{window.location.hash || ''}}`;
            window.history.replaceState(null, '', nextUrl);
        }}

        function applyStateFromUrl() {{
            const state = readStateFromUrl();

            if (state.hasLines) {{
                document.querySelectorAll('.line-toggle').forEach((checkbox) => {{
                    const line = checkbox.getAttribute('data-line');
                    checkbox.checked = state.lines.has(line);
                }});
            }}

            if (state.hasCategories) {{
                document.querySelectorAll('.category-toggle').forEach((checkbox) => {{
                    const category = checkbox.getAttribute('data-category');
                    checkbox.checked = state.categories.has(category);
                    checkbox.indeterminate = false;
                }});
            }}

            const stopNameToggle = document.getElementById('toggle-stop-names');
            if (stopNameToggle && state.showStopNames !== null) {{
                stopNameToggle.checked = state.showStopNames;
            }}

            document.querySelectorAll('.category-toggle').forEach((checkbox) => {{
                updateCategoryState(checkbox.getAttribute('data-category'));
            }});

            return state;
        }}

        function setVisible(element, visible) {{
            const opacity = visible ? '' : '0';
            if (element.tagName === 'path' || element.tagName === 'circle') {{
                element.style.opacity = opacity;
                element.style.pointerEvents = visible ? '' : 'none';
                return;
            }}
            if (element.tagName === 'DIV' && element.classList.contains('transit-feature')) {{
                element.style.display = visible ? '' : 'none';
            }}
        }}

        function applyTransitFilters() {{
            const enabledCategories = readEnabledSet('.category-toggle', 'data-category');
            const enabledLines = readEnabledSet('.line-toggle', 'data-line');
            const stopNameToggle = document.getElementById('toggle-stop-names');
            const showStopNames = !stopNameToggle || stopNameToggle.checked;

            document.querySelectorAll('.transit-feature').forEach((element) => {{
                const modeClass = Array.from(element.classList).find((cls) => cls.startsWith('mode-'));
                const lineClass = Array.from(element.classList).find((cls) => cls.startsWith('line-'));
                const mode = modeClass ? modeClass.substring(5) : null;
                const line = lineClass ? lineClass.substring(5) : null;
                const isStopLabel = element.classList.contains('transit-stop-label');

                const visible = Boolean(
                    mode
                    && line
                    && enabledCategories.has(mode)
                    && enabledLines.has(line)
                    && (!isStopLabel || showStopNames)
                );
                setVisible(element, visible);
            }});
        }}

        function getLeafletMap() {{
            for (const key in window) {{
                if (!Object.prototype.hasOwnProperty.call(window, key)) {{
                    continue;
                }}
                const value = window[key];
                if (value && typeof L !== 'undefined' && value instanceof L.Map) {{
                    return value;
                }}
            }}
            return null;
        }}

        function setCollapsed(toggleButton, targetElement, collapsed) {{
            targetElement.style.display = collapsed ? 'none' : 'flex';
            toggleButton.textContent = collapsed ? '▸' : '▾';
        }}

        const filterPanelBody = document.getElementById('transit-filter-body');
        const filterPanelToggle = document.getElementById('toggle-filter-panel');
        function setFilterPanelCollapsed(collapsed) {{
            if (!filterPanelBody || !filterPanelToggle) {{
                return;
            }}
            filterPanelBody.style.display = collapsed ? 'none' : 'flex';
            filterPanelToggle.textContent = collapsed ? 'Show' : 'Hide';
        }}

        if (filterPanelBody && filterPanelToggle) {{
            let panelCollapsed = false;
            filterPanelToggle.addEventListener('click', () => {{
                panelCollapsed = !panelCollapsed;
                setFilterPanelCollapsed(panelCollapsed);
                writeStateToUrl();
            }});
        }}

        document.querySelectorAll('.collapse-toggle').forEach((button) => {{
            const targetId = button.getAttribute('data-target');
            const target = document.getElementById(targetId);
            if (!target) {{
                return;
            }}
            let collapsed = false;
            button.addEventListener('click', () => {{
                collapsed = !collapsed;
                setCollapsed(button, target, collapsed);
            }});
        }});

        document.querySelectorAll('.category-toggle').forEach((checkbox) => {{
            checkbox.addEventListener('change', (event) => {{
                const category = event.target.getAttribute('data-category');
                setCategoryLines(category, event.target.checked);
                updateCategoryState(category);
                applyTransitFilters();
                writeStateToUrl();
            }});
        }});

        document.querySelectorAll('.line-toggle').forEach((checkbox) => {{
            checkbox.addEventListener('change', (event) => {{
                const category = event.target.getAttribute('data-category');
                updateCategoryState(category);
                applyTransitFilters();
                writeStateToUrl();
            }});
        }});

        const stopNameToggle = document.getElementById('toggle-stop-names');
        if (stopNameToggle) {{
            stopNameToggle.addEventListener('change', () => {{
                applyTransitFilters();
                writeStateToUrl();
            }});
        }}

        const initialState = applyStateFromUrl();
        if (initialState && initialState.panelCollapsed !== null) {{
            setFilterPanelCollapsed(initialState.panelCollapsed);
        }}
        writeStateToUrl();

        window.setTimeout(applyTransitFilters, 400);
        const leafletMap = getLeafletMap();
        if (leafletMap) {{
            leafletMap.on('zoomend', applyTransitFilters);
            leafletMap.on('moveend', applyTransitFilters);
            leafletMap.on('layeradd', applyTransitFilters);
        }}
    }})();
    </script>
    """

    map_object.get_root().html.add_child(Element(control_html))


def save_static_image(route_segments, stop_points, line2_segments, output_file):
    all_points = []
    for segment in route_segments:
        all_points.extend(segment['coords'])
    for segment in line2_segments:
        all_points.extend(segment['coords'])
    all_points.extend(stop['coords'] for stop in stop_points.values())

    if not all_points:
        print("Warning: No transit geometry found for static image output.")
        return

    lats = [point[0] for point in all_points]
    lons = [point[1] for point in all_points]

    lat_padding = (max(lats) - min(lats)) * 0.08 or 0.02
    lon_padding = (max(lons) - min(lons)) * 0.08 or 0.02

    fig, ax = plt.subplots(figsize=(14, 12), dpi=200)
    ax.set_facecolor('#f7f7f7')
    fig.patch.set_facecolor('#f7f7f7')

    min_lon = min(lons) - lon_padding
    max_lon = max(lons) + lon_padding
    min_lat = min(lats) - lat_padding
    max_lat = max(lats) + lat_padding

    add_tile_background(ax, min_lon, max_lon, min_lat, max_lat)

    draw_segments = sorted(route_segments + line2_segments, key=lambda s: s['priority'])
    for segment in draw_segments:
        segment_lats = [coord[0] for coord in segment['coords']]
        segment_lons = [coord[1] for coord in segment['coords']]
        ax.plot(
            segment_lons,
            segment_lats,
            color=segment['color'],
            linewidth=segment['weight'] * 0.55,
            alpha=0.85,
            solid_capstyle='round',
            zorder=segment['priority'] + 1,
        )

    for stop in sorted(stop_points.values(), key=lambda s: s['priority']):
        lat, lon = stop['coords']
        ax.scatter(
            lon,
            lat,
            s=12,
            c=stop['color'],
            edgecolors='white',
            linewidths=0.4,
            alpha=0.95,
            zorder=stop['priority'] + 5,
        )

        if stop['name']:
            ax.text(
                lon,
                lat,
                f" {stop['name']}",
                fontsize=4.6,
                color='#222222',
                va='center',
                ha='left',
                zorder=stop['priority'] + 6,
                bbox={
                    'boxstyle': 'round,pad=0.1',
                    'facecolor': 'white',
                    'edgecolor': 'none',
                    'alpha': 0.55,
                },
            )

    ax.set_xlim(min_lon, max_lon)
    ax.set_ylim(min_lat, max_lat)
    ax.set_aspect('equal', adjustable='box')
    ax.axis('off')

    fig.savefig(output_file, bbox_inches='tight', pad_inches=0.08)
    plt.close(fig)
    print(f"Static transit map successfully generated and saved to {output_file}")

def generate_map():
    print("Fetching data from GIS sources...")

    m = folium.Map(location=[47.6062, -122.3321], zoom_start=11, tiles="CartoDB positron")

    route_segments = []
    stop_points = {}
    if ENABLE_TROLLEYBUS:
        collect_trolleybus(route_segments, stop_points)
    collect_rapidride(route_segments, stop_points)
    collect_streetcar(route_segments, stop_points)
    collect_light_rail(route_segments, stop_points)
    filter_rapidride_stops_near_other_modes(
        stop_points,
        RAPIDRIDE_STOP_EXCLUDE_NEAR_RAIL_OR_STREETCAR_METERS,
    )
    filter_rapidride_stops_by_min_distance(
        stop_points,
        RAPIDRIDE_STOP_MIN_DISTANCE_METERS,
    )
    if ENABLE_TROLLEYBUS:
        filter_trolleybus_stops_by_min_distance(
            stop_points,
            TROLLEYBUS_STOP_MIN_DISTANCE_METERS,
        )

    line_labels = {
        "line_1": "1 Line",
        "line_2": "2 Line",
    }

    for segment in sorted(route_segments, key=lambda s: s['priority']):
        mode_key = segment.get("mode_key", "unknown")
        line_key = segment.get("line_key", "unknown")
        if line_key.startswith("rapidride_"):
            line_labels[line_key] = line_key.split('_', 1)[1].upper()
        elif line_key.startswith("trolleybus_"):
            line_labels[line_key] = line_key.split('_', 1)[1]
        elif line_key.startswith("streetcar_"):
            line_labels[line_key] = segment.get("tooltip", "Streetcar")

        folium.PolyLine(
            segment['coords'],
            color=segment['color'],
            weight=segment['weight'],
            opacity=0.55,
            tooltip=segment['tooltip'],
            class_name=f"transit-feature mode-{mode_key} line-{line_key}",
        ).add_to(m)

    for stop in sorted(stop_points.values(), key=lambda s: s['priority']):
        mode_key = stop.get("mode_key", "unknown")
        line_key = stop.get("line_key", "unknown")
        folium.CircleMarker(
            location=stop['coords'],
            radius=5,
            color=stop['color'],
            fill=True,
            fill_color=stop['color'],
            fill_opacity=0.95,
            weight=1,
            tooltip=stop['name'] or stop['mode_key'].replace('_', ' ').title(),
            class_name=f"transit-feature mode-{mode_key} line-{line_key}",
        ).add_to(m)

        if stop['name']:
            folium.Marker(
                location=stop['coords'],
                icon=folium.DivIcon(
                    class_name=f"transit-feature transit-stop-label mode-{mode_key} line-{line_key}",
                    icon_size=None,
                    icon_anchor=(8, -8),
                    html=(
                        f"<div class=\"transit-feature transit-stop-label mode-{mode_key} line-{line_key}\" style=\"font-size: 10px; color: #222; "
                        "background: rgba(255, 255, 255, 0.75); padding: 1px 3px; "
                        "border-radius: 3px; white-space: nowrap; display: inline-block; width: max-content;\">"
                        f"{stop['name']}"
                        "</div>"
                    )
                )
            ).add_to(m)

        if line_key.startswith("rapidride_") and line_key not in line_labels and line_key != "rapidride_unknown":
            line_labels[line_key] = line_key.split('_', 1)[1].upper()
        elif line_key.startswith("streetcar_") and line_key not in line_labels:
            line_labels[line_key] = "Streetcar"

        if line_key == "rapidride_unknown" and line_key not in line_labels:
            line_labels[line_key] = "RapidRide (Unassigned Stop)"

    add_filter_controls(m, line_labels)

    # Save the map
    html_output_file = "index.html"
    m.save(html_output_file)
    print(f"Transit map successfully generated and saved to {html_output_file}")

    stops_geojson_output_file = "stops.geojson"
    save_stops_geojson(stop_points, stops_geojson_output_file)

if __name__ == "__main__":
    generate_map()
