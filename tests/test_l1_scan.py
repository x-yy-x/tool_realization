# -*- coding: utf-8 -*-
"""L1 扫描层测试。

跑法（在 tool/ 下）：
    py -m unittest tests.test_l1_scan -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.common.models import ContractError, FileRoute, load_filelists, save_filelists
from src.l1_scan import rules
from src.l1_scan.treemodel import classify, scan

TOOL = Path(__file__).resolve().parents[1]
FIXTURE = TOOL / "tests" / "fixture"


def _write(root: Path, rel: str, content: bytes = b"x\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


class NameRulesTest(unittest.TestCase):
    """第 1 层：只按名字判。"""

    CASES = {
        # 完整文件名纠正后缀
        "BoardConfig.mk": ("C4", "exact"),
        "setup.py": ("C3", "exact"),
        "config.gni": ("C4", "exact"),
        "rtconfig.py": ("C4", "exact"),
        "bundle.json": ("C3", "exact"),
        "defconfig": ("C4", "exact"),
        "ld.script": ("C5", "exact"),
        "Makefile": ("C2", "exact"),
        # 前缀
        "Kconfig": ("C7", "prefix"),
        "Kconfig.debug": ("C7", "prefix"),
        "Kconfig.defconfig": ("C7", "prefix"),
        "Config.in": ("C7", "exact"),
        "Config.in.legacy": ("C7", "prefix"),
        "config.in": None,          # Autotools 生成的头文件模板，不是 Kconfig
        # 后缀（大小写不敏感）
        "a.c": ("C6", "suffix"),
        "a.S": ("C6", "suffix"),
        "a.C": ("C6", "suffix"),
        "BUILD.gn": ("C2", "exact"),
        "x.gni": ("C2", "suffix"),
        "core_portme.mak": ("C2", "suffix"),
        "configure.ac": ("C2", "suffix"),
        "Makefile.am": ("C2", "suffix"),
        "Makefile.in": ("C2", "exact"),
        "foo.lds": ("C5", "suffix"),
        "bar.hcs": ("C8", "suffix"),
        "board.dtso": ("C8", "suffix"),
        "baz.para": ("C4", "suffix"),
        "y.config": ("C4", "suffix"),
        "arm64_defconfig": ("C4", "suffix"),
        "subprojects/foo.wrap": ("C3", "suffix"),
        # 认不出
        "LICENSE": None,
        "a.rs": None,
        ".gitignore": None,
    }

    def test_name_layer(self):
        for name, expected in self.CASES.items():
            with self.subTest(name=name):
                self.assertEqual(rules.match_name_detail(name), expected)

    def test_path_layer(self):
        self.assertEqual(rules.match_path("pkg/debian/control"), "C3")
        self.assertIsNone(rules.match_path("src/control"))
        self.assertEqual(
            rules.match_path("productdefine/common/inherit/2in1.json"), "C3"
        )
        # Yocto：conf/ 下的 .conf 是全局构建配置
        self.assertEqual(rules.match_path("meta/conf/bitbake.conf"), "C4")
        self.assertEqual(rules.match_path("meta/conf/machine-sdk/aarch64.conf"), "C4")
        # Debian 的 rules 是 Makefile，不是普通脚本
        self.assertEqual(rules.match_path("pkg/debian/rules"), "C2")
        # 运行时配置不在 conf/ 下，不该被这条规则抓走
        self.assertIsNone(rules.match_path("files/syslog.conf"))

    def test_extra_classes(self):
        # Yocto recipe 既是构建描述又是包清单
        self.assertEqual(
            rules.classes_for("C2", "meta/recipes-core/busybox/busybox.bb"),
            ("S1", "S3", "S4", "S5", "S7"),
        )
        # 全局 distro include 不是包清单，不给 S3
        self.assertEqual(
            rules.classes_for("C2", "meta/conf/distro/include/default-distrovars.inc"),
            ("S1", "S4", "S5", "S7"),
        )
        # Android.bp 同理
        self.assertIn("S3", rules.classes_for("C2", "frameworks/base/Android.bp"))
        # NuttX 的 board.h 既是头文件又是板级配置
        self.assertEqual(
            rules.classes_for("C6", "boards/x/include/board.h"),
            ("S4", "S6", "S7"),
        )

    def test_classes_match_schema_examples(self):
        # schema 的两条示例必须能被规则复现
        self.assertEqual(rules.classes_for("C3"), ("S3", "S6"))
        self.assertEqual(rules.classes_for("C2"), ("S1", "S4", "S5", "S7"))

    def test_all_categories_have_classes(self):
        for cat in rules.CATEGORY_NAMES:
            self.assertTrue(rules.classes_for(cat), f"{cat} 没有提取器")
            self.assertEqual(
                list(rules.classes_for(cat)),
                sorted(rules.classes_for(cat), key=rules.CLASS_ORDER.index),
            )


class ContractTest(unittest.TestCase):
    """filelists.json 的契约（schema 已冻结）。"""

    def test_valid(self):
        r = FileRoute("base/hilog/bundle.json", ("S3", "S6"))
        self.assertEqual(r.to_dict(), {"path": "base/hilog/bundle.json",
                                       "classes": ["S3", "S6"]})

    def test_rejects(self):
        bad = [
            ("", ("S1",)),
            ("/a", ("S1",)),
            ("a\\b", ("S1",)),
            ("a//b", ("S1",)),
            ("./a", ("S1",)),
            ("a/../b", ("S1",)),
            ("a/", ("S1",)),
            ("C:/a", ("S1",)),
            ("~/a", ("S1",)),
            ("a", ()),
            ("a", ("S1", "S1")),
            ("a", ("S2",)),
            ("a", ("S4", "S1")),
        ]
        for path, classes in bad:
            with self.subTest(path=path, classes=classes):
                with self.assertRaises(ContractError):
                    FileRoute(path, classes)

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "filelists.json"
            routes = [FileRoute("a/b.c", ("S6",)), FileRoute("x/BUILD.gn", ("S1", "S4"))]
            save_filelists(out, routes)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")),
                             [r.to_dict() for r in routes])
            self.assertEqual(load_filelists(out), routes)

    def test_load_rejects_extra_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "f.json"
            out.write_text('[{"path": "a", "classes": ["S1"], "kind": "gn"}]',
                           encoding="utf-8")
            with self.assertRaises(ContractError):
                load_filelists(out)


class ScanTest(unittest.TestCase):
    """端到端：临时小树 → 路由表。"""

    def test_basic_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "build/build.sh", b"#!/bin/sh\necho hi\n")
            _write(root, "src/a.cpp", b"#include <stdio.h>\n")
            _write(root, "src/a.h", b"#pragma once\n")
            _write(root, "board/BUILD.gn", b'executable("x") {}\n')
            _write(root, "board/config.gni", b'board_cpu = "cortex-a7"\n')
            _write(root, "vendor/x/config.json", b'{"product_name": "x"}\n')
            _write(root, "docs/readme.md", b"# hi\n")
            _write(root, ".git/HEAD", b"ref\n")

            routes, stats = scan(root)
            got = {r.path: r.classes for r in routes}

            self.assertEqual(got["build/build.sh"], ("S1", "S4", "S5"))
            self.assertEqual(got["src/a.cpp"], ("S6",))
            self.assertEqual(got["src/a.h"], ("S6",))
            self.assertEqual(got["board/BUILD.gn"], ("S1", "S4", "S5", "S7"))
            self.assertEqual(got["board/config.gni"], ("S3", "S4", "S7"))
            self.assertEqual(got["vendor/x/config.json"], ("S3", "S4", "S7"))
            self.assertNotIn("docs/readme.md", got)      # 文档不路由
            self.assertNotIn(".git/HEAD", got)           # 排除目录
            self.assertEqual([r.path for r in routes], sorted(got))

    def test_content_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "dev/device_info.hcs",
                   b"root {\n  device_info {\n    match_attr = \"hdf_manager\";\n")
            _write(root, "etc/init.cfg",
                   b'{\n  "jobs": [{"name": "boot", "cmds": ["start x"]}]\n}\n')
            _write(root, "vendor/p/config.json", b'{"product_name": "p"}\n')
            _write(root, "misc/data.json", b'{"hello": 1}\n')
            # 普通 JSON 里出现 project( 的字符串，不能当 CMake 处理
            _write(root, "misc/fake.json", b'{"note": "run project(foo) first"}\n')
            _write(root, "misc/tool", b"#!/bin/bash\necho hi\n")     # 无后缀脚本
            _write(root, "misc/notes.txt", b"a = b\nc = d\n")        # 文档不读内容
            _write(root, "misc/blob.bin", b"\x00\x01\x02")

            routes, _ = scan(root)
            got = {r.path: r.classes for r in routes}

            self.assertEqual(got["dev/device_info.hcs"], ("S4", "S7"))
            self.assertEqual(got["etc/init.cfg"], ("S1", "S4", "S5"))
            self.assertEqual(got["vendor/p/config.json"], ("S3", "S4", "S7"))
            self.assertNotIn("misc/data.json", got)
            self.assertNotIn("misc/fake.json", got)
            self.assertEqual(got["misc/tool"], ("S1", "S4", "S5"))
            self.assertNotIn("misc/notes.txt", got)
            self.assertNotIn("misc/blob.bin", got)

    def test_no_content_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "dev/device_info.hcs", b"match_attr = \"hdf_manager\";\n")
            _write(root, "misc/tool", b"#!/bin/sh\n")
            routes, stats = scan(root, use_content=False)
            got = {r.path: r.classes for r in routes}
            self.assertIn("dev/device_info.hcs", got)   # 后缀已能判
            self.assertNotIn("misc/tool", got)          # 只能靠内容，未启用
            self.assertEqual(stats.content_read, 0)

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(30):
                _write(root, f"d{i % 3}/f{i}.c", b"int x;\n")
            first, _ = scan(root)
            second, _ = scan(root)
            self.assertEqual([r.to_dict() for r in first],
                             [r.to_dict() for r in second])

    def test_default_excludes(self):
        """目录剪枝是固定规则，不可配置：.git/.repo/out/prebuilts 一律不进。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "keep/a.c", b"int x;\n")
            _write(root, ".git/config", b"x\n")
            _write(root, ".repo/manifest.xml", b"<manifest/>\n")
            _write(root, "out/gen.c", b"int y;\n")
            _write(root, "prebuilts/p.h", b"int z;\n")

            routes, stats = scan(root)
            paths = {r.path for r in routes}

            self.assertIn("keep/a.c", paths)
            for hidden in (".git/config", ".repo/manifest.xml", "out/gen.c", "prebuilts/p.h"):
                self.assertNotIn(hidden, paths)
            self.assertEqual(stats.excluded_dirs, 4)


