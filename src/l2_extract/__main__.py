import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .core import InputError, read_json
from .s3_pipeline import extract_s3


def write_json(path, data):
    """Replace one output atomically, never leave a half-written JSON file."""
    handle = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent),
                                         suffix=".json.tmp", delete=False) as f:
            handle = Path(f.name)
            json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.write("\n")
        os.replace(str(handle), str(path))
    finally:
        if handle is not None and handle.exists():
            handle.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description="S3 部件提取器：读取 path/classes 清单，输出精简声明与关系")
    parser.add_argument("--filelists", required=True, help="L1 filelists.json (path/classes array)")
    parser.add_argument("--root", required=True, help="existing source checkout root")
    parser.add_argument("--output", default="out/s3", help="output directory outside source root")
    parser.add_argument("--evidence", action="store_true", help="also save full evidence.json for debugging")
    args = parser.parse_args(argv)
    try:
        destination = Path(args.output).resolve()
        root = Path(args.root).resolve()
        if destination == root or root in destination.parents:
            raise InputError("output directory must be outside inspected source_root")
        inputs = {Path(args.filelists).resolve()}
        targets = [destination / "requirements.json", destination / "diagnostics.json"]
        if args.evidence:
            targets.append(destination / "evidence.json")
        if any(path.resolve() in inputs for path in targets):
            raise InputError("output would overwrite an input")
        output, diagnostics, evidence = extract_s3(root, read_json(args.filelists))
        destination.mkdir(parents=True, exist_ok=True)
        # os.replace replaces output symlinks rather than writing their targets.
        write_json(targets[0], output)
        write_json(targets[1], diagnostics)
        if args.evidence:
            write_json(targets[2], evidence)
        complete = diagnostics["complete"]
        print("S3: {} records; extraction {}; {} diagnostics".format(
            len(output), "complete" if complete else "partial",
            len(diagnostics["messages"])))
        print(str(targets[0]))
        print(str(targets[1]))
        return 0 if complete else 1
    except (InputError, OSError, UnicodeError) as exc:
        print("input/output error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
