"""filelists.json 契约测试。

跑法（在 tool/ 下）：
    py -m unittest tests.test_schema_filelists -v
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    import jsonschema
except ImportError:  # 工具本体零依赖；没装 jsonschema 时跳过契约测试
    jsonschema = None

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "filelists.schema.json"
CLASS_ORDER = {"S1": 1, "S3": 2, "S4": 3, "S5": 4, "S6": 5, "S7": 6}

VALID = [
    {"path": "base/hiviewdfx/hilog/bundle.json", "classes": ["S3", "S6"]},
    {"path": "base/hiviewdfx/hilog/BUILD.gn", "classes": ["S1", "S4", "S5", "S7"]},
]

INVALID = {
    "missing classes": [{"path": "a"}],
    "extra field": [{"path": "a", "classes": ["S1"], "kind": "gn"}],
    "empty classes": [{"path": "a", "classes": []}],
    "duplicate classes": [{"path": "a", "classes": ["S1", "S1"]}],
    "S2 removed": [{"path": "a", "classes": ["S2"]}],
    "absolute path": [{"path": "/a", "classes": ["S1"]}],
    "backslash path": [{"path": "a\\b", "classes": ["S1"]}],
    "dot segment": [{"path": "./a", "classes": ["S1"]}],
    "dotdot segment": [{"path": "a/../b", "classes": ["S1"]}],
    "trailing slash": [{"path": "a/", "classes": ["S1"]}],
    "drive path": [{"path": "C:/a", "classes": ["S1"]}],
    "home path": [{"path": "~/a", "classes": ["S1"]}],
    "double slash": [{"path": "a//b", "classes": ["S1"]}],
}


@unittest.skipUnless(jsonschema is not None, "jsonschema not installed")
class FilelistsSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(cls.schema)
        cls.validator = jsonschema.Draft202012Validator(cls.schema)

    def assert_valid(self, data):
        errors = sorted(self.validator.iter_errors(data), key=lambda e: list(e.path))
        self.assertEqual([], [e.message for e in errors])

    def assert_invalid(self, data):
        errors = list(self.validator.iter_errors(data))
        self.assertTrue(errors, f"应当校验失败，但通过了: {data!r}")

    def test_schema_itself_and_examples(self):
        self.assert_valid([])
        self.assert_valid(VALID)
        for example in self.schema.get("examples", []):
            self.assert_valid(example)

    def test_invalid_records(self):
        for name, data in INVALID.items():
            with self.subTest(name=name):
                self.assert_invalid(data)

    def test_sorted_classes_and_unique_paths(self):
        for data in [VALID] + self.schema.get("examples", []):
            paths = [item["path"] for item in data]
            self.assertEqual(len(paths), len(set(paths)), "path 必须唯一")
            for item in data:
                classes = item["classes"]
                self.assertEqual(
                    sorted(classes, key=CLASS_ORDER.__getitem__),
                    classes,
                    "classes 必须按 S1,S3,S4,S5,S6,S7 升序",
                )


if __name__ == "__main__":
    unittest.main()
