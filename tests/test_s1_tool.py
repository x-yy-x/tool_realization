"""Offline acceptance/regression tests; no OpenHarmony or target host required."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from extractors.s1_tool import scan_tree
from lib.shelltoken import ShellSyntaxError, iter_commands, tokenize, unwrap


REPO = Path(__file__).resolve().parents[1]


class LexerTests(unittest.TestCase):
    def argv(self, source):
        return [command.argv for command in iter_commands(source)]

    def test_quotes_comments_and_literal_hash(self):
        self.assertEqual(self.argv('echo "# not a comment; a && b | then"; ninja foo#bar # ignore\n'),
                         [("echo", "# not a comment; a && b | then"), ("ninja", "foo#bar")])

    def test_single_quotes_and_escaped_space(self):
        self.assertEqual(self.argv("echo '$HOME; $(fake)' escaped\\ space"),
                         [("echo", "$HOME; $(fake)", "escaped space")])
        self.assertFalse(tokenize("'$HOME'")[0].dynamic)

    def test_continuation_keeps_physical_lines(self):
        commands = list(iter_commands("# note\ntimeout \\\n  500 hb build\nninja\n"))
        self.assertEqual(commands[0].argv, ("timeout", "500", "hb", "build"))
        self.assertEqual((commands[0].line, commands[1].line), (2, 4))
        self.assertEqual(commands[0].tokens[2].line, 3)

    def test_comment_backslash_does_not_join_next_line(self):
        self.assertEqual(self.argv("# ignored \\\nninja\n"), [("ninja",)])

    def test_nested_substitutions(self):
        commands = self.argv('echo "$(dirname "$(which ninja)")"; echo `uname -m`')
        self.assertIn(("which", "ninja"), commands)
        self.assertIn(("dirname", "$(which ninja)"), commands)
        self.assertIn(("uname", "-m"), commands)
        self.assertEqual(len(commands), 5)

    def test_assignments_and_redirections(self):
        self.assertEqual(self.argv('FOO=1 2>error.log ninja 1>&2'), [("ninja",)])
        self.assertEqual(self.argv('>out CC=gcc ninja -v'), [("ninja", "-v")])

    def test_s5_interface_preserves_link_args(self):
        command = next(iter_commands('$CC -o out "$OBJS" -lc -lnosys -L "/tmp/a b"'))
        self.assertEqual(command.argv, ("$CC", "-o", "out", "$OBJS", "-lc", "-lnosys", "-L", "/tmp/a b"))
        self.assertTrue(command.tokens[0].dynamic)

    def test_control_flow_and_function_definition(self):
        source = 'build_it() { ninja; }\nif test -f x; then build_it; else make; fi\nfor x in a b; do mcopy x y; done'
        commands = self.argv(source)
        self.assertNotIn(("build_it",), commands[:1])
        self.assertIn(("ninja",), commands)
        self.assertIn(("mcopy", "x", "y"), commands)
        self.assertFalse(any(c[0] in {"for", "fi", "done", "a", "b"} for c in commands))

    def test_case_patterns_are_not_commands(self):
        self.assertEqual(self.argv('case "$X" in a|b) ninja ;; *) make ;; esac'), [("ninja",), ("make",)])

    def test_extended_test_operators_are_not_shell_commands(self):
        self.assertEqual(self.argv('if [[ "$X" == a && "$Y" == b ]]; then ninja; fi'), [("ninja",)])

    def test_heredoc_is_data(self):
        self.assertEqual(self.argv("cat <<'END'\nfake command\n$(also_fake)\nEND\nninja"), [("cat",), ("ninja",)])

    def test_multiple_heredocs_and_tab_stripping(self):
        self.assertEqual(self.argv("cat <<A <<-B\nignore\nA\n\tmore_fake\n\tB\nmake"), [("cat",), ("make",)])

    def test_bad_syntax_is_not_silently_split(self):
        for source in ('echo "broken', 'echo $(broken', "cat <<END\nbody", "ninja \\"):
            with self.subTest(source=source), self.assertRaises(ShellSyntaxError):
                list(iter_commands(source))

    def test_wrappers_with_values_units_and_assignments(self):
        command = next(iter_commands('sudo -u root env FOO=1 timeout --signal TERM -k 2s .5m hb build'))
        self.assertEqual([call.head.value for call in unwrap(command)], ["sudo", "env", "timeout", "hb"])

    def test_attached_wrapper_options_and_end_of_options(self):
        for source, expected in [
            ('sudo -uroot -- ninja', ["sudo", "ninja"]),
            ('nice -n 10 stdbuf -oL make', ["nice", "stdbuf", "make"]),
            ('ionice -c2 -n7 setsid -w nohup ninja', ["ionice", "setsid", "nohup", "ninja"]),
            ('timeout --kill-after=2s -- 1.5h hb', ["timeout", "hb"]),
        ]:
            with self.subTest(source=source):
                self.assertEqual([c.head.value for c in unwrap(next(iter_commands(source)))], expected)

    def test_unknown_wrapper_option_stops_without_guessing_operand(self):
        calls = unwrap(next(iter_commands('env -S "ninja -v"')))
        self.assertEqual([c.head.value for c in calls], ["env"])
        self.assertIn("Unsupported", calls[0].reason)


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp_base = REPO / "out" / "test_tmp"
        self.temp_base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="s1-tests-", dir=self.temp_base)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.temp_base.resolve()))
        self.temp.cleanup()

    def write(self, path, content):
        result = self.root / path
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(content, encoding="utf-8")
        return result

    def scan(self, paths=None):
        return scan_tree(self.root, paths)

    def names(self, result):
        return {r["name"] for r in result["requirements"]}

    def test_original_fixture_acceptance(self):
        result = scan_tree(REPO / "tests/fixture")
        self.assertEqual(self.names(result), {"timeout", "hb", "mkfs.vfat", "mformat", "mcopy"})
        self.assertFalse(result["unresolved"])
        self.assertEqual(result["references"][0]["path"], "./build/genconfig.sh")
        self.assertEqual(result["references"][0]["status"], "missing")
        self.assertEqual(next(r for r in result["requirements"] if r["name"] == "hb")["declared_at"], "build/build.sh:7")

    def test_external_genconfig_is_extracted_without_fixture_special_case(self):
        self.write("build.sh", 'set -e\ngenconfig\nif test -f x; then echo "config ok"; fi\ntimeout 500 hb build')
        self.assertEqual(self.names(self.scan()), {"genconfig", "timeout", "hb"})

    def test_builtin_and_keyword_denylist(self):
        self.write("a.sh", 'echo "config ok"\nset -e\nif [ -f x ]; then printf x; fi\nexport CC=gcc\ntest -d a')
        result = self.scan()
        self.assertEqual(result["requirements"], [])
        self.assertEqual(result["unresolved"], [])

    def test_function_calls_are_not_host_tools(self):
        self.write("a.sh", 'build_it() { ninja; }\nbuild_it\nfunction copy_it { mcopy a b; }\ncopy_it')
        self.assertEqual(self.names(self.scan()), {"ninja", "mcopy"})

    def test_shell_confidence_and_locations(self):
        self.write("build.sh", "# header\nninja")
        self.write("other.sh", "make")
        result = self.scan()["requirements"]
        self.assertEqual({r["name"]: r["confidence"] for r in result}, {"ninja": "high", "make": "medium"})
        self.assertEqual(next(r for r in result if r["name"] == "ninja")["declared_at"], "build.sh:2")

    def test_dynamic_commands_are_unresolved(self):
        self.write("a.sh", '$CC -lc\n${TOOL} -v\n"$(which ninja)" -v')
        result = self.scan()
        self.assertEqual(self.names(result), {"which"})
        self.assertEqual(len(result["unresolved"]), 3)
        self.assertTrue(all(r["confidence"] == "low" for r in result["unresolved"]))

    def test_shell_interpreter_c_recursion(self):
        self.write("a.sh", "bash -lc 'timeout 10s hb build'\nsh -c '$CMD'\n")
        result = self.scan()
        self.assertEqual(self.names(result), {"bash", "timeout", "hb", "sh"})
        self.assertEqual(len(result["unresolved"]), 1)

    def test_inline_python(self):
        self.write("a.sh", "python3 -c 'import shutil; shutil.which(\"ninja\")'")
        self.assertEqual(self.names(self.scan()), {"python3", "ninja"})

    def test_tree_references_not_external_tools(self):
        self.write("a.sh", "nohup ./scripts/run.sh &")
        self.write("scripts/run.sh", "ninja")
        result = self.scan(["a.sh"])
        self.assertEqual(self.names(result), {"nohup"})
        self.assertEqual(result["references"][0]["status"], "exists")

    def test_make_only_recipes_and_main_target(self):
        self.write("Makefile", 'LDFLAGS = -lc\n.PHONY: all clean\nall:\n\t@timeout 500 hb build\n\t$(CC) -lc\nclean:\n\t-rm -f out\nNOT_A_RECIPE=ninja')
        result = self.scan()
        self.assertEqual(self.names(result), {"timeout", "hb", "rm"})
        self.assertEqual({r["name"]: r["confidence"] for r in result["requirements"]}, {"timeout": "high", "hb": "high", "rm": "medium"})
        self.assertEqual(len(result["unresolved"]), 1)
        self.assertEqual(result["unresolved"][0]["declared_at"], "Makefile:5")

    def test_make_double_dollar_and_recipe_continuation(self):
        self.write("part.mk", 'all:\n\t@echo $$(which ninja)\n\tnice -n \\\n  5 make\n')
        result = self.scan()
        self.assertEqual(self.names(result), {"which", "nice", "make"})
        self.assertFalse(result["diagnostics"])

    def test_python_ast_aliases_multiline_and_keywords(self):
        self.write("a.py", 'import subprocess as sp\nfrom shutil import which as find\nsp.run(\n ["mcopy", "-i", image]\n)\nfind(cmd="ninja")\nsp.Popen(args=("timeout", "5s", "hb", "build"))\n')
        result = self.scan()
        self.assertEqual(self.names(result), {"mcopy", "ninja", "timeout", "hb"})
        self.assertTrue(all(r["confidence"] == "high" for r in result["requirements"]))
        self.assertEqual(next(r for r in result["requirements"] if r["name"] == "mcopy")["declared_at"], "a.py:3")

    def test_python_dynamic_not_guessed(self):
        self.write("a.py", 'import os, subprocess\nos.system("mkfs.vfat " + image)\nsubprocess.run([tool, "-v"])\nos.system(f"{tool} -v")')
        result = self.scan()
        self.assertFalse(result["requirements"])
        self.assertEqual(len(result["unresolved"]), 3)

    def test_python_shell_string_respects_quotes_wrappers(self):
        self.write("a.py", '''import subprocess, os
subprocess.run('timeout 500 hb build && echo "fake; command"', shell=True)
os.system("mkfs.vfat -F 32 image")
''')
        self.assertEqual(self.names(self.scan()), {"timeout", "hb", "mkfs.vfat"})

    def test_python_shell_false_string_not_split(self):
        self.write("a.py", 'import subprocess\nsubprocess.run("tool with spaces", shell=False)')
        result = self.scan()
        self.assertEqual(self.names(result), {"tool with spaces"})
        self.assertEqual(result["requirements"][0]["verify_cmd"], "command -v 'tool with spaces'")

    def test_python_shell_true_list_only_first_element_is_command(self):
        self.write("a.py", 'import subprocess\nsubprocess.run(["ninja -v", "not_a_command"], shell=True)')
        self.assertEqual(self.names(self.scan()), {"ninja"})

    def test_python_comments_strings_are_not_calls(self):
        self.write("a.py", '# os.system("fake")\nx = \'subprocess.run(["fake"])\'')
        self.assertFalse(self.scan()["requirements"])

    def test_python_escaped_newlines_anchor_to_real_call_line(self):
        self.write("a.py", 'import os\nos.system("ninja\\nmake")\n')
        result = self.scan()
        self.assertEqual(self.names(result), {"ninja", "make"})
        self.assertTrue(all(r["declared_at"] == "a.py:2" for r in result["requirements"]))
        self.assertTrue(all(r["declared_as"] == 'os.system("ninja\\nmake")' for r in result["requirements"]))

    def test_python_shadowed_import_is_not_high_confidence_tool(self):
        self.write("a.py", 'import subprocess\ndef check(subprocess):\n subprocess.run(["fake"])\n')
        result = self.scan()
        self.assertFalse(result["requirements"])
        self.assertIn("shadowed", result["unresolved"][0]["reason"])

    def test_python_executable_override(self):
        self.write("a.py", 'import subprocess\nsubprocess.run(["decoy", "--help"], executable="ninja")\nsubprocess.run(["other"], executable=dynamic)')
        result = self.scan()
        self.assertEqual(self.names(result), {"ninja"})
        self.assertEqual(len(result["unresolved"]), 1)

    def test_parse_errors_reported_and_other_files_survive(self):
        self.write("bad.py", "subprocess.run(")
        self.write("bad.sh", "echo 'broken")
        self.write("good.sh", "ninja")
        result = self.scan()
        self.assertEqual(self.names(result), {"ninja"})
        self.assertEqual({r["code"] for r in result["diagnostics"]}, {"python_syntax", "shell_syntax"})

    def test_gn_root_reference_recursion_and_args_confidence(self):
        self.write("BUILD.gn", 'action("image") {\n script = "//scripts/image.sh"\n args = ["ninja", "--output", "out.img", rebase_path("fake")]\n}')
        self.write("scripts/image.sh", "timeout 500 hb build")
        result = self.scan(["BUILD.gn"])
        self.assertEqual(self.names(result), {"ninja", "out.img", "timeout", "hb"})
        self.assertEqual(next(r for r in result["requirements"] if r["name"] == "ninja")["confidence"], "low")
        self.assertEqual(result["extractor_stats"]["files_parsed"], 2)
        self.assertFalse(result["diagnostics"])

    def test_gn_relative_python_script_and_dedup(self):
        self.write("sub/BUILD.gn", 'action_foreach("a") { script = "check.py" }\naction("b") { script = "check.py" }')
        self.write("sub/check.py", 'import shutil\nshutil.which("ninja")')
        result = self.scan(["sub/BUILD.gn", "sub/check.py"])
        self.assertEqual(len(result["requirements"]), 1)
        self.assertEqual(result["requirements"][0]["declared_at"], "sub/check.py:2")
        self.assertEqual(result["extractor_stats"]["files_parsed"], 2)

    def test_gn_non_action_ignored(self):
        self.write("BUILD.gn", 'group("not_action") { script = "missing.sh" args = ["fake"] }')
        result = self.scan()
        self.assertFalse(result["requirements"])
        self.assertFalse(result["references"])

    def test_gn_comments_braces_and_nested_conditions(self):
        self.write("BUILD.gn", '# action("fake") { script = "no.sh" }\naction("real}") {\n if (enabled) { script = "//a.sh" }\n args = ["--key={a}", "ninja"]\n}')
        self.write("a.sh", "make")
        result = self.scan(["BUILD.gn"])
        self.assertEqual(self.names(result), {"make", "ninja"})

    def test_gn_dynamic_script_and_args_retained(self):
        self.write("BUILD.gn", 'action("a") { script = "$root/run.py" args = ["$tool"] }\naction("b") { script = "prefix" + suffix }')
        result = self.scan()
        self.assertFalse(result["requirements"])
        self.assertFalse(result["references"])
        self.assertEqual(len(result["unresolved"]), 3)

    def test_gn_missing_and_outside_scripts_not_read(self):
        self.write("BUILD.gn", 'action("a") { script = "//missing.py" }\naction("b") { script = "../../outside.py" }')
        result = self.scan()
        self.assertEqual({r["status"] for r in result["references"]}, {"missing", "outside_root"})
        self.assertEqual(len(result["diagnostics"]), 2)

    def test_bad_gn_syntax_diagnostic(self):
        self.write("BUILD.gn", 'action("x") { script = "a.py"')
        self.assertEqual(self.scan()["diagnostics"][0]["code"], "gn_syntax")

    def test_unquoted_heredoc_expansions_are_visible_as_unresolved(self):
        self.write("a.sh", "cat <<EOF\n$(ninja)\nEOF\n")
        result = self.scan()
        self.assertEqual(self.names(result), {"cat"})
        self.assertEqual(len(result["unresolved"]), 1)

    def test_utf8_bom_crlf(self):
        path = self.root / "a.sh"
        path.write_bytes(b'\xef\xbb\xbf#!/bin/sh\r\ntimeout 500 hb build\r\n')
        result = self.scan()
        self.assertEqual(self.names(result), {"timeout", "hb"})
        self.assertTrue(all(r["declared_at"] == "a.sh:2" for r in result["requirements"]))

    def test_custom_denylist(self):
        self.write("a.sh", "ninja\nmake")
        denylist = self.write("deny.txt", "ninja # project-specific suppression\n")
        result = scan_tree(self.root, denylist=denylist)
        self.assertEqual(self.names(result), {"make"})

    def test_excluded_directories_and_no_execution(self):
        marker = self.root / "MUST_NOT_EXIST"
        self.write("a.sh", 'touch "' + marker.as_posix() + '"\nninja')
        self.write("out/generated.sh", "fake")
        self.write(".git/hook.sh", "fake")
        result = self.scan()
        self.assertEqual(self.names(result), {"touch", "ninja"})
        self.assertFalse(marker.exists())

    def test_stable_ids_and_json_serializable(self):
        self.write("a.sh", "ninja\nmake")
        first, second = self.scan(), self.scan()
        self.assertEqual(first["requirements"], second["requirements"])
        self.assertEqual(len({r["id"] for r in first["requirements"]}), 2)
        json.dumps(first)

    def test_reject_entry_outside_root(self):
        with self.assertRaises(ValueError):
            self.scan(["../outside.sh"])

    def test_cli_full_json_and_low_display(self):
        self.write("BUILD.gn", 'action("a") { args = ["ninja"] }')
        output = self.root / "report.json"
        command = [sys.executable, "-m", "extractors.s1_tool", "--root", str(self.root), "--output", str(output)]
        result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=True)
        self.assertNotIn("[low]", result.stdout)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["requirements"][0]["name"], "ninja")
        result = subprocess.run(command + ["--show-low"], cwd=REPO, capture_output=True, text=True, check=True)
        self.assertIn("[low] ninja", result.stdout)

    def test_cli_invalid_root_exit_code(self):
        result = subprocess.run([sys.executable, "-m", "extractors.s1_tool", "--root", str(self.root / "missing")],
                                cwd=REPO, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("S1 error", result.stderr)


if __name__ == "__main__":
    unittest.main()
