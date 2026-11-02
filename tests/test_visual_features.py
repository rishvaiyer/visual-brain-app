"""Visual lookup should find a changed view without sending or saving the frame."""

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

import visual_features


class VisualFeatureTests(unittest.TestCase):
    def test_changed_view_prefers_the_same_saved_object(self):
        with tempfile.TemporaryDirectory() as temporary:
            orange = Image.new("RGB", (180, 180), "#efece3")
            draw = ImageDraw.Draw(orange)
            draw.ellipse((35, 24, 145, 155), fill="#d56d35")
            draw.rectangle((78, 12, 102, 40), fill="#765329")
            blue = Image.new("RGB", (180, 180), "#e7eff4")
            ImageDraw.Draw(blue).rectangle((20, 25, 160, 160), fill="#20578d")
            orange_path = Path(temporary) / "orange.png"
            blue_path = Path(temporary) / "blue.png"
            orange.save(orange_path)
            blue.save(blue_path)
            frame = orange.rotate(8, fillcolor="#efece3")
            output = BytesIO()
            frame.save(output, "PNG")
            found = visual_features.rank(visual_features.byte_features(output.getvalue()),
                                         [{"id": "orange", "path": str(orange_path)},
                                          {"id": "blue", "path": str(blue_path)}])
            self.assertEqual(found[0]["id"], "orange")
            self.assertGreater(found[0]["similarity"], found[1]["similarity"])

    def test_taste_groups_use_saved_tags_without_inventing_attribution(self):
        with tempfile.TemporaryDirectory() as temporary:
            rows = []
            for index in range(12):
                path = Path(temporary) / f"{index}.png"
                Image.new("RGB", (40, 40), "#d27a42" if index < 6 else "#246c9b").save(path)
                rows.append({"id": str(index), "path": str(path),
                             "collection": "", "tags": ["Ceramics"] if index < 6 else ["Water"]})
            groups = visual_features.taste_groups(rows)
            self.assertEqual({group["title"] for group in groups}, {"Ceramics", "Water"})
            self.assertEqual(sum(group["count"] for group in groups), 12)


if __name__ == "__main__":
    unittest.main()
