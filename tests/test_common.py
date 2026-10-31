"""地基的最小测试：YAML 子集、配置、契约 dataclass。

跑法（在 tool/ 下）：
    py -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.common import yamlish
from src.common.config import Config
from src.common.models import (
    ContractError, Finding, Requirement, load_requirements, make_finding,
    save_findings,
)


class YamlishTest(unittest.TestCase):
    def test_nested_mapping_sequence_and_comments(self):
        text = """
        # 顶部注释
        source_root: /srv/oh
        excludes:
          - .git/
          - out/          # 行尾注释
        endpoints:
          - name: gitee
            url: "https://gitee.com/x#frag"
          - name: mirror
            url: https://m.example.com
        timeouts: {cmd: 30, http: 10}
        flag: true
        empty:
        """
        data = yamlish.load(text)
        self.assertEqual(data["source_root"], "/srv/oh")
        self.assertEqual(data["excludes"], [".git/", "out/"])
        self.assertEqual(data["endpoints"][0]["url"], "https://gitee.com/x#frag")
        self.assertEqual(data["endpoints"][1]["name"], "mirror")
        self.assertEqual(data["timeouts"], {"cmd": 30, "http": 10})
        self.assertIs(data["flag"], True)
        self.assertIsNone(data["empty"])

    def test_scalars(self):
        data = yamlish.load("a: 1\nb: 2.5\nc: false\nd: null\ne: 'x y'\n")
        self.assertEqual(data, {"a": 1, "b": 2.5, "c": False, "d": None, "e": "x y"})

    def test_bom_is_stripped(self):
        """PowerShell / 记事本保存的 UTF-8 BOM 不能污染第一个键。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.yaml"
            p.write_bytes((chr(0xFEFF) + "source_root: /x\n").encode("utf-8"))
            self.assertEqual(yamlish.load_file(p), {"source_root": "/x"})

    def test_block_scalar_is_rejected(self):
        with self.assertRaises(yamlish.YamlError):
            yamlish.load("a: |\n  line\n")


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.compiler(), "cc")
        self.assertIn("build/build.sh", cfg.sentinel_files)
        self.assertEqual(cfg.timeout("http"), 10.0)

    def test_excludes_key_is_gone(self):
        """排除目录已固定给 L1，配置里再写 excludes 只应得到一条警告。"""
        cfg = Config.from_mapping({"excludes": [".git/"]})
        self.assertTrue(any("excludes" in w for w in cfg.warnings))
        self.assertFalse(hasattr(cfg, "excludes"))

    def test_mapping_and_unknown_key(self):
        cfg = Config.from_mapping({
            "source_root": "/srv/oh",
            "cc": "aarch64-linux-gnu-gcc",
            "endpoints": [{"name": "e", "url": "https://x", "timeout": 3}],
            "surprise": 1,
        })
        self.assertEqual(cfg.source_root, "/srv/oh")
        self.assertEqual(cfg.compiler(), "aarch64-linux-gnu-gcc")
        self.assertEqual(cfg.endpoints[0].timeout, 3.0)
        self.assertTrue(any("surprise" in w for w in cfg.warnings))

    def test_missing_file_uses_defaults(self):
        cfg = Config.from_file(Path("does-not-exist.yaml"))
        self.assertEqual(cfg.source_root, "")
        self.assertTrue(cfg.warnings)


def _req(**over):
    base = dict(
        id="REQ-00001", kind="tool", name="mcopy",
        declared_at="a.sh:42", declared_as="mcopy -i x ::",
        extractor="S1-tool", confidence="high",
        check="CHK-EXISTS-CMD", severity="block",
    )
    base.update(over)
    return base


class ModelsTest(unittest.TestCase):
    def test_requirement_roundtrip(self):
        req = Requirement.from_dict(_req())
        self.assertEqual(req.to_dict()["name"], "mcopy")

    def test_contract_rejects_unknown_field(self):
        with self.assertRaises(ContractError):
            Requirement.from_dict(_req(extra=1))

    def test_contract_rejects_bad_kind(self):
        with self.assertRaises(ContractError):
            Requirement.from_dict(_req(kind="banana"))

    def test_load_and_save_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            req = Requirement.from_dict(_req())
            finding = make_finding(
                req, seq=7, gate="g3", result="missing",
                evidence="command -v mcopy -> exit 1", exit_code=1,
            )
            out = save_findings(tmp / "findings.json", [finding],
                                round=0, target_env="wsl2/ubuntu")
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["meta"]["round"], 0)
            self.assertEqual(data["meta"]["target_env"], "wsl2/ubuntu")
            self.assertEqual(data["findings"][0]["id"], "F-0007")
            self.assertEqual(data["findings"][0]["req"], "REQ-00001")
            self.assertEqual(data["findings"][0]["declared_at"], "a.sh:42")
            self.assertNotIn("fix_class", data["findings"][0])

    def test_load_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.json"
            path.write_text(json.dumps({
                "meta": {"source_root": "/srv/oh",
                         "scanned_at": "2026-10-17T10:00:00+08:00",
                         "tool_version": "0.1"},
                "requirements": [_req()],
            }), encoding="utf-8")
            meta, reqs = load_requirements(path)
            self.assertEqual(meta["source_root"], "/srv/oh")
            self.assertEqual(len(reqs), 1)


if __name__ == "__main__":
    unittest.main()
