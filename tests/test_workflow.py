"""Tests use temporary archives only, never the real Desktop or medical content."""

import contextlib
from datetime import date, timedelta
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib


SCRIPT = Path(__file__).resolve().parents[1] / "skills/doctor-title/scripts/workflow.py"
SPEC = importlib.util.spec_from_file_location("workflow", SCRIPT)
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


def draft():
    return {
        "title": "排尿后仍有尿意？先记录伴随变化，不要急着自己猜",
        "category": "prostate", "summary": "排尿后仍感尿意的读者记录伴随表现，了解就诊沟通边界。",
        "features": ["排尿后尿意", "记录伴随表现", "不凭症状猜病", "就诊沟通边界"],
        "article": "这是一份仅用于软件测试的文本，不作为医学建议发布。排尿后仍有尿意时，先把出现情境和伴随变化记录清楚，不能只凭一种感觉自行判断原因。持续不适请就医评估。",
        "banner_text": ["排尿后仍有尿意？", "先记录伴随变化"],
        "palette": "米白暖橙", "image_tool": "no image tool (software test)",
        "image_prompt": "横版16:9医疗科普大字Banner，目标1600x900，允许宽1500-1700、高800-1000像素，米白暖橙配色，大字两行，准确文字：排尿后仍有尿意？先记录伴随变化。原创抽象圆环元素，不使用人物或第三方素材。",
        "image_status": "skipped", "skip_reason": "软件测试未调用生图工具。",
        "sources": [{"title": "Test fixture, not a verified medical source", "url": "https://example.org/test", "claim": "Only a software validation fixture"}],
        "loaded_skills": list(w.SKILLS),
        "review": {"medical": True, "compliance": True, "originality": True, "semantic": True, "visual": True},
    }


