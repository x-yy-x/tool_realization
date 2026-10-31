# -*- coding: utf-8 -*-
"""在多棵真实源码树上跑 L1，输出对照表，并逐棵校验 filelists.json 契约。

用法（在 tool/ 下）：

    py tests/tools/scan_matrix.py \
        --tree ohos=/path/to/ohos \
        --tree poky=/path/to/poky \
        --tree meson=/path/to/meson

输出列：文件数、路由数、读内容数、耗时、schema 校验结果。
不写任何文件到被测树；临时产物落在系统临时目录，跑完即删。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.models import save_filelists          # noqa: E402
from src.l1_scan import scan                          # noqa: E402

SCHEMA_PATH = ROOT / "schema" / "filelists.schema.json"


def _validator():
    try:
        import jsonschema
    except ImportError:
        return None
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def _parse_tree(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--tree 需要 NAME=PATH 形式")
    name, path = value.split("=", 1)
    return name, path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跨树跑 L1 并校验契约")
    ap.add_argument("--tree", action="append", type=_parse_tree, required=True,
                    metavar="NAME=PATH")
    ap.add_argument("--no-content", action="store_true")
    args = ap.parse_args(argv)

    validator = _validator()
    print(f"{'tree':<12}{'files':>8}{'routed':>8}{'read':>7}{'ms':>7}  schema")

    failed = 0
    for name, root in args.tree:
        if not Path(root).is_dir():
            print(f"{name:<12}  (missing: {root})")
            failed += 1
            continue
        routes, stats = scan(root, use_content=not args.no_content)

        status = "skipped(no jsonschema)"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "filelists.json"
            save_filelists(out, routes)
            data = json.loads(out.read_text(encoding="utf-8"))

        paths = [r["path"] for r in data]
        problems = []
        if paths != sorted(paths):
            problems.append("未排序")
        if len(paths) != len(set(paths)):
            problems.append("路径重复")
        if validator is not None:
            errors = list(validator.iter_errors(data))
            if errors:
                problems.append(f"{len(errors)} 条 schema 错误: {errors[0].message}")
            else:
                status = "OK"
        if problems:
            status = "FAIL:" + "; ".join(problems)
            failed += 1

        print(f"{name:<12}{stats.total_files:>8}{stats.routed:>8}"
              f"{stats.content_read:>7}{stats.elapsed_ms:>7}  {status}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
