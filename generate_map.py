import math
import re
from io import BytesIO

import folium
import matplotlib.pyplot as plt
import requests
from branca.element import Element
from PIL import Image
from pyproj import Transformer


PRIORITY_ORDER = {
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
    "rapidride": "#ef6a78",
    "streetcar": "#f6ad55",
}

STOP_COLORS = {
    "line_1": "#008f5a",
    "line_2": "#005b9f",
    "rapidride": "#e31837",
    "streetcar": "#f28c28",
}


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
    threshold_sq = threshold * threshold

    for segment in segments:
        line_key = segment.get("line_key")
        if not line_key:
            continue
        for seg_lat, seg_lon in segment.get("coords", []):
            d_lat = seg_lat - lat
            d_lon = seg_lon - lon
            distance_sq = (d_lat * d_lat) + (d_lon * d_lon)
            if distance_sq > threshold_sq:
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

    rapidride_segments = [segment for segment in route_segments if segment.get("mode_key") == "rapidride"]

    stop_features = fetch_geojson(RAPIDRIDE_STOPS_URL, params={"where": "1=1", "outFields": "*", "f": "geojson"}).get("features", [])
    for feature in stop_features:
        props = feature.get("properties", {})
        route_list = props.get("ROUTE_LIST") or props.get("ROUTES") or props.get("ROUTE")

        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue
        lon, lat = geometry.get("coordinates", [None, None])
        if lon is None or lat is None:
            continue
        lon, lat = normalize_to_wgs84(lon, lat)

        route_tokens = {
            token.strip().upper()
            for token in re.split(r"[;,\s/]+", str(route_list or ""))
            if token.strip()
        }
        matched_refs = sorted(token for token in route_tokens if token in RAPIDRIDE_REFS)
        explicitly_rapidride = bool(matched_refs)
        near_rapidride = is_point_near_route_segments(lat, lon, rapidride_segments)
        if not explicitly_rapidride and not near_rapidride:
            continue

        line_key = "rapidride_unknown"
        if matched_refs:
            line_key = f"rapidride_{matched_refs[0].lower()}"
        else:
            for segment in rapidride_segments:
                if is_point_near_route_segments(lat, lon, [segment], threshold=0.0012):
                    line_key = segment.get("line_key", "rapidride_unknown")
                    break

        stop_id = str(props.get("STOP_ID") or props.get("OBJECTID") or f"rr_{lat}_{lon}")
        stop_name = props.get("STOP_NAME") or props.get("NAME") or ""
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


def collect_streetcar(route_segments, stop_points):
    features = fetch_geojson(STREETCAR_LINES_URL).get("features", [])
    streetcar_segments = []
    for feature in features:
        props = feature.get("properties", {})
        route_name = props.get("LINE") or props.get("ROUTE") or props.get("NAME") or "Seattle Streetcar"
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
                "tooltip": route_name,
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

        stop_id = str(props.get("OBJECTID") or props.get("STOP_ID") or f"sc_{lat}_{lon}")
        stop_name = props.get("STOP") or props.get("STOP_NAME") or props.get("NAME") or props.get("STATION") or ""
        route_name = props.get("LINE") or props.get("ROUTE") or ""
        line_key = to_streetcar_line_key(route_name)
        if not line_key:
            line_key = nearest_line_key_for_point(lat, lon, streetcar_segments) or "streetcar_unknown"
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


def add_filter_controls(map_object, line_labels):
    category_labels = {
        "light_rail": "Light Rail",
        "streetcar": "Streetcar",
        "rapidride": "RapidRide",
    }
    sorted_lines = sorted(line_labels.items(), key=lambda item: item[1].lower())

    lines_by_category = {key: [] for key in category_labels}
    for line_key, label in sorted_lines:
        if line_key in {"line_1", "line_2"}:
            category = "light_rail"
        elif line_key.startswith("streetcar_"):
            category = "streetcar"
        elif line_key.startswith("rapidride_"):
            category = "rapidride"
        else:
            continue
        lines_by_category[category].append((line_key, label))

    category_section_html = ""
    for category_key, category_label in category_labels.items():
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
            f'<div><input type="checkbox" id="{category_id}" class="category-toggle" '
            f'data-category="{category_key}" checked> '
            f'<label for="{category_id}" style="font-weight:600;">{category_label}</label></div>'
            f'<div style="display:flex; flex-direction:column; gap:3px; margin-left:16px; margin-top:3px;">{line_items_html}</div>'
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
        <div style="font-weight: 600; margin-bottom: 6px;">Transit Filters</div>
        <div style="display:flex; flex-direction:column;">{category_section_html}</div>
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

            document.querySelectorAll('.transit-feature').forEach((element) => {{
                const modeClass = Array.from(element.classList).find((cls) => cls.startsWith('mode-'));
                const lineClass = Array.from(element.classList).find((cls) => cls.startsWith('line-'));
                const mode = modeClass ? modeClass.substring(5) : null;
                const line = lineClass ? lineClass.substring(5) : null;

                const visible = Boolean(mode && line && enabledCategories.has(mode) && enabledLines.has(line));
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

        document.querySelectorAll('.category-toggle').forEach((checkbox) => {{
            checkbox.addEventListener('change', (event) => {{
                const category = event.target.getAttribute('data-category');
                setCategoryLines(category, event.target.checked);
                updateCategoryState(category);
                applyTransitFilters();
            }});
        }});

        document.querySelectorAll('.line-toggle').forEach((checkbox) => {{
            checkbox.addEventListener('change', (event) => {{
                const category = event.target.getAttribute('data-category');
                updateCategoryState(category);
                applyTransitFilters();
            }});
        }});

        document.querySelectorAll('.category-toggle').forEach((checkbox) => {{
            updateCategoryState(checkbox.getAttribute('data-category'));
        }});

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
    collect_rapidride(route_segments, stop_points)
    collect_streetcar(route_segments, stop_points)
    collect_light_rail(route_segments, stop_points)

    line_labels = {
        "line_1": "Light Rail - 1 Line",
        "line_2": "Light Rail - 2 Line",
    }

    for segment in sorted(route_segments, key=lambda s: s['priority']):
        mode_key = segment.get("mode_key", "unknown")
        line_key = segment.get("line_key", "unknown")
        if line_key.startswith("rapidride_"):
            line_labels[line_key] = f"RapidRide {line_key.split('_', 1)[1].upper()}"
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
                    class_name=f"transit-feature mode-{mode_key} line-{line_key}",
                    icon_size=(220, 14),
                    icon_anchor=(8, -8),
                    html=(
                        f"<div class=\"transit-feature mode-{mode_key} line-{line_key}\" style=\"font-size: 10px; color: #222; "
                        "background: rgba(255, 255, 255, 0.75); padding: 1px 3px; "
                        "border-radius: 3px; white-space: nowrap;\">"
                        f"{stop['name']}"
                        "</div>"
                    )
                )
            ).add_to(m)

        if line_key.startswith("rapidride_") and line_key not in line_labels and line_key != "rapidride_unknown":
            line_labels[line_key] = f"RapidRide {line_key.split('_', 1)[1].upper()}"
        elif line_key.startswith("streetcar_") and line_key not in line_labels:
            line_labels[line_key] = "Streetcar"

        if line_key == "rapidride_unknown" and line_key not in line_labels:
            line_labels[line_key] = "RapidRide (Unassigned Stop)"

    add_filter_controls(m, line_labels)

    # Save the map
    html_output_file = "Seattle_Transit_Map.html"
    m.save(html_output_file)
    print(f"Transit map successfully generated and saved to {html_output_file}")

if __name__ == "__main__":
    generate_map()