def png(width=1600, height=900):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\xff\xff" * width) * height)) + chunk(b"IEND", b""))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="doctor-skills-test-")
        self.root = Path(self.temp.name) / "doctorlist"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_daily_threshold_and_cap(self):
        start = date(2026, 1, 1)
        for days, expected in ((0, .2), (1, .202), (99, .398), (100, .4), (400, .4)):
            result = w.memory(self.root, start + timedelta(days=days))
            self.assertEqual(result["threshold"], expected)
            self.assertEqual(bool(result["notice"]), days >= 100)
            self.assertEqual(result["state"]["started_on"], start.isoformat())

    def test_clock_rollback_is_error(self):
        w.memory(self.root, date(2026, 1, 2))
        with self.assertRaisesRegex(ValueError, "clock"):
            w.memory(self.root, date(2026, 1, 1))

    def test_archive_skipped_image_and_read_history(self):
        value = draft()
        result = w.archive(self.root, value, w.memory(self.root), self.root)
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["files"]["image"])
        self.assertEqual(list((self.root / "img").iterdir()), [])
        body = (self.root / result["files"]["article"]).read_text(encoding="utf-8")
        self.assertTrue(body.startswith("# " + value["title"]))
        manifest = w.load_json(self.root / result["files"]["manifest"])
        self.assertEqual(manifest["image_prompt"], value["image_prompt"])
        self.assertEqual(manifest["skip_reason"], value["skip_reason"])
        memory = w.memory(self.root)
        self.assertEqual(len(memory["history"]), 1)
        self.assertNotIn("article", memory["history"][0])
        self.assertFalse(w.similarity(value, memory)["ok"])

    def test_generated_png_archive(self):
        image = self.root / "source.png"
        image.write_bytes(png())
        value = draft()
        value.update(image_status="generated", image_path=str(image))
        result = w.archive(self.root, value, w.memory(self.root), self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.root / result["files"]["image"]).read_bytes(), image.read_bytes())
        self.assertEqual(Path(result["files"]["image"]).stem, Path(result["files"]["article"]).stem)
        self.assertEqual(len(w.memory(self.root)["history"]), 1)

    def test_dimensions_within_tolerance_are_accepted(self):
        for width, height in ((1500, 800), (1500, 1000), (1700, 800), (1700, 1000), (1601, 901)):
            with self.subTest(width=width, height=height):
                root = self.root / f"{width}x{height}"
                root.mkdir()
                image = root / "source.png"
                image.write_bytes(png(width, height))
                value = draft()
                value.update(image_status="generated", image_path=str(image))
                result = w.archive(root, value, w.memory(root), root)
                self.assertTrue(result["ok"], result)
                manifest = w.load_json(root / result["files"]["manifest"])
                self.assertEqual(manifest["dimensions"], {"width": width, "height": height})
                self.assertEqual((root / result["files"]["image"]).read_bytes(), image.read_bytes())

    def test_dimensions_outside_tolerance_do_not_commit(self):
        for width, height in ((1499, 900), (1701, 900), (1600, 799), (1600, 1001), (1280, 720), (1920, 1080)):
            with self.subTest(width=width, height=height):
                image = self.root / "source.png"
                image.write_bytes(png(width, height))
                value = draft()
                value.update(image_status="generated", image_path=str(image))
                with self.assertRaisesRegex(ValueError, "1500-1700"):
                    w.archive(self.root, value, w.memory(self.root), self.root)
                self.assertFalse(w.memory(self.root)["history"])
                self.assertFalse((self.root / "title").exists())

    def test_bad_png_checksum_rejected(self):
        data = bytearray(png())
        data[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "checksum"):
            w.image_info(bytes(data))

    def test_fake_images_rejected(self):
        for data in (b"fake image", png()[:-12], b"RIFF" + b"\x00" * 4 + b"WEBP"):
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                w.image_info(data)

    def test_png_requires_decompressible_pixels(self):
        data = png()
        start = data.index(b"IDAT") - 4
        payload = b"not-zlib-pixels"
        bad = data[:start] + struct.pack(">I", len(payload)) + b"IDAT" + payload + struct.pack(">I", zlib.crc32(b"IDAT" + payload) & 0xffffffff) + data[-12:]
        with self.assertRaisesRegex(ValueError, "decompressed"):
            w.image_info(bad)

    def test_jpeg_header_without_scan_rejected(self):
        frame = b"\x08" + struct.pack(">HH", 720, 1280) + b"\x01"
        data = b"\xff\xd8\xff\xc0" + struct.pack(">H", len(frame) + 2) + frame + b"\xff\xd9"
        with self.assertRaises(ValueError):
            w.image_info(data)

    def test_empty_content_cannot_poison_memory(self):
        value = draft()
        value["article"] = w.DISCLAIMER + "!" * 60
        self.assertTrue(w.content_errors(value, final=True))
        value = draft()
        value["title"] = "!!!"
        self.assertTrue(w.content_errors(value))

    def test_compliance_rules(self):
        for wording in ("据了解", "百分百治愈", "彻底根治", "绝对安全无副作用", "最好", "有效率达到80%", "价格1000元",
                        "我们医院比XX医院治疗效果好很多", "患者送的锦旗", "知名专家强力推荐", "西地那非", "治疗仪", "三个月就恢复",
                        "百分之八十", "价格一千元", "价格１０００元"):
            with self.subTest(wording=wording):
                value = draft()
                value["title"] = wording
                self.assertTrue(w.content_errors(value))

    def test_numbered_sections_allowed(self):
        value = draft()
        value["title"] = "这3个记录要点，让就医沟通更清楚"
        self.assertFalse(w.content_errors(value), w.content_errors(value))

    def test_required_final_fields(self):
        for key in ("article", "image_prompt", "banner_text", "palette", "image_tool", "skip_reason", "sources", "loaded_skills", "review"):
            with self.subTest(key=key):
                value = draft()
                value.pop(key)
                self.assertFalse(w.archive(self.root, value, w.memory(self.root), self.root)["ok"])
                self.assertFalse(w.memory(self.root)["history"])

    def test_invalid_types_rejected(self):
        for key, bad in (("features", [None] * 4), ("loaded_skills", [{}]), ("review", []), ("article", 2), ("sources", [None]), ("title", "a\nb")):
            with self.subTest(key=key):
                value = draft()
                value[key] = bad
                self.assertTrue(w.content_errors(value, final=True))

    def test_exact_title_and_normalized_synonym_rejected(self):
        value = draft()
        w.archive(self.root, value, w.memory(self.root), self.root)
        value["title"] = value["title"].replace("排尿", "小便").replace("？", "!")
        report = w.similarity(value, w.memory(self.root))
        self.assertFalse(report["ok"])
        self.assertTrue(report["nearest"][0]["same_title"])

    def test_sketch_and_threshold_boundary(self):
        self.assertEqual(w.overlap(["a", "b"], ["b", "c"]), 1 / 3)
        hashes = [f"{i:016x}" for i in range(128)]
        self.assertEqual(w.overlap(hashes, hashes, sketch=True), 1.0)
        value = draft()
        current = w.fingerprints(value)
        old = {"id": "one", "title": "other", "normalized_title": "other", "summary": "other", "fingerprints": current}
        ctx = {"threshold": 1.0, "notice": None, "history": [old]}
        self.assertTrue(w.similarity(value, ctx)["ok"])
        ctx["threshold"] = .99999999
        self.assertFalse(w.similarity(value, ctx)["ok"])

    def test_windows_safe_stems(self):
        self.assertEqual(w.safe_stem("CON"), "_CON")
        self.assertEqual(w.safe_stem('a/b\\c:d?e*"f<g>|.'), "a_b_c_d_e__f_g__")
        self.assertLess(len(w.safe_stem("题" * 160).encode("utf-16-le")), 160)

    def test_collision_is_not_overwritten(self):
        (self.root / "title").mkdir()
        (self.root / "title/A_B.md").write_text("existing", encoding="utf-8")
        stem = w.available_stem(self.root, "A?B")
        self.assertNotEqual(stem, "A_B")
        self.assertEqual((self.root / "title/A_B.md").read_text(encoding="utf-8"), "existing")

    def test_corrupt_or_missing_memory_fails_closed(self):
        w.memory(self.root)
        (self.root / "memory/history.jsonl").write_text("bad json\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            w.memory(self.root)
        (self.root / "memory/history.jsonl").unlink()
        with self.assertRaisesRegex(ValueError, "History is missing"):
            w.memory(self.root)

    def test_missing_state_not_reset(self):
        w.memory(self.root)
        (self.root / "memory/state.json").unlink()
        with self.assertRaisesRegex(ValueError, "refusing"):
            w.memory(self.root)

    def test_lost_history_record_detected(self):
        w.archive(self.root, draft(), w.memory(self.root), self.root)
        (self.root / "memory/history.jsonl").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            w.memory(self.root)

    def test_corrupt_nested_history_returns_json(self):
        w.archive(self.root, draft(), w.memory(self.root), self.root)
        original = w.memory(self.root)["history"][0]
        for key, bad in (("fingerprints", 12), ("files", []), ("files", {"article": 1}), ("id", []), ("features", 12)):
            with self.subTest(key=key, bad=bad):
                broken = {**original, key: bad}
                (self.root / "memory/history.jsonl").write_text(json.dumps(broken) + "\n", encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = w.main(["context", "--output-root", str(self.root)])
                self.assertEqual(status, 1)
                self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_concurrent_lock_and_pending_transaction(self):
        with w.locked(self.root):
            with self.assertRaisesRegex(ValueError, "locked"):
                with w.locked(self.root):
                    pass
        self.assertFalse((self.root / ".workflow.lock").exists())
        w.memory(self.root)
        (self.root / "memory/pending.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            w.memory(self.root)

    def test_failed_archive_rolls_back(self):
        context = w.memory(self.root)
        original = w.atomic_text

        def fail_history(path, text):
            if path.name == "history.jsonl":
                raise OSError("Simulated disk failure")
            original(path, text)

        with patch.object(w, "atomic_text", side_effect=fail_history):
            with self.assertRaisesRegex(OSError, "Simulated"):
                w.archive(self.root, draft(), context, self.root)
        self.assertEqual(list((self.root / "title").iterdir()), [])
        self.assertFalse(w.memory(self.root)["history"])

    def test_discovery_and_missing_dependencies(self):
        self.assertTrue(w.discover([SCRIPT.parents[4]])["ok"])
        with patch.object(w.Path, "is_file", return_value=False):
            result = w.discover([])
            self.assertFalse(result["ok"])
            self.assertEqual(set(result["missing"]), set(w.SKILLS))

    def test_draft_check_does_not_write_history(self):
        path = self.root / "draft.json"
        value = draft()
        value.pop("article")
        path.write_text(w.json_text(value), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = w.main(["check", "--output-root", str(self.root), "--draft", str(path)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertFalse(w.memory(self.root)["history"])

    @unittest.skipUnless(w.os.name == "nt", "Windows only")
    def test_real_desktop_resolution_is_read_only(self):
        result = w.desktop_root()
        self.assertEqual(result.name, "doctorlist")
        self.assertTrue(result.parent.is_dir())


if __name__ == "__main__":
    unittest.main()
