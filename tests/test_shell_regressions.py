"""Shell boundary regressions found during the S1 delivery audit.

All cases are inspected as text. No source command or shell is executed.
"""

from pathlib import Path
import unittest

from extractors.s1_tool import ToolScanner
from lib.shelltoken import ShellSyntaxError, function_names, iter_commands, unwrap


REPO = Path(__file__).resolve().parents[1]


class ShellRegressionTests(unittest.TestCase):
    def argv(self, text):
        return [command.argv for command in iter_commands(text)]

    def scan(self, text):
        scanner = ToolScanner(REPO)
        path = REPO / "audit_source.sh"
        scanner.source_lines[path] = text.splitlines()
        scanner.shell(text, path)
        return scanner

    def names(self, text):
        return {item["name"] for item in self.scan(text).requirements}

    def calls(self, text, *, external=False):
        return unwrap(next(iter_commands(text)), external=external)

    def test_arithmetic_expansion_visits_real_subcommands(self):
        text = 'jobs=$(( $(nproc) + 1 ))\nninja -j "$jobs"'
        self.assertEqual(self.names(text), {"nproc", "ninja"})
        self.assertEqual(next(r for r in self.scan(text).requirements if r["name"] == "nproc")["declared_at"],
                         "audit_source.sh:1")

    def test_parameter_fallback_visits_command_and_backtick_substitutions(self):
        for text in ('echo ${VALUE:-$(nproc)}', 'echo ${VALUE:-`nproc`}',
                     'echo "${VALUE:-\'$(nproc)\'}"', 'echo ${VALUE:-${OTHER:-$(nproc)}}'):
            with self.subTest(text=text):
                self.assertEqual(self.names(text), {"nproc"})

    def test_single_quote_in_unquoted_parameter_word_stays_literal(self):
        self.assertEqual(self.names("echo ${VALUE:-'$(fake)'}"), set())

    def test_arithmetic_commands_and_for_headers_are_data(self):
        self.assertEqual(self.names('(( count = 1 + 2 ))\nninja'), {"ninja"})
        self.assertEqual(self.names('for ((i=0; i<$(nproc); i++)); do ninja; done'), {"nproc", "ninja"})

    def test_array_initializers_are_data_and_visit_only_subcommands(self):
        self.assertEqual(self.names('tools=(ninja make)\necho "$tools"'), set())
        self.assertEqual(self.names('tools+=("$(nproc)" ninja case)\nmake'), {"nproc", "make"})

    def test_flag_with_an_invalid_value_is_not_guessed(self):
        calls = self.calls('timeout --foreground=ignored 5 ninja')
        self.assertEqual(len(calls), 1)
        self.assertIn("Unsupported", calls[0].reason)

    def test_case_pattern_parenthesis_inside_command_substitution(self):
        self.assertEqual(self.names('echo "$(case x in x) ninja ;; y) make ;; esac)"'), {"ninja", "make"})

    def test_empty_and_grouped_cases_keep_the_substitution_boundary(self):
        self.assertEqual(self.names('echo "$(case x in esac)"\nninja'), {"ninja"})
        self.assertEqual(self.names('echo "$({ case x in x) ninja ;; esac; })"'), {"ninja"})
        self.assertEqual(self.names('echo "$( ! case x in a|b) ninja ;; esac)"'), {"ninja"})

    def test_extended_test_marker_as_argument_does_not_hide_commands(self):
        self.assertEqual(self.argv('echo [[ && ninja ]]'), [("echo", "[["), ("ninja", "]]")])
        self.assertEqual(self.names('echo [[\nninja'), {"ninja"})

    def test_substitution_comment_does_not_terminate_expansion(self):
        self.assertEqual(self.names('echo "$(ninja # )\nmake\n)"'), {"ninja", "make"})
        locations = {r["name"]: r["declared_at"] for r in self.scan('echo "$(ninja # )\nmake\n)"').requirements}
        self.assertEqual(locations, {"ninja": "audit_source.sh:1", "make": "audit_source.sh:2"})

    def test_function_argument_and_quoted_symbols_are_not_declarations(self):
        self.assertEqual(self.names('echo function ninja\nninja'), {"ninja"})
        self.assertEqual(function_names("echo build_it '(' ')' '{'\nbuild_it"), set())

    def test_function_subshell_and_multiline_compound_body(self):
        for text in ('build_it() ( ninja )\nbuild_it', 'build_it()\n{ ninja; }\nbuild_it',
                     'function build_it\n{ ninja; }\nbuild_it'):
            with self.subTest(text=text):
                self.assertEqual(self.names(text), {"ninja"})

    def test_quoted_assignment_like_executable_is_not_removed(self):
        self.assertEqual(self.argv("'FOO=1' ninja"), [("FOO=1", "ninja")])
        self.assertEqual(self.argv('FOO="1" ninja'), [("ninja",)])

    def test_exec_and_command_forward_executable(self):
        self.assertEqual(self.names('exec -a custom_name ninja'), {"ninja"})
        self.assertEqual(self.names('command -p ninja'), {"ninja"})
        self.assertEqual(self.names('command -v ninja'), set())
        self.assertEqual(self.names('command -V ninja'), set())

    def test_external_wrapper_and_exec_payload_bypass_shell_builtin_lookup(self):
        for text in ('timeout 5 echo hi', 'env echo hi', 'sudo echo hi', 'nohup echo hi', 'exec echo hi'):
            with self.subTest(text=text):
                self.assertEqual(self.calls(text)[-1].head.value, "echo")
                self.assertTrue(self.calls(text)[-1].external)
        self.assertFalse(self.calls('command echo hi')[-1].external)
        self.assertFalse(self.calls('echo hi')[0].external)

    def test_external_argv_exec_and_command_are_executable_names(self):
        for text in ('command ninja', 'exec ninja'):
            with self.subTest(text=text):
                calls = self.calls(text, external=True)
                self.assertEqual(len(calls), 1)
                self.assertTrue(calls[0].external)
        self.assertEqual([c.head.value for c in self.calls('env exec ninja')], ["env", "exec"])

    def test_wrapper_diagnostic_retains_external_context(self):
        calls = self.calls('env timeout $SECONDS ninja')
        self.assertEqual([c.head.value for c in calls], ["env", "timeout"])
        self.assertTrue(calls[-1].external)
        self.assertTrue(calls[-1].reason)

    def test_help_and_version_options_do_not_execute_trailing_operand(self):
        for text in ('env --help ninja', 'timeout --version 5 ninja', 'sudo -l ninja'):
            with self.subTest(text=text):
                self.assertEqual(len(self.calls(text)), 1)

    def test_dynamic_wrapper_values_are_visible_and_do_not_guess_operand(self):
        for text in ('sudo -u $USER ninja', 'nice -n $PRIORITY ninja', 'timeout $SECONDS ninja',
                     'stdbuf -o$BUFFER ninja', 'env NAME=$VALUE ninja'):
            with self.subTest(text=text):
                calls = self.calls(text)
                self.assertEqual(len(calls), 1)
                self.assertTrue(calls[0].reason)
                self.assertNotIn("ninja", self.names(text))
                self.assertTrue(self.scan(text).unresolved)

    def test_only_env_and_sudo_consume_assignments(self):
        self.assertEqual([c.head.value for c in self.calls('env NAME=1 ninja')], ["env", "ninja"])
        self.assertEqual([c.head.value for c in self.calls('nohup NAME=1 ninja')], ["nohup", "NAME=1"])
        self.assertEqual([c.head.value for c in self.calls('timeout 5 NAME=1 ninja')], ["timeout", "NAME=1"])

    def test_assignment_ends_env_option_parsing(self):
        self.assertEqual([c.head.value for c in self.calls('env NAME=1 -n ninja')], ["env", "-n"])

    def test_process_substitution_visits_inner_commands(self):
        self.assertEqual(self.names('diff <(sort left) <(sort right)'), {"diff", "sort"})

    def test_heredoc_delimiter_does_not_execute_substitutions(self):
        self.assertEqual(self.names('cat <<$(fake)\nliteral data\n$(fake)\nninja'), {"cat", "ninja"})

    def test_comment_backslash_and_word_continuation(self):
        self.assertEqual(self.argv('# ignored \\\nninja'), [("ninja",)])
        self.assertEqual(self.argv('nin\\\nja#version # ignored'), [("ninja#version",)])
        self.assertEqual(self.argv('ninja \\\n# ignored\nmake'), [("ninja",), ("make",)])

    def test_nested_expansion_limit_reports_error(self):
        text = 'echo ' + '${VALUE:-' * 40 + 'ninja' + '}' * 40
        with self.assertRaises(ShellSyntaxError):
            list(iter_commands(text))


if __name__ == "__main__":
    unittest.main()
