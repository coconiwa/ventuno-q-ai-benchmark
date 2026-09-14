import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class BenchmarkHelpersTest(unittest.TestCase):
    def test_percentile_interpolates(self):
        self.assertEqual(app.percentile([1, 2, 3], 0.5), 2)
        self.assertAlmostEqual(app.percentile([1, 2], 0.95), 1.95)

    def test_normalize_image_is_exact_geometry_and_jpeg(self):
        raw = io.BytesIO()
        Image.new("RGB", (1200, 600), "navy").save(raw, "PNG")
        normalized = app.normalize_image(raw.getvalue())
        self.assertTrue(normalized.startswith(b"\xff\xd8"))
        with Image.open(io.BytesIO(normalized)) as image:
            self.assertEqual(image.size, (640, 480))
            self.assertEqual(image.mode, "RGB")

    def test_active_model_is_fixed_to_2b(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with mock.patch.object(app, "BASE", base):
                self.assertEqual(app.active_model(), "2b")
                (base / "runtime").mkdir()
                (base / "runtime" / "active-model").write_text("unknown\n")
                self.assertEqual(app.active_model(), "2b")

    def test_model_status_exposes_public_profile(self):
        with mock.patch.object(app, "active_model", return_value="2b"):
            status = app.model_status()
        self.assertEqual(status["active"], "2b")
        self.assertEqual({item["id"] for item in status["options"]}, {"2b"})

    def test_language_defaults_to_japanese(self):
        self.assertEqual(app.normalize_language("en"), "en")
        self.assertEqual(app.normalize_language("invalid"), "ja")
        self.assertIn("English", app.PROMPTS["en"])
        self.assertIn("日本語", app.PROMPTS["ja"])

    def test_history_is_summarized_per_language(self):
        rows = [
            {"language": "ja", "cpu": {"total_s": 2, "prefill_s": 1, "decode_tps": 4}, "npu": {"total_s": 1, "prefill_s": .5, "decode_tps": 8}},
            {"language": "en", "cpu": {"total_s": 4, "prefill_s": 2, "decode_tps": 5}, "npu": {"total_s": 2, "prefill_s": 1, "decode_tps": 10}},
        ]
        with mock.patch.object(app, "HISTORY", rows), mock.patch.object(app, "RUN_STATS", {"total_runs": {"ja": 3, "en": 7}}):
            english = app.history_summary("en")
        self.assertEqual(english["language"], "en")
        self.assertEqual(english["runs"], 7)
        self.assertEqual(english["window_runs"], 1)
        self.assertEqual(english["cpu"]["total_s"]["p50"], 4)

    def test_recycle_required_uses_memory_threshold(self):
        with mock.patch.object(app, "active_model", return_value="2b"), mock.patch.object(app, "RECYCLE_GB", 9.0):
            self.assertFalse(app.recycle_required({"memory_used_gb": 8.9}))
            self.assertTrue(app.recycle_required({"memory_used_gb": 9.0}))
            self.assertFalse(app.recycle_required({"memory_used_gb": None}))

if __name__ == "__main__":
    unittest.main()