class CliTest(unittest.TestCase):
    """`scan.py --stage tree` 的端到端链路。"""

    def test_tree_stage_writes_contract_file(self):
        import scan as entry

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "src"
            root.mkdir()
            _write(root, "a/BUILD.gn", b'executable("x") {}\n')
            _write(root, "a/b.c", b"int x;\n")
            _write(root, "a/README.md", b"# hi\n")
            out = Path(tmp) / "filelists.json"

            code = entry.main(["--stage", "tree", "--root", str(root), "--out", str(out)])
            self.assertEqual(code, 0)

            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(
                data,
                [
                    {"path": "a/BUILD.gn", "classes": ["S1", "S4", "S5", "S7"]},
                    {"path": "a/b.c", "classes": ["S6"]},
                ],
            )
            # 写出的文件必须能被契约模型原样读回
            self.assertEqual(load_filelists(out), [
                FileRoute("a/BUILD.gn", ("S1", "S4", "S5", "S7")),
                FileRoute("a/b.c", ("S6",)),
            ])

    def test_missing_root_returns_4(self):
        import scan as entry

        code = entry.main(["--stage", "tree", "--root", str(Path(tempfile.gettempdir()) / "no-such-tree-xyz")])
        self.assertEqual(code, 4)

    def test_verify_missing_requirements_returns_4(self):
        import scan as entry

        code = entry.main([
            "--stage", "verify",
            "--requirements", str(Path(tempfile.gettempdir()) / "no-such-req.json"),
            "--out", str(Path(tempfile.gettempdir()) / "no-such-out.json"),
        ])
        self.assertEqual(code, 4)


class FixtureTest(unittest.TestCase):
    """仓库自带的 fixture 假树。"""

    def test_fixture_tree(self):
        if not FIXTURE.is_dir():
            self.skipTest("tests/fixture 不存在")
        routes, stats = scan(FIXTURE)
        got = {r.path: r.classes for r in routes}

        self.assertEqual(got["build/build.sh"], ("S1", "S4", "S5"))
        self.assertEqual(got["device/demo/chip/mkimage.sh"], ("S1", "S4", "S5"))
        self.assertEqual(got["src/a.cpp"], ("S6",))
        self.assertEqual(got["third_party/foo/bundle.json"], ("S3", "S6"))
        self.assertEqual(got["vendor/demo/board/config.json"], ("S3", "S4", "S7"))
        self.assertNotIn(".repo/manifest.xml", got)   # .repo/ 被排除
        self.assertGreaterEqual(stats.total_files, 5)


if __name__ == "__main__":
    unittest.main()
