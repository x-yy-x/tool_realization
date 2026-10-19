"""Contract tests for the shared lexer used by L2 text extractors."""

import unittest

from lib.shelltoken import iter_commands as legacy_iter_commands
from src.l2_extract.shelltoken import (
    iter_commands, tokenize, unwrap,
)


class SharedShellTokenizerTests(unittest.TestCase):
    def test_legacy_import_is_the_same_shared_implementation(self):
        self.assertIs(legacy_iter_commands, iter_commands)

    def test_quoted_separators_comments_and_empty_arguments_are_preserved(self):
        commands = list(iter_commands('echo "a; b && c # literal" \'\'; ninja # comment\n'))
        self.assertEqual([command.argv for command in commands], [
            ("echo", "a; b && c # literal", ""),
            ("ninja",),
        ])
        self.assertEqual(commands[0].text, 'echo "a; b && c # literal" \'\'')

    def test_make_style_continuation_keeps_physical_line_of_first_word(self):
        continued = "cc -c src/a.c " + chr(92) + chr(10) + "  -o out/a.o" + chr(10)
        commands = list(iter_commands(continued, line_offset=39))
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].line, 40)
        self.assertEqual(commands[0].argv, ("cc", "-c", "src/a.c", "-o", "out/a.o"))
        self.assertEqual(commands[0].tokens[-1].line, 41)

    def test_process_substitution_remains_one_argument_and_inner_commands_are_seen(self):
        text = "diff <(sort left) >(sort right)"
        commands = list(iter_commands(text))
        self.assertEqual(commands[0].argv, ("diff", "<(sort left)", ">(sort right)"))
        self.assertEqual(commands[0].text, text)
        self.assertEqual([command.argv for command in commands[1:]], [
            ("sort", "left"), ("sort", "right"),
        ])
        process_tokens = [token for token in tokenize(text) if token.value.startswith(("<(", ">("))]
        self.assertTrue(all(token.dynamic for token in process_tokens))

    def test_process_substitution_can_be_concatenated_into_a_word(self):
        commands = list(iter_commands("cat prefix<(sort input)suffix"))
        self.assertEqual(commands[0].argv, ("cat", "prefix<(sort input)suffix"))
        self.assertEqual(commands[1].argv, ("sort", "input"))

    def test_s5_link_arguments_are_not_evaluated_or_split(self):
        text = '$CC -o out "$OBJS" -lc -lnosys -L "/sysroot/lib dir"'
        command = next(iter_commands(text))
        self.assertEqual(command.argv, (
            "$CC", "-o", "out", "$OBJS", "-lc", "-lnosys", "-L", "/sysroot/lib dir",
        ))
        self.assertTrue(command.tokens[0].dynamic)
        self.assertTrue(command.tokens[3].dynamic)
        self.assertFalse(command.tokens[4].dynamic)

    def test_redirections_are_not_mistaken_for_arguments_or_commands(self):
        commands = list(iter_commands("ninja >out 2>&1 && make <input\n"))
        self.assertEqual([command.argv for command in commands], [("ninja",), ("make",)])
        self.assertEqual([command.text for command in commands], ["ninja >out 2>&1", "make <input"])

    def test_shared_lexer_keeps_wrapper_payload_for_s1_layer(self):
        command = next(iter_commands("env FOO=1 timeout --signal TERM 5 hb build"))
        wrapped = unwrap(command)
        self.assertEqual([item.head.value for item in wrapped], ["env", "timeout", "hb"])


if __name__ == "__main__":
    unittest.main()
