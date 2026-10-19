"""Stages 4 and 5: recognize/filter tool literals, then annotate requirements."""

from dataclasses import dataclass
from pathlib import Path
import re
import shlex

from .s1_literals import ExecutableLiteral, LiteralBatch, ReferenceLiteral, UnresolvedLiteral


BARE_TOOL = re.compile(r"[A-Za-z_][A-Za-z_0-9.+-]*\Z")


@dataclass(frozen=True)
class RecognizedTool:
    literal: ExecutableLiteral

    @property
    def name(self):
        return self.literal.token.value


@dataclass(frozen=True)
class RecognizedBatch:
    tools: tuple[RecognizedTool, ...]
    references: tuple[ReferenceLiteral, ...]
    unresolved: tuple[UnresolvedLiteral, ...]


def recognize_tools(literals: LiteralBatch, *, denylist, custom_denylist=False) -> RecognizedBatch:
    """Select tools from literal-stage output; no report fields are constructed."""
    tools, references, unresolved = [], list(literals.references), list(literals.unresolved)
    for literal in literals.values:
        token, name = literal.token, literal.token.value
        if literal.bare_only and not BARE_TOOL.fullmatch(name):
            continue
        if name in denylist and (not literal.external or custom_denylist):
            continue
        if not literal.external and (name in literal.functions or name in {"[[", "]]", "]"}):
            continue
        if name.startswith(("./", "../", "//")) or ("/" in name and not name.startswith("/")):
            references.append(ReferenceLiteral(name, literal.path, token.line, literal.source_type))
            continue
        if name.startswith("~"):
            unresolved.append(UnresolvedLiteral(literal.path, token.line, literal.expression or token.raw,
                                                "Home-directory expansion is environment dependent"))
            continue
        tools.append(RecognizedTool(literal))
    return RecognizedBatch(tuple(tools), tuple(references), tuple(unresolved))


def annotate_tool(tool: RecognizedTool, *, source_root: Path, shell_words, problem_map) -> dict:
    """Attach provenance, confidence and verifier descriptors after recognition."""
    literal, name = tool.literal, tool.name
    confidence = ("low" if literal.source_context == "gn_argument"
                  else "high" if literal.source_context in {"python_literal", "main_recipe", "build_entry"}
                  else "medium")
    verify_cmd = "command -v " + shlex.quote(name)
    if literal.external and name in shell_words:
        code = "import shutil,sys; sys.exit(0 if shutil.which(sys.argv[1]) else 1)"
        verify_cmd = "python3 -c " + shlex.quote(code) + " " + shlex.quote(name)
    return {
        "kind": "tool", "name": name,
        "declared_at": literal.path.relative_to(source_root).as_posix() + ":" + str(literal.token.line),
        "declared_as": literal.expression,
        "extractor": "S1-tool", "confidence": confidence, "check": "CHK-EXISTS-CMD",
        "verify_cmd": verify_cmd, "requires_external_binary": literal.external,
        "maps_to": list(problem_map.get(name.rsplit("/", 1)[-1], [])), "source_type": literal.source_type,
    }
