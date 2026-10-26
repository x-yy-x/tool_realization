"""L3 三个验证器的最小测试（全部离线，外部命令走桩）。

跑法（在 tool/ 下）：
    py -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.common.config import Config
from src.common.models import Requirement
from src.common.probe import RunResult
from src.l3_verify import v_exists, v_identity, v_reach
from scan import run_verify


INCLUDE_OUTPUT = """
#include "..." search starts here:
#include <...> search starts here:
 /opt/tc/include
 /opt/tc/target/include
End of search list.
"""


class FakeProbe:
    """只实现验证器用到的那几个原语。"""

    def __init__(self, *, commands=None, executables=None, runs=None,
                 dns=None, http=None, reals=None):
        self.commands = commands or {}
        self.executables = executables or {}
        self.runs = runs or {}
        self.dns = dns or {}
        self.http = http or {}
        self.reals = reals or {}
        self.calls = {"command_v": [], "run": [], "dns": [], "http": []}

    def command_v(self, name, timeout=10.0):
        self.calls["command_v"].append(name)
        path = self.commands.get(name)
        if path is None:
            return None, RunResult(["command -v", name], 1)
        return path, RunResult(["command -v", name], 0, stdout=path + "\n")

    def is_executable(self, path):
        return self.executables.get(str(path), True)

    def run(self, argv, timeout=30.0, cwd=None):
        key = " ".join(str(a) for a in argv)
        self.calls["run"].append(key)
        if key in self.runs:
            return self.runs[key]
        return RunResult([str(a) for a in argv], 127, stderr="not found")

    def dns_resolve(self, host, timeout=5.0):
        self.calls["dns"].append(host)
        return self.dns.get(host, RunResult(["getent", "hosts", host], 0,
                                            stdout="127.0.0.1\n"))

    def http_head(self, url, timeout=10.0):
        self.calls["http"].append(url)
        return self.http.get(url, RunResult(["curl", "-I", url], 0,
                                            stdout="200\nHTTP/1.1 200 OK"))

    def real_path(self, path):
        return self.reals.get(str(path), str(path))


def req(**over) -> Requirement:
    base = dict(
        id="REQ-00001", kind="tool", name="mcopy",
        declared_at="a.sh:42", declared_as="mcopy -i x ::",
        extractor="S1-tool", confidence="high",
        check="CHK-EXISTS-CMD", severity="block",
    )
    base.update(over)
    return Requirement.from_dict(base)


class ExistsTest(unittest.TestCase):
    def test_tool_ok(self):
        fake = FakeProbe(commands={"mcopy": "/usr/bin/mcopy"})
        f = v_exists.verify(req(), probe_module=fake)
        self.assertEqual(f.result, "ok")
        self.assertIn("/usr/bin/mcopy", f.evidence)
        self.assertEqual(f.exit_code, 0)

    def test_tool_missing(self):
        fake = FakeProbe(commands={"mcopy": None})
        f = v_exists.verify(req(), probe_module=fake)
        self.assertEqual(f.result, "missing")

    def test_tool_not_executable_is_its_own_state(self):
        fake = FakeProbe(
            commands={"mcopy": "/usr/bin/mcopy"},
            executables={"/usr/bin/mcopy": False},
        )
        f = v_exists.verify(req(), probe_module=fake)
        self.assertEqual(f.result, "mismatch")
        self.assertIn("not-executable", f.evidence)
        self.assertEqual(f.severity, "block")

    def test_repo_exists_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "present").mkdir()
            cfg = Config(source_root=tmp)
            fake = FakeProbe()
            ok = v_exists.verify(
                req(kind="repo", name="present", check="CHK-REPO-EXISTS"),
                config=cfg, probe_module=fake,
            )
            missing = v_exists.verify(
                req(kind="repo", name="absent", check="CHK-REPO-EXISTS"),
                config=cfg, probe_module=fake,
            )
            self.assertEqual(ok.result, "ok")
            self.assertEqual(missing.result, "missing")

    def test_lib_not_found_is_missing(self):
        argv = "cc -print-file-name=libnosuch.a"
        fake = FakeProbe(runs={argv: RunResult(argv.split(), 0,
                                               stdout="libnosuch.a\n")})
        f = v_exists.verify(
            req(kind="lib", name="libnosuch.a", check="CHK-LIB-EXISTS"),
            probe_module=fake,
        )
        self.assertEqual(f.result, "missing")
        self.assertIn("原样回显", f.evidence)


class IdentityTest(unittest.TestCase):
    def test_ok_when_triple_matches(self):
        name = "arm-none-eabi-gcc"
        fake = FakeProbe(
            commands={name: "/opt/tc/bin/" + name},
            runs={
                f"{name} -dumpmachine": RunResult([name, "-dumpmachine"], 0,
                                                  stdout="arm-none-eabi\n"),
                f"{name} -E -v -": RunResult([name, "-E", "-v", "-"], 0,
                                             stderr=INCLUDE_OUTPUT),
            },
            reals={"/opt/tc/bin/" + name: "/opt/tc/bin/" + name},
        )
        f = v_identity.verify(req(kind="toolchain", name=name,
                                  check="CHK-TOOLCHAIN-IDENTITY"),
                              probe_module=fake)
        self.assertEqual(f.result, "ok")
        self.assertIn("arm-none-eabi", f.evidence)
        self.assertIn("/opt/tc/include", f.evidence)

    def test_mismatch(self):
        name = "arm-linux-gnueabi-gcc"
        fake = FakeProbe(
            commands={name: "/usr/bin/" + name},
            runs={
                f"{name} -dumpmachine": RunResult([name, "-dumpmachine"], 0,
                                                  stdout="arm-none-eabi\n"),
                f"{name} -E -v -": RunResult([name, "-E", "-v", "-"], 0,
                                             stderr=INCLUDE_OUTPUT),
            },
        )
        f = v_identity.verify(
            req(kind="toolchain", name=name, check="CHK-TOOLCHAIN-IDENTITY"),
            probe_module=fake,
        )
        self.assertEqual(f.result, "mismatch")
        self.assertIn("期望 arm-linux-gnueabi", f.evidence)

    def test_unknown_when_no_expected_triple(self):
        name = "mycc"
        fake = FakeProbe(
            commands={name: "/usr/bin/mycc"},
            runs={f"{name} -dumpmachine": RunResult([name, "-dumpmachine"], 0,
                                                    stdout="x86_64-linux-gnu\n")},
        )
        f = v_identity.verify(
            req(kind="toolchain", name=name, check="CHK-TOOLCHAIN-IDENTITY"),
            probe_module=fake,
        )
        self.assertEqual(f.result, "unknown")
        self.assertIn("没有 config.target_triple", f.evidence)


class ReachTest(unittest.TestCase):
    def test_classify_table(self):
        table = {
            "200": ("ok", "info"),
            "302": ("ok", "info"),
            "403": ("mismatch", "warn"),
            "404": ("mismatch", "block"),
            "000": ("timeout", "block"),
        }
        for code, (result, severity) in table.items():
            got = v_reach.classify_http(code)
            self.assertEqual(got[:2], (result, severity), code)

    def test_classify_timeout(self):
        self.assertEqual(v_reach.classify_http(None, timed_out=True)[0], "timeout")

    def test_resolve_url(self):
        self.assertEqual(v_reach.resolve_url("third_party/x", "https://h/"),
                         "https://h/third_party/x")
        self.assertEqual(v_reach.resolve_url("https://x/y", None), "https://x/y")
        self.assertIsNone(v_reach.resolve_url("third_party/x", ""))

    def test_dns_failure_stops_before_http(self):
        fake = FakeProbe(
            dns={"h": RunResult(["getent", "hosts", "h"], 2, stderr="not found")},
        )
        f = v_reach.verify(
            req(kind="repo", name="third_party/x", check="CHK-REPO-EXISTS"),
            config=Config(repo_base_url="https://h"),
            probe_module=fake,
        )
        self.assertEqual(f.result, "unreachable")
        self.assertEqual(fake.calls["http"], [])

    def test_ok(self):
        fake = FakeProbe()
        f = v_reach.verify(
            req(kind="repo", name="third_party/x", check="CHK-REPO-EXISTS"),
            config=Config(repo_base_url="https://h"),
            probe_module=fake,
        )
        self.assertEqual(f.result, "ok")
        self.assertEqual(fake.calls["http"], ["https://h/third_party/x"])


class OrchestrationTest(unittest.TestCase):
    def test_only_missing_repos_go_to_reach(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "present_repo").mkdir()
            cfg = Config(source_root=tmp, repo_base_url="https://h")
            reqs = [
                req(id="REQ-00001", kind="tool", name="mcopy"),
                req(id="REQ-00002", kind="repo", name="present_repo",
                    check="CHK-REPO-EXISTS"),
                req(id="REQ-00003", kind="repo", name="missing_repo",
                    check="CHK-REPO-EXISTS"),
                req(id="REQ-00004", kind="arch", name="riscv32",
                    check="CHK-ARCH-MAP"),
            ]
            fake = FakeProbe(commands={"mcopy": "/usr/bin/mcopy"})
            findings, skipped = run_verify(reqs, cfg, probe_module=fake)

            by_gate = {(f.req, f.gate): f.result for f in findings}
            self.assertEqual(by_gate[("REQ-00001", "g2")], "ok")
            self.assertEqual(by_gate[("REQ-00002", "g2")], "ok")
            # 缺失的仓在第 2 关记 missing，第 5 关再问「补得上吗」
            self.assertEqual(by_gate[("REQ-00003", "g2")], "missing")
            self.assertEqual(by_gate[("REQ-00003", "g5")], "ok")
            # 存在的仓不探网络
            self.assertEqual(fake.calls["http"], ["https://h/missing_repo"])
            self.assertEqual(skipped, {"arch": 1})


if __name__ == "__main__":
    unittest.main()
