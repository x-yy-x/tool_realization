"""探测原语：执行命令、查命令、DNS、HTTP head。

被 L3 的 `v_exists` / `v_identity` / `v_reach` 与 `precheck/` 共用
（`src/common/README.md`）。

三条硬约束
----------
1. **每个外部动作都带超时。** 验证器自己不能挂住（`SYNC-001` 的教训）。
2. **不做破坏性探测。** 只读；不 kill、不写、不改。
3. **留下原始输出。** 验证器返回的是「命令 + 退出码 + stdout + stderr」，
   不是布尔值——`timeout` 的 124 和普通失败的 1 是两回事（`ENV-013`）。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit


@dataclass
class RunResult:
    argv: list[str]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.error

    def evidence(self) -> str:
        """人能看懂的一行 + 原始输出。报告直接用这个。"""
        head = "$ " + " ".join(self.argv)
        if self.timed_out:
            tail = "TIMEOUT"
        elif self.error:
            tail = f"ERROR: {self.error}"
        else:
            tail = f"exit={self.returncode}"
            if self.stdout.strip():
                tail += "\n" + _indent(self.stdout.strip())
            if self.stderr.strip():
                tail += "\n" + _indent(self.stderr.strip())
        return f"{head}\n{tail}"


def run(argv: Sequence[str], timeout: float = 30.0, cwd: str | Path | None = None) -> RunResult:
    """跑一个命令，永不抛异常（超时 / 找不到都折算成 RunResult）。"""
    argv = [str(a) for a in argv]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        return RunResult(argv, proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            argv, None,
            _text(exc.stdout), _text(exc.stderr),
            timed_out=True,
        )
    except FileNotFoundError:
        return RunResult(argv, None, error=f"命令不存在: {argv[0]}")
    except OSError as exc:
        return RunResult(argv, None, error=str(exc))


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #

def which(name: str) -> str | None:
    return shutil.which(name)


def command_v(name: str, timeout: float = 10.0) -> tuple[str | None, RunResult]:
    """`command -v <name>`。

    返回 (路径或 None, 原始结果)。POSIX 上真的跑 `sh -c 'command -v'`，
    因为报告要留下「命令 + 输出」；Windows 上没有 `sh` 时退回 `shutil.which`，
    并如实写进证据，不假装跑了 `command -v`。
    """
    sh = shutil.which("sh")
    if os.name != "nt" and sh:
        r = run([sh, "-c", 'command -v -- "$1"', "sh", name], timeout=timeout)
        path = r.stdout.strip().splitlines()[0] if r.stdout.strip() else None
        return path if r.returncode == 0 else None, r

    # Windows 上没有 sh：退回 shutil.which，并在证据里如实写清楚用的是哪个原语。
    path = shutil.which(name)
    if path:
        return path, RunResult(["shutil.which", name], 0, stdout=path + "\n")
    return None, RunResult(["shutil.which", name], 1, stderr="not found")


def is_executable(path: str | Path) -> bool:
    """文件在，且有执行位。Windows 上没有 x 位，os.access 退化为「文件存在」。"""
    p = Path(path)
    return p.is_file() and os.access(str(p), os.X_OK)


def real_path(path: str | Path) -> str:
    """`readlink -f` 的等价物：解掉全部符号链接。"""
    return os.path.realpath(str(path))


# --------------------------------------------------------------------------- #
# 网络
# --------------------------------------------------------------------------- #

def dns_resolve(host: str, timeout: float = 5.0) -> RunResult:
    """`getent hosts <host>`；没有 getent 时退回 socket。

    socket.getaddrinfo 不能设超时，所以只在没有 getent 的平台用，
    并把这一点写进证据。
    """
    if not host:
        return RunResult(["getent", "hosts", ""], None, error="空主机名")
    getent = shutil.which("getent")
    if getent and os.name != "nt":
        return run([getent, "hosts", host], timeout=timeout)

    argv = ["socket.getaddrinfo", host]
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = sorted({info[4][0] for info in infos})
        return RunResult(argv, 0, stdout="\n".join(addrs))
    except socket.gaierror as exc:
        return RunResult(argv, 2, stderr=f"gaierror: {exc}")
    except OSError as exc:
        return RunResult(argv, 1, stderr=str(exc))


def http_head(url: str, timeout: float = 10.0) -> RunResult:
    """HEAD 请求，只取状态码。优先 curl（设计里写的就是它），退回 urllib。"""
    curl = shutil.which("curl")
    if curl:
        argv = [curl, "-I", "-s", "-S", "-o", "-", "-w", "\n%{http_code}",
                "--max-time", str(timeout), url]
        r = run(argv, timeout=timeout + 2)
        if r.stdout:
            r.stdout = _normalize_curl_output(r.stdout)
        return r

    import urllib.error
    import urllib.request

    argv = ["urllib.head", url]
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return RunResult(argv, 0, stdout=f"{resp.status}\n{_headers(resp)}")
    except urllib.error.HTTPError as exc:
        return RunResult(argv, 0, stdout=f"{exc.code}\n{_headers(exc)}")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.timeout):
            return RunResult(argv, None, timed_out=True)
        return RunResult(argv, 6, stderr=str(reason))
    except (TimeoutError, socket.timeout):
        return RunResult(argv, None, timed_out=True)


def _normalize_curl_output(stdout: str) -> str:
    """curl 把 header 和 `%{http_code}` 一起写到 stdout。压成「状态码 + 首行」。"""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return stdout
    code = lines[-1].strip()
    status = next((line.strip() for line in lines[:-1]
                   if line.strip().startswith("HTTP/")), "")
    return f"{code}\n{status}" if status else code


def _headers(resp: object) -> str:
    headers = getattr(resp, "headers", None)
    if headers is None:
        return ""
    return "\n".join(f"{k}: {v}" for k, v in headers.items())


def split_url(url: str) -> tuple[str, str]:
    """返回 (scheme, host)。解析失败时 host 为空串。"""
    parts = urlsplit(url)
    return parts.scheme, parts.hostname or ""


@dataclass
class EndpointCheck:
    name: str
    url: str
    result: str
    severity: str
    evidence: str
    exit_code: int | None = None
    extra: dict[str, str] = field(default_factory=dict)
