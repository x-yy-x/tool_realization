"""Compare S1 output with a small, manually labelled synthetic fixture."""

from collections import Counter
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from extractors.s1_tool import scan_tree  # noqa: E402


MANIFEST = ROOT / "tests" / "fp_fn_expectations.json"
FIXTURE = ROOT / "tests" / "fixture_s1"
CERTAIN_FIELDS = ("name", "declared_at", "source_type", "confidence")
UNRESOLVED_FIELDS = ("declared_at", "declared_as", "reason")


def _records(rows, fields):
    return Counter(tuple(row.get(field) for field in fields) for row in rows)


def _difference(expected, actual, fields):
    expected_counts = _records(expected, fields)
    actual_counts = _records(actual, fields)

    def expand(counts):
        rows = []
        for values, count in sorted(counts.items()):
            rows.extend(dict(zip(fields, values)) for _ in range(count))
        return rows

    return {
        "missing": expand(expected_counts - actual_counts),
        "unexpected": expand(actual_counts - expected_counts),
        "matched": sum((expected_counts & actual_counts).values()),
        "expected": sum(expected_counts.values()),
        "actual": sum(actual_counts.values()),
    }


def evaluate():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scan = scan_tree(FIXTURE)
    requirements = scan["requirements"]
    certain = [row for row in requirements if row["confidence"] in {"high", "medium"}]
    low = [row for row in requirements if row["confidence"] == "low"]

    certain_check = _difference(manifest["certain_requirements"], certain, CERTAIN_FIELDS)
    low_check = _difference(manifest["low_candidates"], low, CERTAIN_FIELDS)
    unresolved_check = _difference(manifest["unresolved"], scan["unresolved"], UNRESOLVED_FIELDS)

    probes = []
    for probe in manifest["negative_probes"]:
        rows = [row for row in certain if row["declared_at"] == probe["declared_at"]]
        names = sorted({row["name"] for row in rows})
        expected_names = sorted(probe["expected_names"])
        probes.append({
            "declared_at": probe["declared_at"],
            "expected_names": expected_names,
            "observed_names": names,
            "passed": names == expected_names,
        })

    true_positive = certain_check["matched"]
    false_positive = len(certain_check["unexpected"])
    false_negative = len(certain_check["missing"])
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    metrics = {
        "labelled_certain_expected": certain_check["expected"],
        "labelled_certain_detected": true_positive,
        "false_positive_cases": false_positive,
        "false_negative_cases": false_negative,
        "precision_on_labelled_fixture": (true_positive / precision_denominator
                                          if precision_denominator else 0.0),
        "recall_on_labelled_fixture": (true_positive / recall_denominator
                                       if recall_denominator else 0.0),
    }
    passed = (not certain_check["missing"] and not certain_check["unexpected"]
              and not low_check["missing"] and not low_check["unexpected"]
              and not unresolved_check["missing"] and not unresolved_check["unexpected"]
              and all(probe["passed"] for probe in probes)
              and scan["extractor_stats"]["files_parsed"] == manifest["expected_files_parsed"]
              and not scan["diagnostics"])
    return {
        "status": "PASS" if passed else "FAIL",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fixture": "tests/fixture_s1",
        "scope": "Five manually labelled synthetic files; not a real-tree accuracy estimate.",
        "metrics": metrics,
        "low_candidate_check": low_check,
        "unresolved_check": unresolved_check,
        "negative_probes": probes,
        "files_parsed": scan["extractor_stats"]["files_parsed"],
        "diagnostics": scan["diagnostics"],
        "false_positives": certain_check["unexpected"],
        "false_negatives": certain_check["missing"],
    }


def markdown(report):
    m = report["metrics"]
    lines = [
        "# S1 误报漏报核验",
        "",
        f"核验状态：**{report['status']}**。范围：{report['scope']}",
        "",
        "## 明确需求核对",
        "",
        "| 项目 | 结果 |",
        "| --- | ---: |",
        f"| 人工标注的明确需求 | {m['labelled_certain_expected']} |",
        f"| 检出且匹配 | {m['labelled_certain_detected']} |",
        f"| 本样例误报 | {m['false_positive_cases']} |",
        f"| 本样例漏报 | {m['false_negative_cases']} |",
        f"| 本样例 precision | {m['precision_on_labelled_fixture']:.0%} |",
        f"| 本样例 recall | {m['recall_on_labelled_fixture']:.0%} |",
        "",
        "这些比例仅描述当前人工标注的合成样例，不代表真实 OpenHarmony 项目的准确率。",
        "低置信度候选和 unresolved 另行核对，不计入明确需求的 precision/recall。",
        "",
        "## 需要单独复核的结果",
        "",
        f"- 低置信度候选：预期 {report['low_candidate_check']['expected']}，实际 {report['low_candidate_check']['actual']}。",
        f"- unresolved：预期 {report['unresolved_check']['expected']}，实际 {report['unresolved_check']['actual']}。",
        f"- 已解析文件：{report['files_parsed']}；诊断：{len(report['diagnostics'])}。",
        "",
        "## 误报探针",
        "",
        "| 来源 | 预期明确需求 | 实际明确需求 | 结果 |",
        "| --- | --- | --- | --- |",
    ]
    for probe in report["negative_probes"]:
        lines.append(
            f"| `{probe['declared_at']}` | {', '.join(probe['expected_names']) or '无'} | "
            f"{', '.join(probe['observed_names']) or '无'} | {'通过' if probe['passed'] else '发现误报'} |"
        )
    lines.extend(["", "完整逐项差异见同目录 `fp_fn_review.json`。", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "fp_fn_review.json")
    args = parser.parse_args()
    report = evaluate()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = output.with_suffix(".md")
    markdown_path.write_text(markdown(report), encoding="utf-8")
    print(f"S1 false-positive/false-negative review: {report['status']}")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"Negative probes: {sum(p['passed'] for p in report['negative_probes'])}/"
          f"{len(report['negative_probes'])} passed")
    print(f"Reports: {output} and {markdown_path}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
