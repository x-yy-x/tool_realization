"""Show real intermediate results for the four source languages in fixture_s1."""

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from extractors.s1_tool import DEFAULT_PROBLEM_MAP, ENTRY_NAMES, ToolScanner, scan_tree
from src.l2_extract.s1_parse import SourceDocument, classify_source, parse_source
from src.l2_extract.s1_literals import extract_literals
from src.l2_extract.s1_pipeline import annotate_tool, recognize_tools


def main():
    root = ROOT / "tests" / "fixture_s1"
    policy = ToolScanner(root)
    snapshots = []
    for name in ("build.sh", "Makefile", "BUILD.gn", "scripts/image.py", "scripts/probe.sh"):
        path = root / name
        text = path.read_text(encoding="utf-8-sig")
        language = classify_source(path)
        priority = "high" if language == "python" or path.name in ENTRY_NAMES else "medium"
        parsed = parse_source(SourceDocument(path, text, language, language, priority))
        literals = extract_literals(parsed)
        identified = recognize_tools(literals, denylist=policy.denylist)
        requirements = [annotate_tool(tool, source_root=root, shell_words=policy.shell_words,
                                      problem_map=DEFAULT_PROBLEM_MAP) for tool in identified.tools]
        snapshots.append({
            "source_read": {"path": name, "physical_lines": len(text.splitlines())},
            "classified_parse": {"language": language, "commands": len(parsed.commands),
                                 "recipes": len(parsed.recipes), "python_calls": len(parsed.python_calls),
                                 "gn_assignments": len(parsed.assignments)},
            "literal_extraction": {
                "values": [{"value": item.token.value, "raw": item.token.raw, "line": item.token.line,
                            "argv": item.argv,
                            "dynamic_arguments": [token.raw for token in item.argv_tokens if token.dynamic]}
                           for item in literals.values],
                "references": [{"value": item.value, "line": item.line} for item in literals.references],
                "unresolved": [{"expression": item.expression, "line": item.line, "reason": item.reason}
                               for item in literals.unresolved],
            },
            "recognition_filtering": {"tool_names": [tool.name for tool in identified.tools]},
            "provenance_confidence_annotation": requirements,
        })
        print(f"{name} [{language}]: literals="
              f"{[item.token.value for item in literals.values]} -> tools="
              f"{[tool.name for tool in identified.tools]}")
    output = ROOT / "out" / "s1_pipeline_stages.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"per_file_stage_snapshots": snapshots, "final_report": scan_tree(root)}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Actual stage snapshots and final JSON report: {output}")


if __name__ == "__main__":
    main()
