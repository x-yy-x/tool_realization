"""Static regressions for executable semantics, lexical scopes and GN evidence."""

from pathlib import Path
import tempfile
import unittest

from extractors.s1_tool import scan_tree


REPO = Path(__file__).resolve().parents[1]
TEMP_ROOT = REPO / "out" / "test_tmp"


class PythonGNRegressions(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="python-gn-", dir=TEMP_ROOT)
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        # Keep recursive cleanup confined to the test workspace.
        if not self.root.is_relative_to(TEMP_ROOT.resolve()) or self.root == TEMP_ROOT.resolve():
            raise AssertionError("Unsafe test cleanup path")
        self.temp.cleanup()

    def scan(self, source, path="a.py"):
        (self.root / path).write_text(source, encoding="utf-8")
        return scan_tree(self.root, [path])

    def names(self, result):
        return {item["name"] for item in result["requirements"]}

    def test_unrelated_function_parameter_does_not_shadow_module_import(self):
        result = self.scan('import subprocess as sp\nsp.run(["ninja"])\n'
                           'def f(sp):\n sp.run(["fake"])\n'
                           'def g():\n sp.run(["make"])')
        self.assertEqual(self.names(result), {"ninja", "make"})
        self.assertEqual(len(result["unresolved"]), 1)
        self.assertEqual(result["unresolved"][0]["declared_at"], "a.py:4")

    def test_local_import_does_not_escape_its_function(self):
        result = self.scan('def f():\n import subprocess as sp\n sp.run(["ninja"])\n'
                           'def g():\n sp.run(["fake"])')
        self.assertEqual(self.names(result), {"ninja"})

    def test_separate_function_imports_use_their_own_alias(self):
        result = self.scan('def f():\n from subprocess import run as call\n call(["ninja"])\n'
                           'def g():\n from shutil import which as call\n call("make")')
        self.assertEqual(self.names(result), {"ninja", "make"})
        self.assertFalse(result["unresolved"])

    def test_class_namespace_does_not_shadow_method_global_binding(self):
        result = self.scan('import subprocess as sp\nclass C:\n sp = object()\n'
                           ' def run(self):\n  sp.run(["ninja"])')
        self.assertEqual(self.names(result), {"ninja"})

    def test_comprehension_target_does_not_escape_its_scope(self):
        result = self.scan('import subprocess\nx = [1 for subprocess in values]\n'
                           'subprocess.run(["ninja"])')
        self.assertEqual(self.names(result), {"ninja"})
        self.assertFalse(result["unresolved"])

    def test_exception_and_match_bindings_are_not_standard_library_imports(self):
        result = self.scan('import subprocess\ntry:\n pass\nexcept Exception as subprocess:\n'
                           ' subprocess.run(["fake"])\n')
        self.assertFalse(result["requirements"])
        self.assertEqual(len(result["unresolved"]), 1)
        result = self.scan('import subprocess\nmatch value:\n case {"x": subprocess}:\n'
                           '  subprocess.run(["fake"])')
        self.assertFalse(result["requirements"])
        self.assertEqual(len(result["unresolved"]), 1)

    def test_unimported_or_relative_modules_are_not_high_confidence(self):
        self.assertFalse(self.scan('subprocess.run(["fake"])')["requirements"])
        self.assertFalse(self.scan('from .subprocess import run\nrun(["fake"])')["requirements"])

    def test_known_executable_overrides_dynamic_argv_head(self):
        result = self.scan('import subprocess\nsubprocess.run([tool, "--help"], executable="ninja")')
        self.assertEqual(self.names(result), {"ninja"})

    def test_known_executable_survives_indirect_args(self):
        result = self.scan('import subprocess\nsubprocess.run(command, executable="ninja")')
        self.assertEqual(self.names(result), {"ninja"})
        self.assertEqual(len(result["unresolved"]), 1)

    def test_none_executable_is_the_default_behavior(self):
        result = self.scan('import subprocess\nsubprocess.run(["ninja"], executable=None)')
        self.assertEqual(self.names(result), {"ninja"})
        self.assertFalse(result["unresolved"])

    def test_direct_python_execution_and_which_require_external_builtins(self):
        result = self.scan('import subprocess, os, shutil\nsubprocess.run(["echo", "hello"])\n'
                           'shutil.which("echo")\nos.execvp("test", ["test", "-f", "file"])')
        self.assertEqual(self.names(result), {"echo", "test"})
        self.assertEqual(len(result["requirements"]), 3)

    def test_python_shell_execution_still_filters_shell_builtins(self):
        result = self.scan('import subprocess, os\nos.system("echo hello")\n'
                           'subprocess.run("echo hello", shell=True)\n'
                           'subprocess.run(["bash", "-c", "echo hello"])')
        self.assertEqual(self.names(result), {"bash"})

    def test_direct_argv_command_is_an_external_executable(self):
        result = self.scan('import subprocess\nsubprocess.run(["command", "-p", "ninja"])')
        self.assertEqual(self.names(result), {"command"})

    def test_python_external_wrapper_payload_keeps_builtin_named_binary(self):
        result = self.scan('import subprocess\nsubprocess.run(["env", "echo", "hello"])')
        self.assertEqual(self.names(result), {"env", "echo"})

    def test_starred_positional_arguments_do_not_invent_an_executable(self):
        result = self.scan('import subprocess\nsubprocess.run(*values, "flag", "decoy")')
        self.assertFalse(result["requirements"])
        self.assertEqual(len(result["unresolved"]), 1)

    def test_known_executable_survives_unknown_expanded_args(self):
        result = self.scan('import subprocess\nsubprocess.run(executable="ninja", **settings)')
        self.assertEqual(self.names(result), {"ninja"})
        self.assertEqual(len(result["unresolved"]), 1)

    def test_dynamic_executable_does_not_claim_decoy_argv(self):
        result = self.scan('import subprocess\nsubprocess.run(["decoy"], executable=tool)')
        self.assertFalse(result["requirements"])
        self.assertEqual(len(result["unresolved"]), 1)

    def test_shell_constants_follow_python_truthiness(self):
        for false_value in (False, 0, None, ""):
            with self.subTest(shell=false_value):
                result = self.scan('import subprocess\nsubprocess.run("tool with spaces", shell='
                                   + repr(false_value) + ')')
                self.assertEqual(self.names(result), {"tool with spaces"})
                self.assertFalse(result["unresolved"])
        for true_value in (True, 1, "yes"):
            with self.subTest(shell=true_value):
                result = self.scan('import subprocess\nsubprocess.run("timeout 5 hb", shell='
                                   + repr(true_value) + ')')
                self.assertEqual(self.names(result), {"timeout", "hb"})
                self.assertFalse(result["unresolved"])

    def test_dynamic_shell_preserves_known_executable(self):
        result = self.scan('import subprocess\nsubprocess.run(["fake"], shell=flag, executable="ninja")')
        self.assertEqual(self.names(result), {"ninja"})
        self.assertEqual(len(result["unresolved"]), 1)

    def test_popen_positional_executable_and_shell(self):
        result = self.scan('import subprocess\nsubprocess.Popen(["decoy"], -1, "ninja")\n'
                           'subprocess.Popen("timeout 5 hb", -1, None, None, None, None, None, True, True)')
        self.assertEqual(self.names(result), {"ninja", "timeout", "hb"})

    def test_keyword_expansion_overrides_are_not_silently_ignored(self):
        result = self.scan('import subprocess\nsubprocess.run(["decoy"], **{"executable": "ninja"})')
        self.assertEqual(self.names(result), {"ninja"})
        result = self.scan('import subprocess\nsubprocess.run(["decoy"], **settings)')
        self.assertFalse(result["requirements"])
        self.assertEqual(len(result["unresolved"]), 1)

    def test_attached_python_c_code_and_versioned_interpreter(self):
        result = self.scan('import subprocess\nsubprocess.run(["python3.11", '
                           '\'-cimport shutil; shutil.which("ninja")\'])')
        self.assertEqual(self.names(result), {"python3.11", "ninja"})

    def test_interpreter_option_values_are_not_c_code(self):
        result = self.scan('import subprocess\nsubprocess.run(["python3", "-Wonce", "-X", "dev", '
                           '"-c", \'import shutil; shutil.which("ninja")\'])')
        self.assertEqual(self.names(result), {"python3", "ninja"})
        self.assertFalse(result["unresolved"])

    def test_c_after_script_module_or_end_of_options_is_not_executed(self):
        source = 'import subprocess\n' + '\n'.join(
            'subprocess.run(' + repr(argv) + ')' for argv in (
                ["bash", "script.sh", "-c", "mcopy"],
                ["bash", "--", "script.sh", "-c", "mcopy"],
                ["python3", "-m", "module", "-c", 'import shutil; shutil.which("fake")'],
                ["python3", "script.py", "-c", 'import shutil; shutil.which("fake")'],
            ))
        result = self.scan(source)
        self.assertEqual(self.names(result), {"bash", "python3"})
        self.assertFalse(result["diagnostics"])

    def test_shell_option_value_before_c_is_skipped(self):
        result = self.scan("bash -o noclobber -lc 'timeout 5 hb'", path="a.sh")
        self.assertEqual(self.names(result), {"bash", "timeout", "hb"})

    def test_python_c_escaped_newlines_keep_real_outer_source(self):
        source = 'import subprocess\nsubprocess.run(["python3", "-c", \'import os\\nos.system("ninja")\'])'
        result = self.scan(source)
        self.assertEqual(self.names(result), {"python3", "ninja"})
        for item in result["requirements"]:
            self.assertEqual(item["declared_at"], "a.py:2")
            self.assertEqual(item["declared_as"], source.splitlines()[1])

    def test_nested_shell_c_escaped_newlines_keep_real_outer_source(self):
        source = 'import subprocess\nsubprocess.run(' + repr(["bash", "-c", "ninja\nmake"]) + ')'
        result = self.scan(source)
        self.assertEqual(self.names(result), {"bash", "ninja", "make"})
        self.assertTrue(all(item["declared_at"] == "a.py:2" for item in result["requirements"]))
        self.assertTrue(all(item["declared_as"] == source.splitlines()[1] for item in result["requirements"]))

    def test_shell_true_python_override_uses_python_ast(self):
        source = 'import subprocess\nsubprocess.run(' + repr('import shutil; shutil.which("ninja")')
        source += ', shell=True, executable="python3")'
        result = self.scan(source)
        self.assertEqual(self.names(result), {"python3", "ninja"})

    def test_unknown_shell_override_keeps_known_tool_and_payload_evidence(self):
        result = self.scan('import subprocess\nsubprocess.run("decoy", shell=True, executable="runner")')
        self.assertEqual(self.names(result), {"runner"})
        self.assertEqual(len(result["unresolved"]), 1)

    def test_gn_indirect_args_and_elements_remain_visible(self):
        result = self.scan('action("x") {\n args = tool_args\n'
                           ' args += [tool, "prefix" + suffix, rebase_path(source)]\n}', path="BUILD.gn")
        self.assertFalse(result["requirements"])
        self.assertEqual(len(result["unresolved"]), 4)
        self.assertIn("tool", {item["declared_as"] for item in result["unresolved"]})

    def test_gn_concatenated_args_keep_literal_candidate_and_dynamic_tail(self):
        result = self.scan('action("x") { args = ["ninja"] + tool_args }', path="BUILD.gn")
        self.assertEqual(self.names(result), {"ninja"})
        self.assertEqual(len(result["unresolved"]), 1)

    def test_gn_augmented_script_is_not_an_independent_path(self):
        result = self.scan('action("x") { script += ".py" }', path="BUILD.gn")
        self.assertFalse(result["references"])
        self.assertFalse(result["diagnostics"])
        self.assertEqual(len(result["unresolved"]), 1)


if __name__ == "__main__":
    unittest.main()
