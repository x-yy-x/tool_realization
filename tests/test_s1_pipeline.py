"""Verify the real stage boundaries, not just the final requirement count."""

from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from extractors import s1_tool
from src.l2_extract.s1_parse import SourceDocument, classify_source, parse_source
from src.l2_extract.s1_literals import extract_literals
from src.l2_extract.s1_pipeline import annotate_tool, recognize_tools


ROOT = Path(__file__).resolve().parents[1]


class S1PipelineTests(unittest.TestCase):
    def document(self, text, name="sample.sh", confidence="medium"):
        path = ROOT / name
        language = classify_source(path)
        return SourceDocument(path, text, language, language, confidence)

    def literals(self, text, name="sample.sh", confidence="medium"):
        return extract_literals(parse_source(self.document(text, name, confidence)))

    def test_classification_dispatches_all_four_languages(self):
        names = {"a.sh": "shell", "Makefile": "make", "part.mk": "make",
                 "BUILD.gn": "gn", "part.gni": "gn", "build.py": "python", "a.json": None}
        self.assertEqual({name: classify_source(Path(name)) for name in names}, names)

    def test_shell_syntax_precedes_literal_selection_and_preserves_argv(self):
        parsed = parse_source(self.document('echo "ninja; fake"\nninja -C "out dir"'))
        self.assertEqual([command.argv[0] for command in parsed.commands], ["echo", "ninja"])
        self.assertFalse(hasattr(parsed, "requirements"))
        literals = extract_literals(parsed)
        self.assertEqual([item.token.value for item in literals.values], ["echo", "ninja"])
        self.assertEqual(literals.values[1].argv, ("ninja", "-C", "out dir"))
        self.assertFalse(hasattr(literals.values[1], "declared_at"))
        self.assertFalse(hasattr(literals.values[1], "confidence"))
        self.assertEqual([item.name for item in recognize_tools(literals, denylist={"echo"}).tools], ["ninja"])

    def test_dynamic_head_stays_unresolved_before_recognition(self):
        literals = self.literals('$TOOL -v\necho "$(nproc)"')
        self.assertEqual({item.token.value for item in literals.values}, {"echo", "nproc"})
        self.assertEqual(len(literals.unresolved), 1)
        self.assertIn("$TOOL", literals.unresolved[0].expression)
        self.assertTrue(all(not item.token.dynamic for item in literals.values))

    def test_python_constants_come_from_proven_calls_not_text_or_expressions(self):
        source = ('import subprocess\ntext = \'subprocess.run(["fake"])\'\n'
                  'subprocess.run(["mcopy", image])\nsubprocess.run([tool])\n'
                  'subprocess.run("ninja " + flags)\n')
        parsed = parse_source(self.document(source, "a.py"))
        self.assertEqual(len(parsed.python_calls), 3)
        literals = extract_literals(parsed)
        self.assertEqual([item.token.value for item in literals.values], ["mcopy"])
        self.assertEqual(literals.values[0].argv, ("mcopy", "image"))
        self.assertTrue(literals.values[0].argv_tokens[1].dynamic)
        self.assertEqual(len(literals.unresolved), 2)

    def test_make_recipes_keep_original_evidence_and_opaque_make_variables(self):
        source = 'LDFLAGS = fake\nall:\n\t$(CC) -lc\n\tninja -C out\n'
        parsed = parse_source(self.document(source, "Makefile"))
        self.assertEqual([recipe.line for recipe in parsed.recipes], [3, 4])
        literals = extract_literals(parsed)
        self.assertEqual([item.token.value for item in literals.values], ["ninja"])
        self.assertEqual(literals.values[0].expression, "\tninja -C out")
        self.assertEqual(literals.unresolved[0].expression, "\t$(CC) -lc")

    def test_gn_direct_literals_precede_bare_tool_policy(self):
        source = 'action("x") { script = "//build.sh" args = ["ninja", "--flag", rebase_path("fake")] }'
        parsed = parse_source(self.document(source, "BUILD.gn"))
        self.assertEqual(len(parsed.assignments), 2)
        literals = extract_literals(parsed)
        self.assertEqual([item.token.value for item in literals.values], ["ninja", "--flag"])
        self.assertEqual(literals.references[0].value, "//build.sh")
        self.assertEqual(len(literals.unresolved), 1)
        self.assertEqual([tool.name for tool in recognize_tools(literals, denylist=set()).tools], ["ninja"])

    def test_recognition_policy_can_change_without_reparsing_or_mutating_literals(self):
        literals = self.literals("ninja\nmake")
        first = recognize_tools(literals, denylist={"make"}, custom_denylist=True)
        second = recognize_tools(literals, denylist={"ninja"}, custom_denylist=True)
        self.assertEqual([tool.name for tool in first.tools], ["ninja"])
        self.assertEqual([tool.name for tool in second.tools], ["make"])
        self.assertEqual([item.token.value for item in literals.values], ["ninja", "make"])
        with self.assertRaises(FrozenInstanceError):
            literals.values[0].expression = "changed"

    def test_annotation_adds_source_confidence_and_verifier_after_recognition(self):
        literals = self.literals('\nninja -C "out dir"', "build.sh", "high")
        tool = recognize_tools(literals, denylist=set()).tools[0]
        self.assertFalse(hasattr(tool, "verify_cmd"))
        self.assertEqual(tool.literal.source_context, "build_entry")
        requirement = annotate_tool(tool, source_root=ROOT, shell_words=set(), problem_map={"ninja": ["ENV-009"]})
        self.assertEqual(requirement["declared_at"], "build.sh:2")
        self.assertEqual(requirement["declared_as"], 'ninja -C "out dir"')
        self.assertEqual(requirement["confidence"], "high")
        self.assertEqual(requirement["maps_to"], ["ENV-009"])
        json.dumps(requirement)

    def test_parse_failure_has_diagnostics_and_no_invented_literals(self):
        for name, text in (("a.sh", 'echo "broken'), ("a.py", "subprocess.run("),
                           ("BUILD.gn", 'action("x") {')):
            with self.subTest(name=name):
                parsed = parse_source(self.document(text, name))
                self.assertTrue(parsed.issues)
                literals = extract_literals(parsed)
                self.assertFalse(literals.values)
                self.assertTrue(literals.diagnostics)

    def test_parsing_and_literals_neither_read_files_nor_execute_commands(self):
        source = 'import subprocess\nsubprocess.run(["touch", "MUST_NOT_EXIST"])\n'
        document = self.document(source, "a.py")
        with patch.object(Path, "read_text", side_effect=AssertionError("unexpected read")), \
                patch.object(subprocess, "run", side_effect=AssertionError("unexpected execution")), \
                patch("builtins.eval", side_effect=AssertionError("unexpected evaluation")):
            literals = extract_literals(parse_source(document))
        self.assertEqual([item.token.value for item in literals.values], ["touch"])

    def test_real_cli_runs_read_classify_parse_literal_recognize_annotate_json_in_order(self):
        temp_root = ROOT / "out" / "test_tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory).resolve()
            source = root / "build.sh"
            source.write_text("ninja\n", encoding="utf-8")
            events = []
            original_read = Path.read_text

            def read(path, *args, **kwargs):
                if path == source:
                    events.append("read")
                return original_read(path, *args, **kwargs)

            def tracked(label, function):
                def run(*args, **kwargs):
                    events.append(label)
                    return function(*args, **kwargs)
                return run

            with patch.object(Path, "read_text", read), \
                    patch.object(s1_tool, "classify_source", tracked("classify", classify_source)), \
                    patch.object(s1_tool, "parse_source", tracked("parse", parse_source)), \
                    patch.object(s1_tool, "extract_literals", tracked("literal", extract_literals)), \
                    patch.object(s1_tool, "recognize_tools", tracked("recognize", recognize_tools)), \
                    patch.object(s1_tool, "annotate_tool", tracked("annotate", annotate_tool)), \
                    patch.object(s1_tool, "_write_report", tracked("json", s1_tool._write_report)), \
                    redirect_stdout(io.StringIO()):
                code = s1_tool.main(["--root", str(root), "--path", "build.sh", "--output", str(root / "report.json")])
            self.assertEqual(code, 0)
            self.assertEqual(events, ["read", "classify", "parse", "literal", "recognize", "annotate", "json"])
            self.assertEqual(json.loads((root / "report.json").read_text())["requirements"][0]["name"], "ninja")

    def test_gn_script_recursion_reenters_the_same_stages(self):
        temp_root = ROOT / "out" / "test_tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory).resolve()
            (root / "BUILD.gn").write_text('action("x") { script = "//build.sh" }', encoding="utf-8")
            (root / "build.sh").write_text("ninja\n", encoding="utf-8")
            languages = []

            def parse(document):
                languages.append(document.language)
                return parse_source(document)

            with patch.object(s1_tool, "parse_source", parse):
                result = s1_tool.scan_tree(root, ["BUILD.gn"])
            self.assertEqual(languages, ["gn", "shell"])
            self.assertEqual(result["requirements"][0]["declared_at"], "build.sh:1")


if __name__ == "__main__":
    unittest.main()
