"""第 5 关：修复可达性——「缺的这些，补得上吗」。

设计见 `src/l3_verify/README.md` §20.4（三档分层）。

输入是**缺失集**，不是全部需求：第 2 关报「这个仓不在」、第 4 关报
「源码引用了但 manifest 没声明」，本关才去问「那能不能补拉」。
（`docs/02` §26.5：这一关探的集合受硬下界约束，物理上没法更早跑。）

三档
----
    ① DNS      getent hosts <host>        解析不了 -> unreachable，到此为止
    ② 连接      curl -I --max-time N       每个请求都必须带超时
    ③ 判读      按 HTTP 码分档

    | 码            | 判定                  | result        |
    |---------------|-----------------------|---------------|
    | 200/301/302   | 通                    | ok            |
    | 401/403       | 通，但凭证/权限有问题 | mismatch      |
    | 404           | 仓名或路径错          | mismatch      |
    | 超时 / 000    | 网络不通              | timeout       |
    | 其他          | 判不了                | unknown       |

**为什么分档**：403 和「连不上」的修复动作完全不同。报成一句「仓库不可达」等于没报。

URL 从哪来
----------
`requirements.json` 的 `repo` 条目只有仓名/路径，不带 remote。所以：
1. `name` 本身就是 URL -> 直接用
2. 配了 `config.repo_base_url` -> `<base>/<name>`
3. 都没有 -> `skip`，并写明原因。**不编造 URL。**

已知未做：`docs/02` §26.5 要求第 5 关结果持久缓存（vendor 闭源仓永久不存在，
每轮重探是浪费）。本版先不缓存，留给编排层。
"""

from __future__ import annotations

from typing import Iterable
from urllib.parse import urlsplit

from src.common import probe as probe_mod
from src.common.config import Config
from src.common.models import Finding, Requirement, make_finding

GATE = "g5"
HANDLES = frozenset({"repo"})

_OK_CODES = {"200", "301", "302"}
_DENIED_CODES = {"401", "403"}


def verify_all(
    reqs: Iterable[Requirement],
    *,
    config: Config | None = None,
    probe_module=probe_mod,
    start_seq: int = 1,
) -> list[Finding]:
    """注意：调用方应先跑第 2 关，只把 `result=missing` 的条目传进来。"""
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
    base_url: str | None = None,
    probe_module=probe_mod,
    seq: int = 1,
) -> Finding:
    cfg = config or Config()
    base = base_url if base_url is not None else cfg.repo_base_url
    url = resolve_url(req.name, base)
    if not url:
        return make_finding(
            req, seq=seq, gate=GATE, result="skip",
            evidence=(
                f"仓 {req.name} 没有可解析的 URL：requirements 不携带 remote，"
                "且 config.repo_base_url 为空。无法做可达性探测。"
            ),
            severity="info",
        )

    check = check_endpoint(req.name, url, cfg, probe_module)
    return make_finding(
        req, seq=seq, gate=GATE, result=check.result,
        evidence=check.evidence, exit_code=check.exit_code,
        severity=check.severity,
    )


def resolve_url(name: str, base_url: str | None) -> str | None:
    if name.startswith(("http://", "https://")):
        return name
    if base_url:
        return base_url.rstrip("/") + "/" + name.lstrip("/")
    return None


def check_endpoint(name: str, url: str, cfg: Config, probe_module=probe_mod):
    """DNS -> HTTP，返回 probe.EndpointCheck。precheck/ 也复用这个函数。"""
    scheme, host = _split(url)
    if not host:
        return probe_mod.EndpointCheck(
            name, url, "skip", "info", f"URL 无法解析出主机名: {url}"
        )

    dns = probe_module.dns_resolve(host, timeout=_timeout(cfg, "dns"))
    if not dns.ok:
        result = "timeout" if dns.timed_out else "unreachable"
        return probe_mod.EndpointCheck(
            name, url, result, "block",
            f"{dns.evidence()}\nDNS 解析失败，不必再试连接",
            _rc(dns),
        )

    http = probe_module.http_head(url, timeout=_timeout(cfg, "http"))
    code = _http_code(http.stdout)
    result, severity, verdict = classify_http(code, timed_out=http.timed_out,
                                              returncode=http.returncode)
    evidence = "\n".join([
        dns.evidence(),
        http.evidence(),
        f"判定: {verdict}（HTTP {code or '无'}）",
    ])
    return probe_mod.EndpointCheck(name, url, result, severity, evidence, _rc(http))


def classify_http(code: str | None, *, timed_out: bool = False,
                  returncode: int | None = None) -> tuple[str, str, str]:
    """(result, severity, 人话判定)。

    curl 的 000 表示「没拿到状态码」——超时或连接失败，不是 HTTP 0。
    """
    if timed_out or code in (None, "", "000"):
        return "timeout", "block", "超时 / 没有拿到状态码"
    if code in _OK_CODES:
        return "ok", "info", "通"
    if code in _DENIED_CODES:
        return "mismatch", "warn", "通，但凭证或权限有问题"
    if code == "404":
        return "mismatch", "block", "仓名或路径错"
    if returncode == 6:
        return "unreachable", "block", "DNS 解析失败"
    return "unknown", "warn", f"无法判读的状态码 {code}"


def _split(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname or ""


def _http_code(stdout: str) -> str | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if len(line) == 3 and line.isdigit():
            return line
    return None


def _timeout(cfg: Config, kind: str) -> float:
    return float(cfg.timeout(kind))


def _rc(r) -> int | None:
    return None if r.timed_out or r.error else r.returncode
