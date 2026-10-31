# -*- coding: utf-8 -*-
"""README 可用性测量（调研工具，不属于流水线）。

回答三个问题：
  1. 一棵源码树里，有多少目录/文件旁边有 README？
  2. README 里有多少条「路径 — 说明」能被机械抽出来？
  3. 抽出来的条目，有多少能在树里找到对应路径（找不到 = README 陈旧）。

用法：
    py -I tests/tools/readme_probe.py --root <源码树目录> [--json out.json]

判定口径（写进 docs/09 §33.6.4）：
  - 只认三种结构化写法：围栏内的树状图、Markdown 表格、`- path — 说明` 列表；
  - 树状图按「树根行 + 缩进层级」还原完整路径；
  - 某个树根下一条都对不上本地仓时，整段记为「跨仓，不可本地验证」；
  - 表格/列表没有目录上下文，只做全树后缀匹配，记为「弱验证」。
"""
import argparse
import json
import re
from pathlib import Path

SKIP_DIRS = {".git", ".repo", "__pycache__", "out", "node_modules"}
DOC_SUFFIX = {".md", ".txt", ".rst", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".ico"}
DOC_NAMES = {"license", "license.txt", "notice", ".gitignore", "owners"}

# 09 §33.2.2 的八个大类认可的名字/后缀；用不到它们的文件算「名字规则认不出」
KNOWN_SUFFIX = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl", ".s",
    ".sh", ".bash", ".zsh", ".py", ".pl", ".rb", ".bat", ".ps1",
    ".mk", ".gn", ".gni", ".cmake", ".bp", ".bb", ".bbappend", ".bbclass", ".inc",
    ".ld", ".lds", ".map", ".dts", ".dtsi", ".overlay", ".json", ".xml", ".yaml", ".yml",
    ".conf", ".in", ".cfg",
}
KNOWN_NAMES = {"makefile", "gnumakefile", "kbuild", "kconfig", "sconscript", "cmakelists.txt",
               "bundle.json", "ohos.build", "android.bp", "meson.build"}

NAME = r"[A-Za-z0-9_.][A-Za-z0-9_.\-]*"
PATHTOK = r"[A-Za-z0-9_][A-Za-z0-9_./\-]*"
DESC = r"(?:#+|[-—–]{2,}|:)\s*(?P<d>.+)"


def read_text(p):
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return ""


def is_readme(name):
    return name.lower().startswith("readme")


