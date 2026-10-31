# -*- coding: utf-8 -*-
"""L1 扫描层：源码树 → `filelists.json`。

对外只需要 `scan()`：

    from src.l1_scan import scan, save_filelists
    routes, stats = scan(r"D:\\oh")
    save_filelists("filelists.json", routes)

命令行用法见 `python -m src.l1_scan --help`。
"""

from __future__ import annotations

from ..common.models import FileRoute, save_filelists, load_filelists
from .treemodel import ScanStats, classify, iter_files, scan

__all__ = [
    "FileRoute",
    "ScanStats",
    "classify",
    "iter_files",
    "load_filelists",
    "save_filelists",
    "scan",
]
