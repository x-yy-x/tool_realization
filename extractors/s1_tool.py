"""S1 coordinator: read -> classify/parse -> literals -> recognize -> annotate -> JSON.

CLI: python -m extractors.s1_tool --root tests/fixture --output out/s1.json
API: scan_tree(root, paths=None) -> JSON-serializable dictionary.
Language parsing and literal extraction are pure stages under src/l2_extract/.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

from src.l2_extract.s1_parse import SourceDocument, classify_source, parse_source
from src.l2_extract.s1_literals import extract_literals
from src.l2_extract.s1_pipeline import annotate_tool, recognize_tools

VERSION = "0.3.0"
DEFAULT_DENYLIST = Path(__file__).resolve().parents[1] / "rules" / "tool_denylist.txt"
EXCLUDED_DIRS = {".git", ".repo", "__pycache__", ".venv", "venv", "node_modules", "out"}
ENTRY_NAMES = {"build.sh", "mk.sh", "mkimage.sh"}
# Only the tool-to-case links explicitly given in §22. This is not a knowledge base.
DEFAULT_PROBLEM_MAP = {
    "python": ["ENV-003"], "ninja": ["ENV-009"], "timeout": ["ENV-013"],
    "mkfs.vfat": ["IMG-001"], "mcopy": ["IMG-002"], "genconfig": ["BUILD-001"],
}


def load_denylist(path: Path) -> set[str]:
    return {word for line in path.read_text(encoding="utf-8-sig").splitlines()
            for word in line.split("#", 1)[0].split()}


class ToolScanner:
    def __init__(self, root, *, denylist=None, problem_map=None):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError("Source root is not a directory: " + str(self.root))
        self.denylist_path = (Path(denylist) if denylist else DEFAULT_DENYLIST).resolve()
        self.denylist = load_denylist(self.denylist_path)
        self.custom_denylist = self.denylist_path != DEFAULT_DENYLIST.resolve()
        self.shell_words = load_denylist(DEFAULT_DENYLIST) if self.custom_denylist else self.denylist
        self.problem_map = DEFAULT_PROBLEM_MAP if problem_map is None else problem_map
        self.requirements, self.references, self.unresolved, self.diagnostics = [], [], [], []
        self.visited, self.seen, self.source_lines = set(), set(), {}
        self._reference_keys, self._unresolved_keys, self._diagnostic_keys = set(), set(), set()

    def location(self, path, line):
        return path.relative_to(self.root).as_posix() + ":" + str(line)

    def diagnostic(self, path, line, code, message):
        item = {"declared_at": self.location(path, line), "code": code, "message": message}
        key = (item["declared_at"], code, message)
        if key not in self._diagnostic_keys:
            self._diagnostic_keys.add(key)
            self.diagnostics.append(item)

    def uncertain(self, path, line, expression, reason):
        item = {"declared_at": self.location(path, line), "declared_as": expression,
                "confidence": "low", "reason": reason}
        key = (item["declared_at"], expression, reason)
        if key not in self._unresolved_keys:
            self._unresolved_keys.add(key)
            self.unresolved.append(item)

    def reference(self, value, path, line, *, source, recurse):
        # GN relative paths are relative to BUILD.gn. Shell paths assume source_root
        # as working directory; no arbitrary path outside source_root is opened.
        base = path.parent if source == "gn" else self.root
        target = (self.root / value[2:] if value.startswith("//") else base / value).resolve()
        inside = target.is_relative_to(self.root)
        status = "exists" if inside and target.is_file() else "missing" if inside else "outside_root"
        item = {"path": value, "declared_at": self.location(path, line), "source_type": source,
                "resolved_path": target.relative_to(self.root).as_posix() if inside else None, "status": status}
        key = tuple(item.items())
        if key not in self._reference_keys:
            self._reference_keys.add(key)
            self.references.append(item)
        if status != "exists":
            self.diagnostic(path, line, "script_" + status, "Referenced script: " + value)
        elif recurse:
            if target.suffix in {".sh", ".py"}:
                self.scan_file(target)
            else:
                self.diagnostic(path, line, "unsupported_script", "GN script language is unsupported: " + value)

    def _process(self, document):
        # Each stage consumes the previous stage's typed output. Parsing and
        # literal extraction have no access to this scanner or the filesystem.
        parsed = parse_source(document)
        literals = extract_literals(parsed, source_lines=self.source_lines.get(document.path))
        recognized = recognize_tools(literals, denylist=self.denylist,
                                     custom_denylist=self.custom_denylist)
        for tool in recognized.tools:
            requirement = annotate_tool(tool, source_root=self.root, shell_words=self.shell_words,
                                        problem_map=self.problem_map)
            key = (requirement["name"], requirement["declared_at"], requirement["source_type"], requirement["declared_as"])
            if key not in self.seen:
                self.seen.add(key)
                self.requirements.append(requirement)
        for item in recognized.unresolved:
            self.uncertain(item.path, item.line, item.expression, item.reason)
        for item in literals.diagnostics:
            self.diagnostic(item.path, item.line, item.code, item.message)
        for item in recognized.references:
            self.reference(item.value, item.path, item.line, source=item.source_type, recurse=item.recurse)

    def shell(self, text, path, *, line_offset=0, confidence="medium", source="shell", main_lines=(), depth=0, origin=None):
        self._process(SourceDocument(path, text, "shell", source, confidence,
                                     line_offset, depth, origin, tuple(main_lines)))

    def make(self, text, path):
        self._process(SourceDocument(path, text, "make", "make"))

    def python(self, text, path, *, line_offset=0, depth=0, origin=None):
        self._process(SourceDocument(path, text, "python", "python", "high", line_offset, depth, origin))

    def gn(self, text, path):
        self._process(SourceDocument(path, text, "gn", "gn"))

    def scan_file(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Scan input is outside source_root: " + str(path))
        if path in self.visited:
            return
        self.visited.add(path)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            self.diagnostic(path, 1, "read_error", str(exc))
            return
        self.source_lines[path] = text.splitlines()
        language = classify_source(path)
        confidence = "high" if language == "python" or language == "shell" and path.name in ENTRY_NAMES else "medium"
        self._process(SourceDocument(path, text, language, language, confidence))

    def scan(self, paths=None):
        if paths is None:
            paths = []
            def walk_error(error):
                directory = Path(error.filename) if error.filename else self.root
                self.diagnostic(directory, 1, "walk_error", str(error))

            for directory, dirs, files in os.walk(self.root, followlinks=False, onerror=walk_error):
                dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not Path(directory, d).is_symlink())
                for name in sorted(files):
                    p = Path(directory, name)
                    if (p.suffix in {".sh", ".mk", ".py", ".gni"} or name in {"Makefile", "makefile", "GNUmakefile", "BUILD.gn"}):
                        if p.is_symlink() and not p.resolve().is_relative_to(self.root):
                            self.diagnostic(p, 1, "outside_root", "Skipped symlink outside source_root")
                        else:
                            paths.append(p)
        for path in paths:
            self.scan_file(self.root / path)
        def source_order(record):
            file, line = record["declared_at"].rsplit(":", 1)
            return file, int(line)

        self.requirements.sort(key=lambda r: (*source_order(r), r["name"], r["source_type"], r["declared_as"]))
        for index, requirement in enumerate(self.requirements, 1):
            requirement["id"] = "S1-" + str(index).zfill(5)
        for records in (self.references, self.unresolved, self.diagnostics):
            records.sort(key=source_order)
        parse_errors = {d["declared_at"].rsplit(":", 1)[0] for d in self.diagnostics
                        if d["code"] in {"shell_syntax", "python_syntax", "gn_syntax", "unsupported_input"}}
        parsed = sum(p.relative_to(self.root).as_posix() not in parse_errors for p in self.source_lines)
        return {
            "meta": {"source_root": str(self.root), "scanned_at": datetime.now(timezone.utc).isoformat(),
                     "tool_version": VERSION, "scope": "S1-only", "executes_commands": False},
            "requirements": self.requirements, "references": self.references,
            "unresolved": self.unresolved, "diagnostics": self.diagnostics,
            "extractor_stats": {"S1": len(self.requirements), "files_seen": len(self.visited),
                                "files_read": len(self.source_lines), "files_parsed": parsed,
                                "by_confidence": {c: sum(r["confidence"] == c for r in self.requirements)
                                                  for c in ("high", "medium", "low")}},
        }


def scan_tree(root, paths=None, *, denylist=None, problem_map=None):
    """Return requirements plus unresolved expressions, references and diagnostics.

    ``paths`` optionally limits entry files; GN script references are still followed.
    IDs are local to S1. The future P0 aggregator can replace them with global IDs.
    """
    return ToolScanner(root, denylist=denylist, problem_map=problem_map).scan(paths)


def _write_report(output, result, scanner):
    """Protect source inputs and atomically replace an existing S1 report."""
    output = Path(output).resolve()
    if output in scanner.visited or output == scanner.denylist_path:
        raise ValueError("Report output would overwrite a source input or denylist: " + str(output))
    if output.is_file() and output.is_relative_to(scanner.root):
        try:
            previous = json.loads(output.read_text(encoding="utf-8"))
            own_report = isinstance(previous, dict) and isinstance(previous.get("meta"), dict) and previous["meta"].get("scope") == "S1-only"
        except (OSError, UnicodeError, ValueError):
            own_report = False
        if not own_report:
            raise ValueError("Report output would overwrite an existing source-tree file: " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=output.parent,
                                         prefix="." + output.name + ".", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(result, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Source tree to inspect (read only)")
    parser.add_argument("--output", type=Path, default=Path("out/s1_requirements.json"))
    parser.add_argument("--denylist", type=Path)
    parser.add_argument("--path", action="append", help="Entry file relative to root; repeatable")
    parser.add_argument("--show-low", action="store_true", help="Show low-confidence candidates and unresolved expressions")
    args = parser.parse_args(argv)
    try:
        scanner = ToolScanner(args.root, denylist=args.denylist)
        result = scanner.scan(args.path)
        _write_report(args.output, result, scanner)
    except (OSError, ValueError) as exc:
        print("S1 error: " + str(exc), file=sys.stderr)
        return 2
    for item in result["requirements"]:
        if item["confidence"] != "low" or args.show_low:
            print("[{confidence}] {name} <- {declared_at}".format(**item))
    if args.show_low:
        for item in result["unresolved"]:
            print("[unresolved] {declared_at}: {reason}".format(**item))
    stats = result["extractor_stats"]
    print(f"S1: {stats['S1']} requirements, {stats['files_parsed']} files; "
          f"{len(result['unresolved'])} unresolved, {len(result['diagnostics'])} diagnostics.")
    for item in result["diagnostics"]:
        print("[diagnostic:{code}] {declared_at}: {message}".format(**item))
    print(f"Full JSON (including low confidence): {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
