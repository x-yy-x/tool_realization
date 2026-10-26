"""三份 JSON 契约的 dataclass 形态。

契约本身在 `schema/*.schema.json`（冻结的交付面），这里是它在 Python 里的投影。
**字段名必须与 schema 一字不差**——四层之间就是靠这三个文件名和字段名交接的。

为什么不引 jsonschema / pydantic：`src/README.md` §15.2 定了「Python 3 标准库，零依赖」。
一个用来诊断环境缺什么的工具，自己不能对环境有要求。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

KINDS = (
    "tool", "repo", "component", "toolchain", "lib", "symbol",
    "header", "gap", "config", "arch", "semantic", "version",
)
CONFIDENCES = ("high", "medium", "low")
SEVERITIES = ("block", "warn", "info")
RESULTS = ("ok", "missing", "mismatch", "unreachable", "timeout", "skip", "unknown")
FIX_CLASSES = ("A", "B", "C", "D")
FIX_STATUSES = ("pending", "fixed", "patched", "escalated", "rolled_back", "abandoned")
DECISIONS = ("apply_recipe", "propose_patch", "escalate", "abandon")


class ContractError(ValueError):
    """JSON 不符合 `schema/` 的契约。"""


# --------------------------------------------------------------------------- #
# filelist.json
# --------------------------------------------------------------------------- #

@dataclass
class FileEntry:
    path: str
    kind: str
    size: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path, "kind": self.kind}
        if self.size is not None:
            out["size"] = self.size
        return out


# --------------------------------------------------------------------------- #
# requirements.json
# --------------------------------------------------------------------------- #

@dataclass
class Requirement:
    id: str
    kind: str
    name: str
    declared_at: str
    declared_as: str
    extractor: str
    confidence: str
    check: str
    verify_cmd: str | None = None
    severity: str = "warn"
    maps_to: list[str] = field(default_factory=list)

    REQUIRED = ("id", "kind", "name", "declared_at", "declared_as",
                "extractor", "confidence", "check")
    OPTIONAL = ("verify_cmd", "severity", "maps_to")

    @classmethod
    def from_dict(cls, d: dict[str, Any], where: str = "") -> "Requirement":
        _require(d, cls.REQUIRED, where)
        _reject_unknown(d, cls.REQUIRED + cls.OPTIONAL, where)
        if d["kind"] not in KINDS:
            raise ContractError(f"{where}: 未知 kind {d['kind']!r}")
        if d["confidence"] not in CONFIDENCES:
            raise ContractError(f"{where}: 未知 confidence {d['confidence']!r}")
        severity = d.get("severity", "warn")
        if severity not in SEVERITIES:
            raise ContractError(f"{where}: 未知 severity {severity!r}")
        maps_to = d.get("maps_to", [])
        if not isinstance(maps_to, list):
            raise ContractError(f"{where}: maps_to 必须是列表")
        return cls(
            id=str(d["id"]), kind=d["kind"], name=str(d["name"]),
            declared_at=str(d["declared_at"]), declared_as=str(d["declared_as"]),
            extractor=str(d["extractor"]), confidence=d["confidence"],
            check=str(d["check"]), verify_cmd=d.get("verify_cmd"),
            severity=severity, maps_to=[str(x) for x in maps_to],
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id, "kind": self.kind, "name": self.name,
            "declared_at": self.declared_at, "declared_as": self.declared_as,
            "extractor": self.extractor, "confidence": self.confidence,
            "check": self.check, "severity": self.severity,
        }
        if self.verify_cmd is not None:
            out["verify_cmd"] = self.verify_cmd
        if self.maps_to:
            out["maps_to"] = list(self.maps_to)
        return out


def load_requirements(path: str | Path) -> tuple[dict[str, Any], list[Requirement]]:
    """读 requirements.json，返回 (meta, requirements)。"""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"{p}: JSON 语法错误: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"{p}: 顶层必须是对象")
    _require(data, ("meta", "requirements"), str(p))
    meta = data["meta"]
    if not isinstance(meta, dict):
        raise ContractError(f"{p}: meta 必须是对象")
    _require(meta, ("source_root", "scanned_at", "tool_version"), f"{p}:meta")
    raw = data["requirements"]
    if not isinstance(raw, list):
        raise ContractError(f"{p}: requirements 必须是数组")
    reqs = [Requirement.from_dict(item, f"{p}:requirements[{i}]")
            for i, item in enumerate(raw)]
    return meta, reqs


# --------------------------------------------------------------------------- #
# findings.json
# --------------------------------------------------------------------------- #

@dataclass
class Finding:
    id: str
    req: str
    gate: str
    severity: str
    kind: str
    name: str
    result: str
    evidence: str

    declared_at: str | None = None
    declared_as: str | None = None
    verify_cmd: str | None = None
    maps_to: list[str] = field(default_factory=list)
    exit_code: int | None = None

    fix_class: str | None = None
    allow: list[str] = field(default_factory=list)
    decision: str | None = None
    recipe: str | None = None
    fix: str | None = None
    advice: str | None = None
    fix_status: str | None = None
    attempts: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id, "req": self.req, "gate": self.gate,
            "severity": self.severity, "kind": self.kind, "name": self.name,
            "result": self.result, "evidence": self.evidence,
        }
        for key in ("declared_at", "declared_as", "verify_cmd", "exit_code",
                    "fix_class", "decision", "recipe", "fix", "advice",
                    "fix_status", "attempts"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.maps_to:
            out["maps_to"] = list(self.maps_to)
        if self.allow:
            out["allow"] = list(self.allow)
        return out


def next_finding_id(seq: int) -> str:
    return f"F-{seq:04d}"


def make_finding(
    req: Requirement,
    *,
    seq: int,
    gate: str,
    result: str,
    evidence: str,
    exit_code: int | None = None,
    severity: str | None = None,
) -> Finding:
    """由一条需求 + 验证结果造一条 finding。

    `declared_at` / `declared_as` 从 requirements 复制过来：报告要能直接指到行，
    不必再回查另一份文件。契约允许这两个字段（schema 的 `additionalProperties: false`
    里显式列了它们），尽管 `req` 的描述写着「不带」——那是文档内部的措辞冲突，
    见交付说明。
    """
    if result not in RESULTS:
        raise ContractError(f"未知 result {result!r}")
    return Finding(
        id=next_finding_id(seq), req=req.id, gate=gate,
        severity=severity or req.severity, kind=req.kind, name=req.name,
        result=result, evidence=evidence,
        declared_at=req.declared_at, declared_as=req.declared_as,
        verify_cmd=req.verify_cmd, maps_to=list(req.maps_to),
        exit_code=exit_code,
    )


def save_findings(
    path: str | Path,
    findings: Iterable[Finding],
    *,
    round: int = 0,
    target_env: str | None = None,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "round": int(round),
    }
    if target_env:
        meta["target_env"] = target_env
    payload = {"meta": meta, "findings": [f.to_dict() for f in findings]}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# 校验小工具
# --------------------------------------------------------------------------- #

def _require(d: dict[str, Any], keys: Iterable[str], where: str) -> None:
    missing = [k for k in keys if k not in d]
    if missing:
        raise ContractError(f"{where}: 缺少必填字段 {missing}")


def _reject_unknown(d: dict[str, Any], allowed: Iterable[str], where: str) -> None:
    extra = [k for k in d if k not in set(allowed)]
    if extra:
        raise ContractError(f"{where}: 契约外的字段 {extra}（schema 是 additionalProperties: false）")
