"""Shared input, provenance and output support. No tree traversal or execution."""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath


class InputError(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON key: " + key)
        result[key] = value
    return result


def loads(text):
    def invalid(value):
        raise InputError("non-finite JSON number: " + value)
    result = json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid)
    pending = [result]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise InputError("JSON contains an unpaired Unicode surrogate") from exc
        elif isinstance(item, float) and not math.isfinite(item):
            raise InputError("JSON number exceeds finite floating-point range")
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return result


def read_json(path):
    try:
        return loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise InputError("{}: {}".format(path, exc)) from exc


def validate_contract(value, schema, where="$"):
    """Validate only the keyword subset used by the repository's two contracts.

    This is not a general JSON Schema implementation. Development tests also
    use the independent draft-2020-12 jsonschema validator.
    """
    checks = {
        "object": lambda x: isinstance(x, dict),
        "array": lambda x: isinstance(x, list),
        "string": lambda x: isinstance(x, str),
        "integer": lambda x: type(x) is int,
        "number": lambda x: type(x) in (int, float) and math.isfinite(x),
    }
    kind = schema.get("type")
    if kind and not checks[kind](value):
        raise InputError("{}: expected {}".format(where, kind))
    if "enum" in schema and value not in schema["enum"]:
        raise InputError("{}: invalid value {!r}".format(where, value))
    if "minimum" in schema and value < schema["minimum"]:
        raise InputError(where + ": below minimum")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise InputError("{}: missing {}".format(where, sorted(missing)))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            sub = props.get(key, extra)
            if sub is False:
                raise InputError("{}: unexpected key {}".format(where, key))
            if isinstance(sub, dict):
                validate_contract(item, sub, where + "." + key)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            validate_contract(item, schema.get("items", {}), where + "[{}]".format(i))
    elif isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise InputError(where + ": pattern mismatch")
        if schema.get("format") == "date-time":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", value):
                raise InputError(where + ": expected RFC 3339 datetime with timezone")
            try:
                datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
            except ValueError as exc:
                raise InputError(where + ": invalid datetime") from exc


def contract(name):
    return read_json(Path(__file__).resolve().parents[2] / "schema" / (name + ".schema.json"))


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise InputError("expected a nonempty relative POSIX path")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or ":" in value or str(p) != value or value == ".":
        raise InputError("invalid relative path: " + value)
    return value


@dataclass(frozen=True)
class Evidence:
    path: str
    line: int
    raw: str

    @property
    def location(self):
        return "{}:{}".format(self.path, self.line)

    def as_dict(self):
        return {"declared_at": self.location, "declared_as": self.raw}


class Source:
    def __init__(self, path, text):
        self.path = path
        self.text = text

    def evidence(self, start, end):
        return Evidence(self.path, self.text.count("\n", 0, start) + 1, self.text[start:end])

    def line(self, number):
        lines = self.text.splitlines()
        return Evidence(self.path, number, lines[number - 1] if number <= len(lines) else "")


class JsonSource(Source):
    """Strict JSON decoding plus JSON-path -> exact source span indexing."""
    def __init__(self, source):
        super().__init__(source.path, source.text)
        self.data = loads(self.text)
        self.spans = {}
        decoder = json.JSONDecoder()

        def ws(i):
            while i < len(self.text) and self.text[i].isspace():
                i += 1
            return i

        def visit(i, pointer):
            i = ws(i)
            start = i
            char = self.text[i]
            if char == "{":
                i = ws(i + 1)
                while self.text[i] != "}":
                    key, i = decoder.raw_decode(self.text, i)
                    i = visit(ws(i) + 1, pointer + (key,))
                    i = ws(i)
                    if self.text[i] == ",":
                        i = ws(i + 1)
                i += 1
            elif char == "[":
                i, index = ws(i + 1), 0
                while self.text[i] != "]":
                    i = ws(visit(i, pointer + (index,)))
                    index += 1
                    if self.text[i] == ",":
                        i = ws(i + 1)
                i += 1
            else:
                _, i = decoder.raw_decode(self.text, i)
            self.spans[pointer] = (start, i)
            return i

        visit(0, ())

    def at(self, pointer=()):
        return self.evidence(*self.spans[tuple(pointer)])


CHECKS = {
    "repo": "CHK-REPO-REVISION",
    "repo_ref": "CHK-REPO-EXISTS",
    "component": "CHK-COMPONENT-RESOLVABLE",
    "toolchain": "CHK-TOOLCHAIN-IDENTITY",
    "config": "CHK-CONFIG-EXISTS",
    "arch": "CHK-ARCH-MAP",
}


def requirement(module, kind, name, evidence, confidence="high", route=None):
    return dict(kind=kind, name=name, extractor=module, confidence=confidence,
                check=CHECKS[route or kind], **evidence.as_dict())


