# -*- coding: utf-8 -*-
"""L1 扫描：一棵源码树 → 一份带提取器标签的路由表。

对外的三个函数：

    scan(root)                全树遍历 + 分类，返回 (routes, stats)
    classify(rel, abs_path)   单个文件的判定（纯逻辑 + 可选的一次读头）
    iter_files(root)          只遍历，不分类

设计要点（`l1_scan/README.md`）：

- **遍历只做一次。** 递归 `os.scandir`，目录原地剪枝；L2 不再走树。
- **默认不读内容。** 只有「名字层认不出」或「后缀有歧义」的文件才读前 2 KB。
  实测只读 18% 的文件，全树扫描仍是秒级。
- **路径一律正斜杠。** 输出的 `path` 直接进 `filelists.json`，契约禁止反斜杠。
- **结果是确定的。** 同一棵树跑两次，输出逐字节相同（按 path 排序）。
"""

from __future__ import annotations

import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from ..common.models import FileRoute
from . import rules

#: 读内容时先读多少字节，不够再扩到多少。实测 97.7% 的文件 2 KB 内进正文。
INITIAL_BYTES = 2048
HARD_CAP_BYTES = 8192

#: 注释行前缀（bytes）。跳注释头时用。
_COMMENT_PREFIX = (b"#", b"//", b"/*", b"*", b"--", b";", b"<!--", b"%")


