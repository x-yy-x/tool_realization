# -*- coding: utf-8 -*-
"""L1 扫描层的命令行入口。

    py -m src.l1_scan --root <源码树> --out filelists.json
    py -m src.l1_scan --root <源码树> --stats            # 只打印统计，不写文件
    py -m src.l1_scan --root <源码树> --no-content       # 只按名字+路径，不读内容

退出码：0 成功；1 参数错误；2 扫描后契约校验失败（正常不应发生）。
"""

from __future__ import annotations

import argparse
import json
import sys

from ..common.models import ContractError, save_filelists
from .treemodel import scan


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="py -m src.l1_scan",
        description="扫描源码树，产出 filelists.json（path + classes）。",
    )
    ap.add_argument("--root", required=True, help="源码树根目录")
    ap.add_argument("--out", default=None, help="输出 filelists.json 的路径")
    ap.add_argument("--no-content", action="store_true",
                    help="不读文件内容，只用名字与路径判定")
    ap.add_argument("--stats", action="store_true", help="把统计打到 stderr")
    ap.add_argument("--stats-json", default=None, help="把统计写成 JSON")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    routes, stats = scan(args.root, use_content=not args.no_content)

    if args.out:
        try:
            save_filelists(args.out, routes)
        except (OSError, ContractError) as exc:
            print(f"写出失败: {exc}", file=sys.stderr)
            return 2
        print(f"{len(routes)} 条 -> {args.out}", file=sys.stderr)

    if args.stats:
        print(stats.summary(), file=sys.stderr)

    if args.stats_json:
        with open(args.stats_json, "w", encoding="utf-8") as fh:
            json.dump(stats.to_dict(), fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