CONFIG_KEYS = {"product", "board", "product_config", "manifest", "toolchain_files",
               "kernel_files", "arch_map", "legacy_fixture", "source_set_id", "repo_profile",
               "openharmony_profile", "s2_context", "s3_context", "local_manifest_policy"}


class Context:
    def __init__(self, filelist, config):
        validate_contract(filelist, contract("filelist"))
        if not isinstance(config, dict) or set(config) - CONFIG_KEYS:
            raise InputError("run config must be an object with only documented keys")
        self.filelist, self.config = filelist, dict(config)
        for key in ('s2_context', 's3_context', 'local_manifest_policy'):
            if key in config and not isinstance(config[key], dict):
                raise InputError(key + ' must be an object')
        if 'legacy_fixture' in config and type(config['legacy_fixture']) is not bool:
            raise InputError('legacy_fixture must be boolean')
        self.independent_results = {}
        self.root = Path(filelist["meta"]["source_root"])
        if not self.root.is_absolute() or not self.root.is_dir():
            raise InputError("source_root must be an existing absolute directory")
        self.root = self.root.resolve()
        self.files = {}
        for entry in filelist["files"]:
            path = relative_path(entry["path"])
            if path in self.files:
                raise InputError("duplicate filelist path: " + path)
            self.files[path] = entry["kind"]
        kinds = Counter(self.files.values())
        stats = filelist["stats"]
        if stats["total_files_seen"] != len(self.files) or any(
            type(n) is not int or n < 0 or n != kinds.get(k, 0)
            for k, n in stats["by_kind"].items()
        ) or any(stats["by_kind"].get(k, 0) != n for k, n in kinds.items()):
            raise InputError("filelist stats do not match files")
        for key in ("product", "board"):
            if key in config and (not isinstance(config[key], str) or not config[key]):
                raise InputError(key + " must be a nonempty string")
        for key in ("product_config", "manifest"):
            if key in config:
                relative_path(config[key])
        for key in ("toolchain_files", "kernel_files"):
            paths = config.get(key, [])
            if not isinstance(paths, list):
                raise InputError(key + " must be a list")
            for path in paths:
                relative_path(path)
            if len(set(paths)) != len(paths):
                raise InputError(key + " contains duplicate paths")
        mapping = config.get("arch_map", {})
        if not isinstance(mapping, dict) or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            for k, v in mapping.items()
        ):
            raise InputError("arch_map must map nonempty strings to nonempty strings")
        self.sources, self.json_sources = {}, {}
        self.diagnostics, self.relationships, self.projects = [], [], []
        self.claimed, self.parsed = {}, set()
        self._messages = set()

    def diagnose(self, module, code, message, evidence=None, incomplete=True, level="warning", **details):
        item = dict(module=module, code=code, level=level, message=message,
                    affects_completeness=incomplete)
        if evidence is not None:
            item.update(evidence.as_dict())
        item.update(details)
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key not in self._messages:
            self._messages.add(key)
            self.diagnostics.append(item)

    def source(self, path, module):
        self.claimed.setdefault(module, set()).add(path)
        if path not in self.files:
            self.diagnose(module, "not_in_filelist", "selected input is not in filelist", path=path)
            return None
        if path not in self.sources:
            try:
                absolute = (self.root / path).resolve()
                absolute.relative_to(self.root)
                # Preserve CRLF in source excerpts; source locations are not a
                # normalized reserialization of the user's input.
                with absolute.open(encoding="utf-8", newline="") as stream:
                    text = stream.read()
                self.sources[path] = Source(path, text)
                self.parsed.add(path)
            except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
                self.sources[path] = str(exc)
        src = self.sources[path]
        if isinstance(src, str):
            self.diagnose(module, "read_error", src, path=path, level="error")
            return None
        return src

    def json(self, path, module):
        source = self.source(path, module)
        if source is None:
            return None
        if path not in self.json_sources:
            try:
                self.json_sources[path] = JsonSource(source)
            except (ValueError, RecursionError) as exc:
                self.json_sources[path] = (str(exc), getattr(exc, "lineno", 1))
        doc = self.json_sources[path]
        if isinstance(doc, tuple):
            self.diagnose(module, "invalid_json", doc[0], source.line(doc[1]), level="error")
            return None
        if not isinstance(doc.data, dict):
            self.diagnose(module, "invalid_shape", "JSON root must be an object", doc.at(), level="error")
            return None
        return doc

    def product(self, module):
        path = self.config.get("product_config")
        if not path:
            self.diagnose(module, "no_product", "product_config must explicitly select a product")
            return None
        return self.json(path, module)

    def paths(self, kind):
        return sorted(path for path, actual in self.files.items() if actual == kind)
