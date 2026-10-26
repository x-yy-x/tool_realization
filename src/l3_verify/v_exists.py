"""第 2 关：存在性验证。

设计见 `src/l3_verify/README.md` §20.1（三态）。

原语：`command -v` / `test -f` / `test -d`。
**三态而不是布尔**：存在 / 不存在 / 存在但不可执行。
最后一态的修复动作（`chmod`）和「没装」（`apt install`）完全不同。

本验证器只处理这些 `kind`：
    tool     -> `command -v`
    repo     -> `test -d <source_root>/<name>`
    lib      -> `$CC -print-file-name=<name>`
    header   -> 编译器头文件搜索路径里找
    config   -> `test -f <source_root>/<name>`

其余 kind（component / arch / gap / toolchain / semantic / version / symbol）
归别的验证器或静态判定，本模块**不产出**它们的 finding——不做「假装验过」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from src.common import probe as probe_mod
from src.common.config import Config
from src.common.models import Finding, Requirement, make_finding

GATE = "g2"
HANDLES = frozenset({"tool", "repo", "lib", "header", "config"})

# include 搜索路径在 `cc -E -v -` 里的边界标记
_INCLUDE_BEGIN = "#include <...> search starts here:"
_INCLUDE_END = "End of search list."


def verify_all(
    reqs: Iterable[Requirement],
    *,
    config: Config | None = None,
    probe_module=probe_mod,
    start_seq: int = 1,
) -> list[Finding]:
    """按 kind 路由；不属于本关的需求静默跳过（由编排层统计）。"""
    cfg = config or Config()
    out: list[Finding] = []
    seq = start_seq
    for req in reqs:
        if req.kind not in HANDLES:
            continue
        finding = verify(req, config=cfg, probe_module=probe_module, seq=seq)
        out.append(finding)
        seq += 1
    return out


def verify(
    req: Requirement,
    *,
    config: Config | None = None,
    probe_module=probe_mod,
    seq: int = 1,
) -> Finding:
    cfg = config or Config()
    handler = {
        "tool": _tool,
        "repo": _path_dir,
        "config": _path_file,
        "lib": _lib,
        "header": _header,
    }[req.kind]
    result, evidence, exit_code, severity = handler(req, cfg, probe_module)
    return make_finding(
        req, seq=seq, gate=GATE, result=result, evidence=evidence,
        exit_code=exit_code, severity=severity,
    )


# --------------------------------------------------------------------------- #
# 各 kind
# --------------------------------------------------------------------------- #

def _tool(req: Requirement, cfg: Config, probe_module):
    path, r = probe_module.command_v(req.name)
    if path is None:
        return "missing", r.evidence(), _rc(r), None
    if not probe_module.is_executable(path):
        return (
            "mismatch",
            f"{r.evidence()}\n解析到 {path}，但该文件没有执行位（not-executable）",
            _rc(r),
            "block",
        )
    return "ok", f"{r.evidence()}\n解析到 {path}", _rc(r), None


def _path_dir(req: Requirement, cfg: Config, probe_module):
    return _path_check(req, cfg, probe_module, want_dir=True)


def _path_file(req: Requirement, cfg: Config, probe_module):
    return _path_check(req, cfg, probe_module, want_dir=False)


def _path_check(req: Requirement, cfg: Config, probe_module, *, want_dir: bool):
    if not cfg.source_root:
        return (
            "skip",
            "config.source_root 为空，无法做路径存在性检查",
            None, "info",
        )
    target = Path(cfg.source_root) / req.name
    exists = target.is_dir() if want_dir else target.is_file()
    kind = "test -d" if want_dir else "test -f"
    evidence = f"$ {kind} {target}\nexit={0 if exists else 1}"
    return ("ok" if exists else "missing"), evidence, 0 if exists else 1, None


def _lib(req: Requirement, cfg: Config, probe_module):
    cc = cfg.compiler()
    r = probe_module.run([cc, f"-print-file-name={req.name}"], timeout=cfg.timeout("cmd"))
    if not r.ok:
        return "skip", f"{r.evidence()}\n无法调用编译器 {cc}", _rc(r), "info"

    resolved = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
    # gcc/clang 找不到库时原样回显库名，这是它们的约定
    if not resolved or resolved == req.name:
        return "missing", f"{r.evidence()}\n编译器未找到（原样回显）", _rc(r), None
    if not Path(resolved).is_file():
        return "missing", f"{r.evidence()}\n回显路径不存在: {resolved}", _rc(r), None
    return "ok", f"{r.evidence()}\n解析到 {resolved}", _rc(r), None


def _header(req: Requirement, cfg: Config, probe_module):
    cc = cfg.compiler()
    dirs, r = include_dirs(cc, cfg, probe_module)
    if dirs is None:
        return "skip", f"{r.evidence()}\n无法读取编译器头文件搜索路径", _rc(r), "info"

    hits = [str(d / req.name) for d in dirs if (d / req.name).is_file()]
    if hits:
        return "ok", f"{r.evidence()}\n找到 {hits[0]}", _rc(r), None
    searched = "\n".join(f"    {d}" for d in dirs)
    return (
        "missing",
        f"{r.evidence()}\n在 {len(dirs)} 个搜索路径里都没有 {req.name}:\n{searched}",
        _rc(r), None,
    )


def include_dirs(cc: str, cfg: Config, probe_module):
    """`$CC -E -v -` 的 include 搜索路径。失败返回 (None, RunResult)。

    第 3 关的 v_identity 也用它（三元素之③）。
    """
    r = probe_module.run([cc, "-E", "-v", "-"], timeout=cfg.timeout("cmd"))
    if not r.ok:
        return None, r
    lines = (r.stdout + "\n" + r.stderr).splitlines()
    dirs: list[Path] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped == _INCLUDE_BEGIN:
            inside = True
            continue
        if inside and stripped == _INCLUDE_END:
            break
        if inside and stripped.startswith("/"):
            dirs.append(Path(stripped.split(" (")[0].strip()))
    if not dirs:
        return None, r
    return dirs, r


def _rc(r) -> int | None:
    return None if r.timed_out or r.error else r.returncode