@dataclass
class ScanStats:
    """扫描过程的计量。**不写进 filelists.json**（契约冻结），只用于报告与诊断。"""

    total_files: int = 0
    routed: int = 0
    excluded_dirs: int = 0
    content_read: int = 0
    by_category: Counter = field(default_factory=Counter)
    by_source: Counter = field(default_factory=Counter)
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "routed": self.routed,
            "excluded_dirs": self.excluded_dirs,
            "content_read": self.content_read,
            "by_category": dict(self.by_category.most_common()),
            "by_source": dict(self.by_source.most_common()),
            "elapsed_ms": self.elapsed_ms,
        }

    def summary(self) -> str:
        d = self.to_dict()
        lines = [
            f"文件 {d['total_files']}，路由 {d['routed']}，"
            f"读内容 {d['content_read']}，剪掉目录 {d['excluded_dirs']}，耗时 {d['elapsed_ms']} ms",
        ]
        if d["total_files"]:
            lines.append(f"路由率 {d['routed'] / d['total_files']:.1%}")
        for cat, n in d["by_category"].items():
            lines.append(f"  {cat} {rules.CATEGORY_NAMES[cat]:<18}{n:>7}")
        for src, n in d["by_source"].items():
            lines.append(f"  依据 {src:<10}{n:>7}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 遍历
# --------------------------------------------------------------------------- #

def _normalize_excludes(excludes: Sequence[str]) -> tuple[frozenset[str], tuple[str, ...]]:
    """把 `[".git/", "out/", "a/b/"]` 拆成（任意深度的目录名, 相对前缀）。"""
    names: set[str] = set()
    prefixes: list[str] = []
    for raw in excludes:
        s = str(raw).strip().replace("\\", "/").strip("/")
        if not s:
            continue
        if "/" in s:
            prefixes.append(s + "/")
        else:
            names.add(s)
    return frozenset(names), tuple(prefixes)


def iter_files(
    root: str | os.PathLike[str],
    *,
    stats: "ScanStats | None" = None,
) -> Iterator[tuple[str, str]]:
    """递归产出 (相对路径, 绝对路径)，相对路径统一用正斜杠。

    目录剪枝用固定规则 `rules.EXCLUDED_DIRS`，不读配置。
    不跟随符号链接，天然避开环。传入 `stats` 时会累计被剪掉的目录数。
    """
    root = os.fspath(root)
    dir_names, dir_prefixes = _normalize_excludes(rules.EXCLUDED_DIRS)
    stack: list[str] = [""]

    while stack:
        rel_dir = stack.pop()
        abs_dir = os.path.join(root, rel_dir) if rel_dir else root
        try:
            with os.scandir(abs_dir) as it:
                entries = list(it)
        except OSError:
            continue

        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in dir_names or any(
                        (rel + "/").startswith(p) for p in dir_prefixes
                    ):
                        if stats is not None:
                            stats.excluded_dirs += 1
                        continue
                    stack.append(rel)
                elif entry.is_file(follow_symlinks=False):
                    yield rel, entry.path
            except OSError:
                continue


# --------------------------------------------------------------------------- #
# 读头
# --------------------------------------------------------------------------- #

def _first_effective_line(data: bytes) -> tuple[int | None, int]:
    """返回 (行号, 字节偏移)：第一个非空、非注释的行。整段都是注释时行号为 None。"""
    offset = 0
    lineno = 0
    for raw in data.splitlines(keepends=True):
        lineno += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith(_COMMENT_PREFIX):
            offset += len(raw)
            continue
        return lineno, offset
    return None, offset


def read_head(abs_path: str) -> tuple[bytes | None, int]:
    """读文件头，返回 (head, 第一个有效行的字节偏移)。

    head 最多 `HARD_CAP_BYTES` 字节；二进制、读失败时返回 (None, 0)。
    整段都是注释时偏移等于 head 长度（正文为空）。
    """
    try:
        with open(abs_path, "rb") as fh:
            head = fh.read(HARD_CAP_BYTES)
    except OSError:
        return None, 0
    if not head or b"\x00" in head[:4096]:
        return None, 0
    _, offset = _first_effective_line(head)
    return head, offset


def body_of(head: bytes, offset: int, window: int = INITIAL_BYTES) -> bytes:
    """从 head 里切出「跳过注释头之后、窗口长度内」的正文。"""
    return head[offset:offset + window]


def _ext_of(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1].lower()
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


# --------------------------------------------------------------------------- #
# 分类
# --------------------------------------------------------------------------- #

def classify(
    rel: str,
    abs_path: str,
    *,
    use_content: bool = True,
    stats: ScanStats | None = None,
) -> tuple[str | None, str]:
    """判定一个文件的大类，返回 (大类, 依据)。

    依据 ∈ {exact, prefix, suffix, path, content, none}。
    名字层的 exact / prefix 是强判据，内容层不会推翻；suffix 与 path 可以被内容纠正。
    """
    detail = rules.match_name_detail(rel)
    category: str | None = None
    source = "none"

    if detail is not None:
        category, source = detail

    if category is None:
        path_hit = rules.match_path(rel)
        if path_hit is not None:
            category, source = path_hit, "path"

    if use_content and rules.needs_content(rel, category):
        if stats is not None:
            stats.content_read += 1
        head, offset = read_head(abs_path)
        if head is not None and head.startswith(b"#!"):
            # shebang 本身是注释行，必须在跳注释之前判
            if source not in ("exact", "prefix"):
                category, source = "C1", "content"
        elif head is not None and offset < len(head):
            ext = _ext_of(rel)
            hit = rules.sniff_content(body_of(head, offset), ext)
            if hit is None and len(head) - offset > INITIAL_BYTES:
                # 2 KB 内没命中：同一份 head 里扩到 4 KB 再试一次，不重复读盘
                hit = rules.sniff_content(body_of(head, offset, 4096), ext)
            if hit is not None and source not in ("exact", "prefix"):
                category, source = hit[0], "content"

    return category, source


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def scan(
    root: str | os.PathLike[str],
    *,
    use_content: bool = True,
) -> tuple[list[FileRoute], ScanStats]:
    """全树扫描，返回 (路由表, 统计)。

    目录剪枝固定用 `rules.EXCLUDED_DIRS`（`.git/ .repo/ out/ prebuilts/`），
    不接受外部传入——排除规则属于工具，不属于某个工程。
    只收录至少有一个提取器的文件；`other` 不进结果。
    """
    root = os.fspath(root)
    stats = ScanStats()
    started = time.perf_counter()
    routes: list[FileRoute] = []

    for rel, abs_path in iter_files(root, stats=stats):
        stats.total_files += 1
        category, source = classify(rel, abs_path, use_content=use_content, stats=stats)
        stats.by_source[source] += 1
        if category is None:
            continue
        stats.by_category[category] += 1
        stats.routed += 1
        routes.append(FileRoute(rel, rules.classes_for(category, rel)))

    routes.sort(key=lambda r: r.path)
    stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return routes, stats
