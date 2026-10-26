"""第 3 关：身份验证——「在的那个，是对的的那个吗」。

设计见 `src/l3_verify/README.md` §20.2（三元素）。

    $CC -dumpmachine                      ① 身份：目标三元组对不对
    readlink -f "$(command -v $CC)"       ② 真身：是不是 PATH 里另一个同名货
    $CC -E -v -                           ③ 头文件搜索路径对不对

典型场景：`arm-linux-gnueabi-gcc` 和 `arm-none-eabi-gcc` 都存在，
一个只能编用户态、一个只能编裸机。**只查存在性会漏。**

期望三元组从哪来
----------------
满足任一即可：
1. `config.target_triple`（显式配置，最可靠）
2. 编译器名里自带的三元组：`arm-none-eabi-gcc` -> `arm-none-eabi`
3. 两者都没有 -> 返回 `unknown`，并在证据里写明「无从判定」——
   **不猜**，因为猜错会把一个错误的工具链判成 OK。
"""

from __future__ import annotations

from typing import Iterable

from src.common import probe as probe_mod
from src.common.config import Config
from src.common.models import Finding, Requirement, make_finding
from src.l3_verify.v_exists import include_dirs

GATE = "g3"
HANDLES = frozenset({"toolchain"})

_SUFFIXES = ("-gcc", "-g++", "-clang", "-cc", "-c++", "-ld")


def verify_all(
    reqs: Iterable[Requirement],
    *,
    config: Config | None = None,
    probe_module=probe_mod,
    start_seq: int = 1,
) -> list[Finding]:
    cfg = config or Config()
    out: list[Finding] = []
    seq = start_seq
    for req in reqs:
        if req.kind not in HANDLES:
            continue
        out.append(verify(req, config=cfg, probe_module=probe_module, seq=seq))
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
    name = req.name

    path, found = probe_module.command_v(name)
    if path is None:
        return make_finding(req, seq=seq, gate=GATE, result="missing",
                            evidence=found.evidence(), exit_code=_rc(found))

    machine, dump = _dumpmachine(name, cfg, probe_module)
    real = probe_module.real_path(path)
    dirs, inc = include_dirs(name, cfg, probe_module)

    expected = expected_triple(name, cfg)
    parts = [
        found.evidence(),
        dump.evidence(),
        f"$ readlink -f {path}\n    {real}",
        inc.evidence() if dirs is not None else f"{inc.evidence()}\n（头文件搜索路径读取失败）",
    ]
    evidence = "\n".join(parts)

    if machine is None:
        return make_finding(req, seq=seq, gate=GATE, result="unknown",
                            evidence=evidence + "\n-dumpmachine 没有给出可判读的输出",
                            exit_code=_rc(dump))
    if not expected:
        return make_finding(
            req, seq=seq, gate=GATE, result="unknown",
            evidence=evidence + f"\n-dumpmachine = {machine}；"
                                "但没有 config.target_triple，也没有可从编译器名推断的三元组",
            exit_code=_rc(dump),
        )
    if machine != expected:
        return make_finding(
            req, seq=seq, gate=GATE, result="mismatch",
            evidence=evidence + f"\n期望 {expected}，实际 {machine}",
            exit_code=_rc(dump), severity="block",
        )
    return make_finding(req, seq=seq, gate=GATE, result="ok",
                        evidence=evidence + f"\n-dumpmachine = {machine}",
                        exit_code=_rc(dump))


def expected_triple(name: str, config: Config) -> str:
    """显式配置优先；否则从编译器名剥后缀；都没有返回空串。"""
    if config.target_triple:
        return config.target_triple
    stem = name
    for suffix in _SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem if "-" in stem else ""


def _dumpmachine(name: str, cfg: Config, probe_module):
    r = probe_module.run([name, "-dumpmachine"], timeout=cfg.timeout("cmd"))
    if not r.ok:
        return None, r
    lines = r.stdout.strip().splitlines()
    return (lines[0].strip() if lines else None), r


def _rc(r) -> int | None:
    return None if r.timed_out or r.error else r.returncode
