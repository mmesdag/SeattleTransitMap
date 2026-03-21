import re
import unittest
from pathlib import Path

import generate_map as map_module


class GenerateMapIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.index_html = cls.project_root / "index.html"

        map_module.generate_map()
        cls.generated_html = cls.index_html.read_text(encoding="utf-8")

    def _count_circle_markers_with_popup(self, html, popup_text):
        escaped_text = re.escape(popup_text)
        pattern = (
            r"var\s+circle_marker_[\w]+\s*=\s*L\.circleMarker\(.*?"
            r"\.bindTooltip\(\s*`<div>\s*"
            + escaped_text
            + r"\s*</div>`"
        )
        return len(re.findall(pattern, html, flags=re.DOTALL))

    def test_generate_map_deduplicates_nearby_rapidride_stop_pair_in_html(self):
        self.assertGreaterEqual(self._count_circle_markers_with_popup(self.generated_html, "46th"), 1)
        self.assertNotIn("N 46th St", self.generated_html)

    def test_generate_map_includes_streetcar_stop_in_html(self):
        self.assertIn("mode-streetcar", self.generated_html)
        self.assertIn('id="toggle-stop-names"', self.generated_html)
        self.assertIn("transit-stop-label", self.generated_html)
        self.assertIn("showStopNames", self.generated_html)

    def test_generate_map_contains_real_streetcar_stop_markers(self):
        self.assertGreaterEqual(
            self._count_circle_markers_with_popup(self.generated_html, "Broadway & Marion"),
            1,
        )
        self.assertGreaterEqual(
            self._count_circle_markers_with_popup(self.generated_html, "Westlake & Mercer"),
            1,
        )

    def test_generate_map_includes_url_filter_state_hooks(self):
        self.assertIn("function readStateFromUrl()", self.generated_html)
        self.assertIn("function writeStateToUrl()", self.generated_html)
        self.assertIn("new URLSearchParams(window.location.search", self.generated_html)
        self.assertIn("window.history.replaceState", self.generated_html)
        self.assertIn("params.set('categories'", self.generated_html)
        self.assertIn("params.set('lines'", self.generated_html)
        self.assertIn("params.set('stop_names'", self.generated_html)
        self.assertIn("applyStateFromUrl();", self.generated_html)

    def test_generate_map_deduplicates_nearby_rapidride_galer_stop_pair_in_html(self):
        self.assertGreaterEqual(self._count_circle_markers_with_popup(self.generated_html, "Galer"), 1)
        self.assertNotIn("Galer St", self.generated_html)
        self.assertNotIn("Aurora Ave N  & Galer St", self.generated_html)

    def test_generate_map_deduplicates_nearby_rapidride_dravus_stop_pair_in_html(self):
        self.assertEqual(
            self._count_circle_markers_with_popup(self.generated_html, "Dravus"),
            1,
        )

    def test_generate_map_combines_armory_and_newton_into_single_stop(self):
        self.assertEqual(
            self._count_circle_markers_with_popup(self.generated_html, "Armory / Newton"),
            1,
        )

    def test_generate_map_simplifies_leary_direction_and_street_type(self):
        self.assertGreaterEqual(self._count_circle_markers_with_popup(self.generated_html, "Leary"), 1)
        self.assertNotIn("NW Leary Way", self.generated_html)


if __name__ == "__main__":
    unittest.main()
