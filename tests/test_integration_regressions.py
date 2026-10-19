"""Regression cases found during the 2026-10-10 review."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from extractors.s1_tool import ToolScanner, _write_report, main, scan_tree


REPO = Path(__file__).resolve().parents[1]


class IntegrationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_base = REPO / "out" / "test_tmp"
        self.temp_base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="integration-", dir=self.temp_base)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.temp_base.resolve()))
        self.temp.cleanup()

    def write(self, name, source):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def names(self, result):
        return {r["name"] for r in result["requirements"]}

    def test_same_line_different_commands_keep_both_evidence(self):
        self.write("a.sh", "ninja -v; ninja -C out")
        requirements = scan_tree(self.root)["requirements"]
        self.assertEqual({r["declared_as"] for r in requirements}, {"ninja -v", "ninja -C out"})
        self.assertEqual(len(requirements), 2)

    def test_source_order_uses_numeric_line_numbers(self):
        self.write("a.sh", "\nninja\n" + "\n" * 7 + "make\n")
        requirements = scan_tree(self.root)["requirements"]
        self.assertEqual([r["declared_at"] for r in requirements], ["a.sh:2", "a.sh:10"])

    def test_make_bad_recipe_does_not_discard_next_recipe(self):
        self.write("Makefile", 'all:\n\techo "broken\n\tninja\n')
        result = scan_tree(self.root)
        self.assertEqual(self.names(result), {"ninja"})
        self.assertEqual(result["diagnostics"][0]["declared_at"], "Makefile:2")

    def test_make_default_shell_does_not_share_functions(self):
        self.write("Makefile", "all:\n\tbuild_it() { ninja; }\n\tbuild_it\n")
        self.assertEqual(self.names(scan_tree(self.root)), {"ninja", "build_it"})

    def test_make_oneshell_shares_functions(self):
        self.write("Makefile", ".ONESHELL:\nall:\n\tbuild_it() { ninja; }\n\tbuild_it\n")
        self.assertEqual(self.names(scan_tree(self.root)), {"ninja"})

    def test_make_heredoc_is_confined_to_logical_recipe(self):
        self.write("Makefile", "all:\n\tcat <<EOF\n\tninja\n\tEOF\n")
        result = scan_tree(self.root)
        self.assertIn("ninja", self.names(result))
        self.assertTrue(result["diagnostics"])

    def test_make_oneshell_heredoc_data_is_not_executed(self):
        self.write("Makefile", ".ONESHELL:\nall:\n\tcat <<'EOF'\n\tninja\n\tEOF\n\tmcopy a b\n")
        result = scan_tree(self.root)
        self.assertEqual(self.names(result), {"cat", "mcopy"})
        self.assertFalse(result["diagnostics"])
        self.assertEqual(next(r for r in result["requirements"] if r["name"] == "mcopy")["declared_at"], "Makefile:6")

    def test_make_oneshell_does_not_share_across_targets(self):
        self.write("Makefile", ".ONESHELL:\nall:\n\tbuild_it() { ninja; }\nother:\n\tbuild_it\n")
        self.assertEqual(self.names(scan_tree(self.root)), {"ninja", "build_it"})

    def test_make_define_body_is_not_recipe(self):
        self.write("Makefile", "define RECIPE\n\tfake\nendef\nall:\n\tninja\n")
        self.assertEqual(self.names(scan_tree(self.root)), {"ninja"})

    def test_make_nested_variable_is_not_shell_substitution(self):
        self.write("Makefile", "all:\n\tninja $(if $(VAR),$(OTHER))\n\t$(call command,$(ARG))\n")
        result = scan_tree(self.root)
        self.assertEqual(self.names(result), {"ninja"})
        self.assertEqual(len(result["unresolved"]), 1)
        self.assertIn("$(call", result["unresolved"][0]["declared_as"])

    def test_make_literal_default_goal_sets_confidence(self):
        self.write("Makefile", ".DEFAULT_GOAL := build\nclean:\n\trm -f out\nbuild:\n\tninja\n")
        requirements = scan_tree(self.root)["requirements"]
        self.assertEqual({r["name"]: r["confidence"] for r in requirements}, {"rm": "medium", "ninja": "high"})

    def test_cli_cannot_overwrite_source(self):
        source = self.write("build.sh", "ninja\n")
        before = source.read_bytes()
        with redirect_stderr(io.StringIO()) as error:
            code = main(["--root", str(self.root), "--output", str(source)])
        self.assertEqual(code, 2)
        self.assertIn("overwrite", error.getvalue())
        self.assertEqual(source.read_bytes(), before)

    def test_shell_wrappers_require_external_builtins(self):
        self.write("build.sh", "echo local\nenv echo external\ntimeout 5 test -f out\nexec printf done\n")
        result = scan_tree(self.root)
        self.assertEqual(self.names(result), {"env", "echo", "timeout", "test", "printf"})
        self.assertEqual(len([r for r in result["requirements"] if r["name"] == "echo"]), 1)

    def test_known_function_wrapper_name_is_not_unwrapped(self):
        self.write("build.sh", "env() { ninja; }\nenv echo argument\n")
        self.assertEqual(self.names(scan_tree(self.root)), {"ninja"})

    def test_custom_denylist_applies_to_direct_external_execution(self):
        self.write("build.sh", "env ninja\nenv echo external\n")
        self.write("build.py", 'import subprocess\nsubprocess.run(["ninja"])\n')
        denylist = self.write("denylist.txt", "ninja echo\n")
        result = scan_tree(self.root, denylist=denylist)
        self.assertEqual(self.names(result), {"env"})

    def test_unquoted_external_bracket_is_a_literal_executable(self):
        self.write("build.sh", "[ -f out ]\nenv [ -f out ]\n")
        result = scan_tree(self.root)
        self.assertEqual(self.names(result), {"env", "["})
        self.assertEqual(result["unresolved"], [])

    def test_external_builtin_named_binary_verifier_does_not_accept_builtin(self):
        self.write("build.sh", "env echo hello\n")
        requirement = next(r for r in scan_tree(self.root)["requirements"] if r["name"] == "echo")
        self.assertTrue(requirement["requires_external_binary"])
        argv = shlex.split(requirement["verify_cmd"])
        # Use this test interpreter; an empty PATH contains no external echo.
        completed = subprocess.run([sys.executable, *argv[1:]],
                                   env={**os.environ, "PATH": str(self.root)}, capture_output=True)
        self.assertEqual(completed.returncode, 1)

    def test_inline_shell_syntax_error_uses_real_outer_line(self):
        self.write("build.py", 'import subprocess\nsubprocess.run(["bash", "-c", "ninja\\necho \'broken"])\n')
        result = scan_tree(self.root)
        diagnostic = next(d for d in result["diagnostics"] if d["code"] == "shell_syntax")
        self.assertEqual(diagnostic["declared_at"], "build.py:2")

    def test_gn_subtracted_args_do_not_create_tool_candidates(self):
        self.write("BUILD.gn", 'action("x") { args -= ["ninja"] }\n')
        result = scan_tree(self.root)
        self.assertEqual(result["requirements"], [])
        self.assertEqual(len(result["unresolved"]), 1)

    def test_python_script_reference_preserves_source_type(self):
        self.write("build.py", 'import subprocess\nsubprocess.run(["./scripts/probe.sh"])\n')
        result = scan_tree(self.root)
        self.assertEqual(result["references"][0]["source_type"], "python")

    def test_oneshell_repeated_rule_does_not_share_function(self):
        self.write("Makefile", ".ONESHELL:\nall::\n\tprobe() { ninja; }\nall::\n\tprobe\n")
        result = scan_tree(self.root)
        self.assertEqual(self.names(result), {"ninja", "probe"})

    def test_cli_cannot_overwrite_existing_source_json(self):
        self.write("a.sh", "ninja")
        config = self.write("config.json", '{"product":"demo"}')
        before = config.read_bytes()
        with redirect_stderr(io.StringIO()):
            code = main(["--root", str(self.root), "--output", str(config)])
        self.assertEqual(code, 2)
        self.assertEqual(config.read_bytes(), before)

    def test_cli_can_replace_own_report(self):
        self.write("a.sh", "ninja")
        output = self.root / "report.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", str(self.root), "--output", str(output)]), 0)
            self.write("a.sh", "make")
            self.assertEqual(main(["--root", str(self.root), "--output", str(output)]), 0)
        self.assertEqual(self.names(json.loads(output.read_text(encoding="utf-8"))), {"make"})

    def test_atomic_report_failure_preserves_previous_report(self):
        self.write("a.sh", "ninja")
        scanner = ToolScanner(self.root)
        report = scanner.scan()
        output = self.root / "report.json"
        output.write_text(json.dumps(report), encoding="utf-8")
        before = output.read_bytes()
        with patch("extractors.s1_tool.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                _write_report(output, report, scanner)
        self.assertEqual(output.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".report.json.*.tmp")))

    def test_stats_do_not_count_failed_read_as_parsed(self):
        self.write("good.sh", "ninja")
        self.write("bad.py", "invalid(")
        result = scan_tree(self.root, ["good.sh", "bad.py", "missing.sh"])
        self.assertEqual(result["extractor_stats"]["files_seen"], 3)
        self.assertEqual(result["extractor_stats"]["files_read"], 2)
        self.assertEqual(result["extractor_stats"]["files_parsed"], 1)


if __name__ == "__main__":
    unittest.main()
