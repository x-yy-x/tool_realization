"""`config.yaml` 的读取与校验。

设计见 `src/README.md` §16（目录结构）与 §15（三个先决决定）。

约定
----
- **缺省值可跑**：没有配置文件时用一套安全默认值，不抛异常。预检工具不能因为
  配置缺失就罢工——它本来就是用来诊断环境的。
- **未知键只警告，不失败**：配置会长，旧版本读到新键不该炸。
- **排除规则不在这里**：L1 的目录剪枝是固定规则（[`l1_scan/rules.py`](../l1_scan/rules.py) 的
  `EXCLUDED_DIRS`），不提供配置项——它属于工具，不属于某个被测工程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .yamlish import YamlError, load_file

# 第 0 关的哨兵文件（docs/02 §18.1）
DEFAULT_SENTINELS = [
    "build/build.sh",
    "build/ohos.gni",
    "build/config/BUILDCONFIG.gn",
    ".repo/manifest.xml",
]

DEFAULT_TIMEOUTS = {"cmd": 30.0, "dns": 5.0, "http": 10.0}


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str
    timeout: float | None = None


@dataclass
class Config:
    source_root: str = ""
    product: str = ""
    board: str = ""
    tool_version: str = "0.1"

    # 第 3 关 v_identity 的期望三元组（可选）。为空时从编译器名推断。
    target_triple: str = ""

    # 第 5 关 v_reach：把仓名拼成 URL 的前缀（可选）。
    repo_base_url: str = ""

    # 默认 C 编译器。库与头文件的验证命令都从它派生。
    cc: str = ""

    sentinel_files: list[str] = field(default_factory=lambda: list(DEFAULT_SENTINELS))
    endpoints: list[Endpoint] = field(default_factory=list)
    timeouts: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TIMEOUTS))

    # 解析时发现的问题（未知键 / 类型不对）。调用方可打印，也可忽略。
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path | None) -> "Config":
        if path is None:
            return cls()
        p = Path(path)
        if not p.is_file():
            cfg = cls()
            cfg.warnings.append(f"配置文件不存在，使用默认值: {p}")
            return cfg
        try:
            data = load_file(p)
        except YamlError as exc:
            raise ConfigError(f"{p}: {exc}") from exc
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ConfigError(f"{p}: 顶层必须是映射（key: value）")
        return cls.from_mapping(data, source=str(p))

    @classmethod
    def from_mapping(cls, data: dict[str, Any], source: str = "<mapping>") -> "Config":
        cfg = cls()
        cfg.warnings = []

        def take_str(key: str) -> None:
            value = data.get(key)
            if value is None:
                return
            if not isinstance(value, str):
                cfg.warnings.append(f"{source}: {key} 应为字符串，已忽略")
                return
            setattr(cfg, key, value)

        for key in ("source_root", "product", "board", "tool_version",
                    "target_triple", "repo_base_url", "cc"):
            take_str(key)

        if "sentinel_files" in data:
            cfg.sentinel_files = _str_list(data["sentinel_files"], "sentinel_files", cfg)
        if "endpoints" in data:
            cfg.endpoints = _endpoints(data["endpoints"], cfg)
        if "timeouts" in data:
            cfg.timeouts = _timeouts(data["timeouts"], cfg)

        known = {
            "source_root", "product", "board", "tool_version", "target_triple",
            "repo_base_url", "cc", "sentinel_files", "endpoints",
            "timeouts",
        }
        for key in data:
            if key in known:
                continue
            if key == "excludes":
                cfg.warnings.append(
                    f"{source}: excludes 已移除——L1 的排除目录是固定规则"
                    f"（src/l1_scan/rules.py 的 EXCLUDED_DIRS），不再接受配置"
                )
                continue
            cfg.warnings.append(f"{source}: 未知配置键 {key!r}（已忽略）")
        return cfg

    def timeout(self, kind: str) -> float:
        return float(self.timeouts.get(kind, DEFAULT_TIMEOUTS.get(kind, 10.0)))

    def compiler(self) -> str:
        """验证用的 C 编译器名。`cc` 未配置时退回 `cc`。"""
        return self.cc or "cc"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_root": self.source_root,
            "product": self.product,
            "board": self.board,
            "tool_version": self.tool_version,
            "target_triple": self.target_triple,
            "repo_base_url": self.repo_base_url,
            "cc": self.cc,
            "sentinel_files": list(self.sentinel_files),
            "endpoints": [
                {"name": e.name, "url": e.url, **({"timeout": e.timeout} if e.timeout else {})}
                for e in self.endpoints
            ],
            "timeouts": dict(self.timeouts),
        }


class ConfigError(ValueError):
    """配置读不懂（语法错 / 顶层类型错）——这是唯一会让流程停下的配置问题。"""


def _str_list(value: Any, key: str, cfg: Config) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        cfg.warnings.append(f"{key} 应为字符串列表，已忽略")
        return []
    return list(value)


def _endpoints(value: Any, cfg: Config) -> list[Endpoint]:
    if not isinstance(value, list):
        cfg.warnings.append("endpoints 应为列表，已忽略")
        return []
    out: list[Endpoint] = []
    for i, item in enumerate(value):
        if isinstance(item, str):
            out.append(Endpoint(name=item, url=item))
            continue
        if not isinstance(item, dict):
            cfg.warnings.append(f"endpoints[{i}] 不是映射，已忽略")
            continue
        name = item.get("name")
        url = item.get("url")
        if not isinstance(name, str) or not isinstance(url, str):
            cfg.warnings.append(f"endpoints[{i}] 缺少 name / url，已忽略")
            continue
        timeout = item.get("timeout")
        if timeout is not None and not isinstance(timeout, (int, float)):
            cfg.warnings.append(f"endpoints[{i}].timeout 不是数字，已忽略")
            timeout = None
        out.append(Endpoint(name=name, url=url, timeout=float(timeout) if timeout else None))
    return out


def _timeouts(value: Any, cfg: Config) -> dict[str, float]:
    if not isinstance(value, dict):
        cfg.warnings.append("timeouts 应为映射，已忽略")
        return dict(DEFAULT_TIMEOUTS)
    out = dict(DEFAULT_TIMEOUTS)
    for key, raw in value.items():
        if not isinstance(raw, (int, float)):
            cfg.warnings.append(f"timeouts.{key} 不是数字，已忽略")
            continue
        out[str(key)] = float(raw)
    return out
