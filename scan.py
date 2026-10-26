"""唯一入口。

    py scan.py --stage verify --config config.yaml

本版实现了 L3 的三个验证器（v_exists / v_identity / v_reach）与验证编排。
其余阶段（env / fetch / tree / extract / fix）属于别的层，尚未实现——
**遇到时明确报错并退出 3，不做假装跑通的空壳。**

退出码
------
0  跑完，且没有 block
2  跑完，有 block（仅在 --fail-fast 时作为失败）
3  该阶段未实现
4  配置或契约错误
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许 `py scan.py` 从任意目录运行
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.common import probe as probe_mod                  # noqa: E402
from src.common.config import Config, ConfigError          # noqa: E402
from src.common.models import (                            # noqa: E402
    ContractError, Finding, load_requirements, save_findings,
)
from src.l3_verify import v_exists, v_identity, v_reach    # noqa: E402

STAGES = ("env", "fetch", "tree", "extract", "verify", "fix")

# 本版 L3 覆盖的 kind；其余归 v_tree / v_consistency / L4，尚未实现
_VERIFIED_KINDS = set(v_exists.HANDLES) | set(v_identity.HANDLES)


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    args = _parser().parse_args(argv)

    if args.stage != "verify":
        print(
            f"[scan] 阶段 {args.stage!r} 尚未实现。\n"
            f"       本版只实现了 verify（v_exists / v_identity / v_reach）。\n"
            f"       其余阶段的代码属于 L1 / L2 / L4 / precheck，见各自的 README。",
            file=sys.stderr,
        )
        return 3

    try:
        config = Config.from_file(args.config)
        _, reqs = load_requirements(args.requirements)
    except (ConfigError, ContractError) as exc:
        print(f"[scan] 输入不可用: {exc}", file=sys.stderr)
        return 4

    for warning in config.warnings:
        print(f"[config] {warning}", file=sys.stderr)

    findings, skipped = run_verify(reqs, config)

    out = Path(args.out)
    save_findings(out, findings, round=args.round, target_env=args.target_env)

    _report(findings, skipped, out)
    blocks = sum(1 for f in findings if f.severity == "block" and f.result != "ok")
    if blocks and args.fail_fast:
        print(f"[scan] {blocks} 条 block，--fail-fast 生效", file=sys.stderr)
        return 2
    return 0


def run_verify(
    reqs, config: Config, probe_module=probe_mod
) -> tuple[list[Finding], dict[str, int]]:
    """按关卡顺序跑本版实现的三个验证器。"""
    findings: list[Finding] = []

    # 第 2 关：存在性
    exists_findings = v_exists.verify_all(
        reqs, config=config, probe_module=probe_module, start_seq=1
    )
    findings.extend(exists_findings)

    # 第 3 关：身份（只对 toolchain）
    identity_findings = v_identity.verify_all(
        reqs, config=config, probe_module=probe_module,
        start_seq=len(findings) + 1,
    )
    findings.extend(identity_findings)

    # 第 5 关：可达性——输入是第 2 关的**缺失集**，不是全部 repo
    missing_repos = [
        req for req in reqs
        if req.kind == "repo" and _is_missing(req, exists_findings)
    ]
    findings.extend(v_reach.verify_all(
        missing_repos, config=config, probe_module=probe_module,
        start_seq=len(findings) + 1,
    ))

    skipped: dict[str, int] = {}
    for req in reqs:
        if req.kind not in _VERIFIED_KINDS:
            skipped[req.kind] = skipped.get(req.kind, 0) + 1
    return findings, skipped


def _is_missing(req, findings: list[Finding]) -> bool:
    return any(f.req == req.id and f.result == "missing" for f in findings)


def _report(findings: list[Finding], skipped: dict[str, int], out: Path) -> None:
    by_result: dict[str, int] = {}
    for f in findings:
        by_result[f.result] = by_result.get(f.result, 0) + 1
    blocks = sum(1 for f in findings if f.severity == "block" and f.result != "ok")

    parts = "  ".join(f"{k}={v}" for k, v in sorted(by_result.items()))
    print(f"[verify] {len(findings)} findings: {parts or 'none'}")
    print(f"[verify] block: {blocks}")
    if skipped:
        detail = "  ".join(f"{k}={v}" for k, v in sorted(skipped.items()))
        print(f"[verify] 未由本版验证器覆盖（归 v_tree / v_consistency / L4）: {detail}")
    print(f"[verify] written: {out}")


def _utf8_stdio() -> None:
    """Windows 控制台默认可能是 GBK；输出中文不崩。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scan.py",
        description="OpenHarmony 编译前预检流水线（本版：L3 verify）",
    )
    p.add_argument("--stage", choices=STAGES, default="verify")
    p.add_argument("--config", default="config.yaml",
                   help="目标配置；缺省 config.yaml，不存在时用默认值")
    p.add_argument("--requirements", default="out/requirements.json",
                   help="L2 产物")
    p.add_argument("--out", default="out/findings.json",
                   help="L3 产物")
    p.add_argument("--round", type=int, default=0,
                   help="修复轮次；0 = 初验")
    p.add_argument("--target-env", default="",
                   help="验证跑在哪个环境（脱敏后写入 meta）")
    p.add_argument("--fail-fast", action="store_true",
                   help="有 block 时以退出码 2 结束")
    return p


if __name__ == "__main__":
    raise SystemExit(main())
