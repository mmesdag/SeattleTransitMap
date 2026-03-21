import unittest
from unittest.mock import patch

from generate_map import (
    nearest_line_key_for_point,
    normalize_streetcar_stop_name,
    to_streetcar_line_key,
    is_excluded_light_rail_stop,
    is_line2_light_rail_stop,
    contains_rapidride_ref,
    is_point_near_route_segments,
    get_rapidride_stop_name,
    get_rapidride_refs_from_stop_properties,
    simplify_rapidride_stop_label,
    fetch_all_geojson_features,
    collect_rapidride,
)


class GenerateMapHelperTests(unittest.TestCase):
    def test_fetch_all_geojson_features_paginates(self):
        responses = [
            {"features": [{"id": 1}, {"id": 2}]},
            {"features": [{"id": 3}]},
        ]

        with patch("generate_map.fetch_geojson", side_effect=responses) as mocked_fetch:
            features = fetch_all_geojson_features("https://example.test/layer", params={"f": "geojson"}, page_size=2)

        self.assertEqual(features, [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(mocked_fetch.call_count, 2)
        self.assertEqual(mocked_fetch.call_args_list[0].kwargs["params"]["resultOffset"], 0)
        self.assertEqual(mocked_fetch.call_args_list[1].kwargs["params"]["resultOffset"], 2)

    def test_collect_rapidride_includes_e_stop_from_paginated_results(self):
        route_segments = []
        stop_points = {}

        rapidride_lines = {
            "features": [
                {
                    "properties": {"ROUTE_NUM": 675},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [-122.3500, 47.6200],
                            [-122.3490, 47.6210],
                        ],
                    },
                }
            ]
        }

        paged_stops = [
            [
                {
                    "properties": {"STOP_ID": "s1", "STOP_NAME": "Unrelated", "ROUTE_LIST": "44 70"},
                    "geometry": {"type": "Point", "coordinates": [-122.2000, 47.7000]},
                }
            ],
            [
                {
                    "properties": {"STOP_ID": "e1", "STOP_NAME": "Aurora Ave N & N 46th St", "ROUTE_LIST": "E 5"},
                    "geometry": {"type": "Point", "coordinates": [-122.3491, 47.6209]},
                }
            ],
        ]

        with patch("generate_map.fetch_geojson", return_value=rapidride_lines), patch(
            "generate_map.fetch_all_geojson_features", return_value=[stop for page in paged_stops for stop in page]
        ):
            collect_rapidride(route_segments, stop_points)

        self.assertIn("e1", stop_points)
        self.assertEqual(stop_points["e1"]["line_key"], "rapidride_e")

    def test_collect_rapidride_clusters_nearby_same_name_stops(self):
        route_segments = []
        stop_points = {}

        rapidride_lines = {
            "features": [
                {
                    "properties": {"ROUTE_NUM": 675},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [-122.3500, 47.6200],
                            [-122.3490, 47.6210],
                        ],
                    },
                }
            ]
        }

        rapidride_stops = [
            {
                "properties": {"STOP_ID": "e1", "STOP_NAME": "N 46th St", "ROUTE_LIST": "E"},
                "geometry": {"type": "Point", "coordinates": [-122.34990, 47.62010]},
            },
            {
                "properties": {"STOP_ID": "e2", "STOP_NAME": "N 46th St", "ROUTE_LIST": "E"},
                "geometry": {"type": "Point", "coordinates": [-122.34995, 47.62015]},
            },
        ]

        with patch("generate_map.fetch_geojson", return_value=rapidride_lines), patch(
            "generate_map.fetch_all_geojson_features", return_value=rapidride_stops
        ):
            collect_rapidride(route_segments, stop_points)

        self.assertEqual(len(stop_points), 1)
        self.assertIn("e1", stop_points)

    def test_collect_rapidride_clusters_transitive_nearby_same_name_stops(self):
        route_segments = []
        stop_points = {}

        rapidride_lines = {
            "features": [
                {
                    "properties": {"ROUTE_NUM": 675},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [-122.3500, 47.6200],
                            [-122.3490, 47.6210],
                        ],
                    },
                }
            ]
        }

        # ~15m between consecutive points, but ~30m between first and third.
        # This should still collapse into one cluster through transitive proximity.
        rapidride_stops = [
            {
                "properties": {"STOP_ID": "e1", "STOP_NAME": "N 46th St", "ROUTE_LIST": "E"},
                "geometry": {"type": "Point", "coordinates": [-122.34990, 47.62010]},
            },
            {
                "properties": {"STOP_ID": "e2", "STOP_NAME": "N 46th St", "ROUTE_LIST": "E"},
                "geometry": {"type": "Point", "coordinates": [-122.34975, 47.62010]},
            },
            {
                "properties": {"STOP_ID": "e3", "STOP_NAME": "N 46th St", "ROUTE_LIST": "E"},
                "geometry": {"type": "Point", "coordinates": [-122.34960, 47.62010]},
            },
        ]

        with patch("generate_map.fetch_geojson", return_value=rapidride_lines), patch(
            "generate_map.fetch_all_geojson_features", return_value=rapidride_stops
        ):
            collect_rapidride(route_segments, stop_points)

        self.assertEqual(len(stop_points), 1)
        self.assertIn("e1", stop_points)

    def test_collect_rapidride_clusters_nearby_directional_stops_with_different_names(self):
        route_segments = []
        stop_points = {}

        rapidride_lines = {
            "features": [
                {
                    "properties": {"ROUTE_NUM": 675},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [-122.3499, 47.6200],
                            [-122.3497, 47.6202],
                        ],
                    },
                }
            ]
        }

        rapidride_stops = [
            {
                "properties": {
                    "STOP_ID": "e1",
                    "STOP_NAME": "N 46th St Inbound",
                    "ROUTE_LIST": "E",
                },
                "geometry": {"type": "Point", "coordinates": [-122.34982, 47.62010]},
            },
            {
                "properties": {
                    "STOP_ID": "e2",
                    "STOP_NAME": "N 46th St Outbound",
                    "ROUTE_LIST": "E",
                },
                "geometry": {"type": "Point", "coordinates": [-122.34978, 47.62013]},
            },
        ]

        with patch("generate_map.fetch_geojson", return_value=rapidride_lines), patch(
            "generate_map.fetch_all_geojson_features", return_value=rapidride_stops
        ):
            collect_rapidride(route_segments, stop_points)

        self.assertEqual(len(stop_points), 1)
        self.assertIn("e1", stop_points)
        merged_lat, merged_lon = stop_points["e1"]["coords"]
        self.assertAlmostEqual(merged_lat, (47.62010 + 47.62013) / 2, places=8)
        self.assertAlmostEqual(merged_lon, (-122.34982 + -122.34978) / 2, places=8)
        self.assertEqual(stop_points["e1"]["name"], "46th St Inbound")

    def test_collect_rapidride_clusters_nearby_different_cross_streets_into_combined_name(self):
        route_segments = []
        stop_points = {}

        rapidride_lines = {
            "features": [
                {
                    "properties": {"ROUTE_NUM": 675},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [-122.3800, 47.6500],
                            [-122.3600, 47.6600],
                        ],
                    },
                }
            ]
        }

        rapidride_stops = [
            {
                "properties": {
                    "STOP_ID": "e1",
                    "STOP_NAME": "Aurora Ave N",
                    "ROUTE_LIST": "E",
                    "ON_STREET_NAME": "Aurora Ave N",
                    "CROSS_STREET_NAME": "W Armory Way",
                },
                "geometry": {"type": "Point", "coordinates": [-122.37000, 47.65000]},
            },
            {
                "properties": {
                    "STOP_ID": "e2",
                    "STOP_NAME": "Aurora Ave N",
                    "ROUTE_LIST": "E",
                    "ON_STREET_NAME": "Aurora Ave N",
                    "CROSS_STREET_NAME": "W Newton St",
                },
                "geometry": {"type": "Point", "coordinates": [-122.37000, 47.65130]},
            },
        ]

        with patch("generate_map.fetch_geojson", return_value=rapidride_lines), patch(
            "generate_map.fetch_all_geojson_features", return_value=rapidride_stops
        ):
            collect_rapidride(route_segments, stop_points)

        self.assertEqual(len(stop_points), 1)
        self.assertIn("e1", stop_points)
        self.assertEqual(stop_points["e1"]["name"], "Armory / Newton")

    def test_collect_rapidride_keeps_same_name_stops_when_far_apart(self):
        route_segments = []
        stop_points = {}

        rapidride_lines = {
            "features": [
                {
                    "properties": {"ROUTE_NUM": 675},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [-122.3600, 47.6100],
                            [-122.3300, 47.6400],
                        ],
                    },
                }
            ]
        }

        rapidride_stops = [
            {
                "properties": {"STOP_ID": "e1", "STOP_NAME": "Aurora Ave N", "ROUTE_LIST": "E"},
                "geometry": {"type": "Point", "coordinates": [-122.34990, 47.62010]},
            },
            {
                "properties": {"STOP_ID": "e2", "STOP_NAME": "Aurora Ave N", "ROUTE_LIST": "E"},
                "geometry": {"type": "Point", "coordinates": [-122.34000, 47.63000]},
            },
        ]

        with patch("generate_map.fetch_geojson", return_value=rapidride_lines), patch(
            "generate_map.fetch_all_geojson_features", return_value=rapidride_stops
        ):
            collect_rapidride(route_segments, stop_points)

        self.assertEqual(len(stop_points), 2)
        self.assertIn("e1", stop_points)
        self.assertIn("e2", stop_points)

    def test_excludes_tacoma_stop_by_link_type(self):
        props = {"LINK_TYPE": 2, "STATION": "Anything"}
        self.assertTrue(is_excluded_light_rail_stop(props))

    def test_excludes_tacoma_stop_by_station_prefix(self):
        props = {"LINK_TYPE": 1, "STATION": "Tacoma Dome"}
        self.assertTrue(is_excluded_light_rail_stop(props))

    def test_excludes_airport_terminal_train_text(self):
        props = {"LINK_TYPE": 1, "DESCRIPTIO": "Sea Underground Satellite Transit System"}
        self.assertTrue(is_excluded_light_rail_stop(props))

    def test_keeps_non_excluded_light_rail_stop(self):
        props = {"LINK_TYPE": 1, "STATION": "Westlake", "DESCRIPTIO": "Central Link"}
        self.assertFalse(is_excluded_light_rail_stop(props))

    def test_detects_line2_stop_by_link_type(self):
        props = {"LINK_TYPE": 7}
        self.assertTrue(is_line2_light_rail_stop(props))

    def test_detects_line2_stop_by_segment_text(self):
        props = {"LINK_TYPE": 1, "SEGMENT": "East Link Extension"}
        self.assertTrue(is_line2_light_rail_stop(props))

    def test_line2_detection_false_for_line1_station(self):
        props = {"LINK_TYPE": 1, "STATION": "Capitol Hill"}
        self.assertFalse(is_line2_light_rail_stop(props))

    def test_normalize_streetcar_stop_name_removes_direction_suffix(self):
        self.assertEqual(normalize_streetcar_stop_name("Broadway & Marion Inbound"), "Broadway & Marion")
        self.assertEqual(normalize_streetcar_stop_name("Broadway & Marion Outbound"), "Broadway & Marion")

    def test_to_streetcar_line_key_maps_known_lines(self):
        self.assertEqual(to_streetcar_line_key("First Hill Alignment"), "streetcar_first_hill")
        self.assertEqual(to_streetcar_line_key("South Lake Union"), "streetcar_south_lake_union")
        self.assertEqual(to_streetcar_line_key("Seattle Streetcar"), "")

    def test_nearest_line_key_for_point_with_threshold_and_without(self):
        segments = [
            {"line_key": "streetcar_first_hill", "coords": [[47.6000, -122.3200]]},
            {"line_key": "streetcar_south_lake_union", "coords": [[47.6200, -122.3400]]},
        ]

        # Too far for default threshold
        self.assertIsNone(nearest_line_key_for_point(47.6100, -122.3300, segments, threshold=0.0001))
        # Global nearest fallback should still choose the nearest line
        self.assertEqual(
            nearest_line_key_for_point(47.6180, -122.3380, segments, threshold=None),
            "streetcar_south_lake_union",
        )

    def test_contains_rapidride_ref_tokenization(self):
        self.assertTrue(contains_rapidride_ref("161 A"))
        self.assertTrue(contains_rapidride_ref("44;E;70"))
        self.assertTrue(contains_rapidride_ref("D/F"))
        self.assertFalse(contains_rapidride_ref("44 70 271"))

    def test_get_rapidride_refs_from_stop_properties(self):
        self.assertEqual(
            get_rapidride_refs_from_stop_properties({"ROUTE_LIST": "1 125 C D E"}),
            ["C", "D", "E"],
        )
        self.assertEqual(
            get_rapidride_refs_from_stop_properties({"ROUTE_LIST": "28E 5"}),
            [],
        )

    def test_get_rapidride_stop_name_prefers_primary_fields(self):
        self.assertEqual(
            get_rapidride_stop_name({"STOP_NAME": "Aurora Ave N & N 46th St", "ON_STREET_NAME": "Aurora Ave N"}),
            "46th",
        )

    def test_get_rapidride_stop_name_prefers_intersection_when_primary_matches_corridor(self):
        self.assertEqual(
            get_rapidride_stop_name(
                {
                    "STOP_NAME": "Aurora Ave N",
                    "ON_STREET_NAME": "Aurora Ave N",
                    "CROSS_STREET_NAME": "N 46th St",
                }
            ),
            "46th",
        )

    def test_get_rapidride_stop_name_uses_alternate_cross_street_fields(self):
        self.assertEqual(
            get_rapidride_stop_name(
                {
                    "STOP_NAME": "Aurora Ave N",
                    "ON_STREET_NAME": "Aurora Ave N",
                    "HASTUS_CROSS_STREET_NAME": "N 46th St",
                }
            ),
            "46th",
        )

    def test_get_rapidride_stop_name_falls_back_to_intersection_then_stop_id(self):
        self.assertEqual(
            get_rapidride_stop_name({"ON_STREET_NAME": "Aurora Ave N", "CROSS_STREET_NAME": "N 46th St"}),
            "46th",
        )
        self.assertEqual(get_rapidride_stop_name({"STOP_ID": 12345}), "Stop 12345")

    def test_simplify_rapidride_stop_label_removes_cardinal_and_street_type(self):
        self.assertEqual(simplify_rapidride_stop_label("NW Leary Way"), "Leary")
        self.assertEqual(simplify_rapidride_stop_label("27th Ave E / 28th Ave E"), "27th / 28th")

    def test_is_point_near_route_segments_threshold_behavior(self):
        segments = [{"coords": [[47.6000, -122.3300]]}]
        self.assertTrue(is_point_near_route_segments(47.6005, -122.3305, segments, threshold=0.0012))
        self.assertFalse(is_point_near_route_segments(47.6040, -122.3340, segments, threshold=0.0012))

    def test_collect_rapidride_excludes_nearby_non_rapidride_stop_without_explicit_ref(self):
        route_segments = []
        stop_points = {}

        rapidride_lines = {
            "features": [
                {
                    "properties": {"ROUTE_NUM": 675},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [-122.3500, 47.6200],
                            [-122.3490, 47.6210],
                        ],
                    },
                }
            ]
        }

        rapidride_stops = [
            {
                "properties": {"STOP_ID": "e1", "STOP_NAME": "Aurora Ave N & N 46th St", "ROUTE_LIST": "E"},
                "geometry": {"type": "Point", "coordinates": [-122.3491, 47.6209]},
            },
            {
                "properties": {"STOP_ID": "n1", "STOP_NAME": "Near but not RapidRide", "ROUTE_LIST": "5 28E"},
                "geometry": {"type": "Point", "coordinates": [-122.3492, 47.6208]},
            },
        ]

        with patch("generate_map.fetch_geojson", return_value=rapidride_lines), patch(
            "generate_map.fetch_all_geojson_features", return_value=rapidride_stops
        ):
            collect_rapidride(route_segments, stop_points)

        self.assertIn("e1", stop_points)
        self.assertNotIn("n1", stop_points)


if __name__ == "__main__":
    unittest.main()