def fenced_blocks(text):
    blocks, cur, inside = [], [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if inside:
                blocks.append(cur)
                cur = []
            inside = not inside
            continue
        if inside:
            cur.append(line)
    return blocks


def parse_tree_block(block):
    """围栏内按「树根行 + 缩进」解析，返回 [(anchor, [(full_path, desc)])]。"""
    if not any(("├" in l or "└" in l) for l in block):
        return []
    sections, anchor, entries, stack = [], None, [], []

    def flush():
        nonlocal anchor, entries
        if entries:
            sections.append((anchor, entries))
        anchor, entries = None, []

    for line in block:
        if any(g in line for g in "├└│"):
            m = re.match(rf"^(?P<pre>[\s│├└─┬┼|]*)(?P<tok>{NAME})(?P<rest>.*)$", line)
            if not m:
                continue
            tok, rest, pre = m.group("tok"), m.group("rest"), m.group("pre")
            dm = re.search(DESC, rest)
            desc = dm.group("d").strip() if dm else ""
            depth = max(len(pre) // 4, 1)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            stack.append((depth, tok))
            if desc:
                entries.append(("/".join(n for _, n in stack), desc))
            continue
        m = re.match(rf"^\s*(?P<tok>/?{PATHTOK})/?\s*(?:{DESC})?$", line)
        if m:
            flush()
            anchor = m.group("tok").strip("/")
            stack = []
    flush()
    return sections


def parse_readme(p):
    text = read_text(p)
    tree, loose = [], []
    for block in fenced_blocks(text):
        for anchor, entries in parse_tree_block(block):
            tree.extend((full, desc, anchor) for full, desc in entries)
    for raw in text.splitlines():
        s = raw.rstrip()
        if s.strip().startswith("|") and s.strip().endswith("|"):
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0] and not set(cells[0]) <= set("-: "):
                if re.fullmatch(PATHTOK, cells[0].strip("`")) and cells[1]:
                    loose.append((cells[0].strip("`").rstrip("/"), cells[1], "table"))
            continue
        m = re.match(rf"^\s*[-*+]\s+`?(?P<tok>{PATHTOK})`?\s+{DESC}$", s)
        if m:
            loose.append((m.group("tok").rstrip("/"), m.group("d").strip(), "list"))
    return tree, loose


def find_local(full, relset):
    full = full.strip("/")
    if not full:
        return None
    if full in relset:
        return full
    cands = [r for r in relset if r.endswith("/" + full)]
    return min(cands, key=len) if cands else None


def probe_repo(repo):
    rels = []
    for p in repo.rglob("*"):
        if any(x in SKIP_DIRS for x in p.parts):
            continue
        rels.append(p.relative_to(repo).as_posix())
    relset = set(rels)
    dirs = [r for r in rels if (repo / r).is_dir()]
    files = [r for r in rels if (repo / r).is_file()]
    readmes = [r for r in files if is_readme(Path(r).name)]
    readme_dirs = {str(Path(r).parent.as_posix()) for r in readmes}

    local = weak = cross = unres = 0
    tree_local = tree_unres = 0
    described, parsed, pairs = set(), 0, []
    for r in readmes:
        tree, loose = parse_readme(repo / r)
        if tree or loose:
            parsed += 1

        by_anchor = {}
        for full, desc, anchor in tree:
            by_anchor.setdefault(anchor, []).append((full, desc))
        for anchor, items in by_anchor.items():
            hits = [(full, desc, find_local(full, relset)) for full, desc in items]
            any_hit = any(h for _, _, h in hits)
            for full, desc, hit in hits:
                if hit:
                    local += 1
                    tree_local += 1
                    if (repo / hit).is_dir():
                        described.add(hit)
                    pairs.append(dict(readme=r, form="tree", token=full, resolved=hit,
                                      desc=desc, kind="local"))
                elif any_hit:
                    unres += 1
                    tree_unres += 1
                    pairs.append(dict(readme=r, form="tree", token=full, resolved=None,
                                      desc=desc, kind="unresolved"))
                else:
                    cross += 1
                    pairs.append(dict(readme=r, form="tree", token=full, resolved=None,
                                      desc=desc, kind="cross", anchor=anchor))

        for tok, desc, form in loose:
            hit = find_local(tok, relset)
            if hit:
                weak += 1
                pairs.append(dict(readme=r, form=form, token=tok, resolved=hit,
                                  desc=desc, kind="weak"))
            else:
                unres += 1
                pairs.append(dict(readme=r, form=form, token=tok, resolved=None,
                                  desc=desc, kind="unresolved"))

    files_in_desc = [f for f in files if str(Path(f).parent.as_posix()) in described]
    gain = 0
    for f in files_in_desc:
        nm = Path(f).name.lower()
        if Path(f).suffix in DOC_SUFFIX or nm in DOC_NAMES or nm in KNOWN_NAMES:
            continue
        if Path(f).suffix not in KNOWN_SUFFIX:
            gain += 1

    return dict(repo=repo.name, dirs=len(dirs), files=len(files), readmes=len(readmes),
                readme_dirs=len(readme_dirs), parseable=parsed, local=local, weak=weak,
                cross=cross, unres=unres, tree_local=tree_local, tree_unres=tree_unres,
                described=len(described), files_in_desc=len(files_in_desc), gain=gain, pairs=pairs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="源码树或一批子仓的父目录")
    ap.add_argument("--json", default=None, help="把逐条配对写到这个文件")
    a = ap.parse_args()

    root = Path(a.root)
    rows = [probe_repo(d) for d in sorted(root.iterdir())
            if d.is_dir() and not d.name.startswith((".", "_"))]
    sum_keys = ("dirs", "files", "readmes", "readme_dirs", "parseable", "local", "weak",
                "cross", "unres", "tree_local", "tree_unres", "described", "files_in_desc", "gain")
    S = {k: sum(r[k] for r in rows) for k in sum_keys}
    denom = S["local"] + S["unres"]
    tdenom = S["tree_local"] + S["tree_unres"]

    print(f"仓库 {len(rows)}，目录 {S['dirs']}，文件 {S['files']}")
    print(f"README {S['readmes']}，所在目录 {S['readme_dirs']}")
    print(f"目录覆盖率 {S['readme_dirs']}/{S['dirs']} = {S['readme_dirs']/max(S['dirs'],1):.1%}")
    print(f"可解析率 {S['parseable']}/{S['readmes']} = {S['parseable']/max(S['readmes'],1):.1%}")
    print(f"配对：强验证 {S['local']} / 弱验证 {S['weak']} / 跨仓 {S['cross']} / 失败 {S['unres']}")
    print(f"  其中树状图：命中 {S['tree_local']} / 失败 {S['tree_unres']} → "
          f"结构准确率 {S['tree_local']/max(tdenom,1):.1%}")
    print(f"  （含表格/列表的总体准确率 {S['local']/max(denom,1):.1%}）")
    print(f"描述到的目录 {S['described']}，其下文件 {S['files_in_desc']}，增益 {S['gain']}")

    print(f"\n{'repo':<44}{'dirs':>5}{'Rdir':>6}{'files':>7}{'ok':>4}{'weak':>5}{'cross':>6}{'fail':>5}{'gain':>5}")
    for r in rows:
        print(f"{r['repo']:<44}{r['dirs']:>5}{r['readme_dirs']:>6}{r['files']:>7}{r['local']:>4}"
              f"{r['weak']:>5}{r['cross']:>6}{r['unres']:>5}{r['gain']:>5}")

    if a.json:
        flat = []
        for r in rows:
            for p in r["pairs"]:
                p = dict(p)
                p["repo"] = r["repo"]
                p["readme"] = str(p["readme"])
                flat.append(p)
        Path(a.json).write_text(json.dumps(flat, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n逐条配对 -> {a.json}")


if __name__ == "__main__":
    main()
