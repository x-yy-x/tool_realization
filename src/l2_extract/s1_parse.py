"""Stage 2: classify and parse source syntax, without producing tool requirements."""

import ast
from dataclasses import dataclass
from pathlib import Path
import re

from .shelltoken import Command, ShellSyntaxError, Token, function_names, iter_commands, tokenize


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    text: str
    language: str
    source_type: str = ""
    confidence_hint: str = "medium"
    line_offset: int = 0
    depth: int = 0
    origin: tuple[int, str] | None = None
    main_lines: tuple[int, ...] = ()


@dataclass(frozen=True)
class ParseIssue:
    path: Path
    line: int
    code: str
    message: str


@dataclass(frozen=True)
class Recipe:
    line: int
    text: str
    is_main: bool


@dataclass(frozen=True)
class GNAssignment:
    field: Token
    operator: str
    value: Token
    concatenated: bool = False
    elements: tuple[tuple[Token, ...], ...] | None = None


@dataclass(frozen=True)
class ParsedSource:
    document: SourceDocument
    tokens: tuple[Token, ...] = ()
    commands: tuple[Command, ...] = ()
    functions: frozenset[str] = frozenset()
    python_calls: tuple = ()
    recipes: tuple[Recipe, ...] = ()
    assignments: tuple[GNAssignment, ...] = ()
    issues: tuple[ParseIssue, ...] = ()


def classify_source(path: Path) -> str | None:
    if path.suffix == ".sh":
        return "shell"
    if path.name in {"Makefile", "makefile", "GNUmakefile"} or path.suffix == ".mk":
        return "make"
    if path.suffix == ".py":
        return "python"
    if path.name == "BUILD.gn" or path.suffix == ".gni":
        return "gn"
    return None


def _parse_recipes(document):
    text, path = document.text, document.path
    one_shell = bool(re.search(r"^\.ONESHELL\s*:", text, re.MULTILINE))
    default = re.search(r"^\.DEFAULT_GOAL\s*(?::=|=)\s*([^\s#$]+)\s*(?:#.*)?$", text, re.MULTILINE)
    main_target = default.group(1) if default else None
    recipes, current, target, rule, issues = [], [], None, 0, []
    start, continuation, define_depth = 0, False, 0
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("\t") or continuation:
            if define_depth:
                continue
            recipe = line[1:] if line.startswith("\t") else line
            if not continuation:
                recipe = recipe.lstrip("@+-")
                start = number
            current.append(recipe)
            continuation = recipe.endswith("\\")
            if not continuation:
                recipes.append((target, rule, start, "\n".join(current)))
                current = []
        else:
            stripped = line.strip()
            if re.match(r"^(?:override\s+|export\s+)?define\s", stripped):
                define_depth += 1
                continue
            if stripped == "endef" and define_depth:
                define_depth -= 1
                continue
            if define_depth:
                continue
            match = re.match(r"^([^\s:#=][^:=]*):(?!=)", line)
            if match:
                rule += 1
                target = match.group(1).split()[0]
                if main_target is None and not target.startswith(".") and "%" not in target:
                    main_target = target
            if line.startswith(".RECIPEPREFIX"):
                issues.append(ParseIssue(path, number, "make_recipe_prefix", "Only TAB-prefixed recipes are supported"))
    if current:
        recipes.append((target, rule, start, "\n".join(current)))
    if one_shell:
        groups = []
        for target, rule, start, recipe in recipes:
            if groups and groups[-1][1] == rule:
                _, _, prior_start, prior_recipe = groups[-1]
                padding = start - prior_start - prior_recipe.count("\n")
                groups[-1] = (target, rule, prior_start, prior_recipe + "\n" * padding + recipe)
            else:
                groups.append((target, rule, start, recipe))
        recipes = groups
    return tuple(Recipe(line, source, target == main_target and main_target is not None)
                 for target, _, line, source in recipes), tuple(issues)


def _parse_gn_assignments(tokens, assignments):
    for i, token in enumerate(tokens):
        if (token.kind != "word" or token.value not in {"action", "action_foreach"}
                or i + 1 >= len(tokens) or tokens[i + 1].value != "("):
            continue
        close = _closing(tokens, i + 1)
        if close + 1 >= len(tokens) or tokens[close + 1].value != "{":
            continue
        end = _closing(tokens, close + 1)
        body = tokens[close + 2:end]
        for j, field in enumerate(body):
            if field.kind != "word" or field.value not in {"script", "args"}:
                continue
            k = j + 1
            augmented = k < len(body) and body[k].value in {"+", "-"}
            operator = body[k].value + "=" if augmented else "="
            if augmented:
                k += 1
            if k + 1 >= len(body) or body[k].value != "=":
                continue
            value = body[k + 1]
            concatenated = k + 2 < len(body) and body[k + 2].value in {"+", "("}
            elements = None
            if field.value == "args" and value.kind == "op" and value.value == "[":
                list_end = _closing(body, k + 1)
                parts, element, nesting = [], [], 0
                for part in body[k + 2:list_end] + [Token(",", ",", field.line, kind="op")]:
                    if part.kind == "op" and part.value in "([{":
                        nesting += 1
                    elif part.kind == "op" and part.value in ")]}":
                        nesting -= 1
                    if part.value == "," and part.kind == "op" and nesting == 0:
                        if element:
                            parts.append(tuple(element))
                        element = []
                    else:
                        element.append(part)
                elements = tuple(parts)
                concatenated = list_end + 1 < len(body) and body[list_end + 1].value in {"+", "-"}
            assignments.append(GNAssignment(field, operator, value, concatenated, elements))


def parse_source(document: SourceDocument) -> ParsedSource:
    """Return only language syntax and parse issues; no denylist or output policy."""
    if document.depth > 16:
        line = document.origin[0] if document.origin else document.line_offset + 1
        return ParsedSource(document, issues=(ParseIssue(document.path, line, "inline_depth", "Inline command nesting exceeds 16"),))
    try:
        if document.language == "shell":
            tokens = tokenize(document.text, document.line_offset)
            return ParsedSource(document, tuple(tokens),
                                tuple(iter_commands(document.text, document.line_offset, _tokens=tokens)),
                                frozenset(function_names(document.text, _tokens=tokens)))
        if document.language == "make":
            recipes, issues = _parse_recipes(document)
            return ParsedSource(document, recipes=recipes, issues=issues)
        if document.language == "python":
            tree = ast.parse(document.text)
            return ParsedSource(document, python_calls=tuple(_python_calls(tree)))
        if document.language == "gn":
            assignments = []
            try:
                tokens = _gn_tokens(document.text)
                _parse_gn_assignments(tokens, assignments)
            except ShellSyntaxError as exc:
                return ParsedSource(document, assignments=tuple(assignments),
                                    issues=(ParseIssue(document.path, exc.line, "gn_syntax", str(exc)),))
            return ParsedSource(document, tokens=tuple(tokens), assignments=tuple(assignments))
        return ParsedSource(document, issues=(ParseIssue(document.path, 1, "unsupported_input", "Unsupported input type"),))
    except (ShellSyntaxError, SyntaxError, ValueError, RecursionError) as exc:
        line = document.origin[0] if document.origin else (getattr(exc, "line", getattr(exc, "lineno", 1)) or 1)
        if not document.origin and document.language == "python":
            line += document.line_offset
        return ParsedSource(document, issues=(ParseIssue(document.path, line, document.language + "_syntax", str(exc)),))


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return parent + "." + node.attr if parent else ""
    return ""


class _PythonScope:
    """Bindings owned by one lexical scope; never leak local imports outward."""

    def __init__(self, nodes, parent=None, parameters=(), kind="function"):
        self.parent, self.kind, self.bindings = parent, kind, {}
        for parameter in parameters:
            self.bind(parameter, None)
        for node in nodes:
            self.collect(node)

    def bind(self, name, target):
        self.bindings.setdefault(name, set()).add(target)

    def collect(self, node):
        if isinstance(node, ast.Import):
            for alias in node.names:
                self.bind(alias.asname or alias.name.split(".", 1)[0],
                          alias.name if alias.asname else alias.name.split(".", 1)[0])
            return
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    self.bind(alias.asname or alias.name,
                              node.module + "." + alias.name if node.module and not node.level else None)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self.bind(node.name, None)
            return
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id, None)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(_dotted(node).split(".", 1)[0], None)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self.bind(node.name, None)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            self.bind(node.name, None)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            self.bind(node.rest, None)
        for child in ast.iter_child_nodes(node):
            self.collect(child)

    def resolve(self, name):
        if name in self.bindings:
            values = self.bindings[name]
            known = sorted(value for value in values if value is not None)
            if known:
                return known[0], len(values) == 1
            inherited, _ = self.parent.resolve(name) if self.parent else (name, False)
            return inherited, False
        return self.parent.resolve(name) if self.parent else (name, False)


def _python_calls(tree):
    """Yield calls with their import binding, accounting for lexical scopes."""
    calls = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = _PythonScope(tree.body, kind="module")

        def visit_Call(self, node):
            dotted = _dotted(node.func)
            root, _, rest = dotted.partition(".")
            target, proven = self.scope.resolve(root)
            calls.append((node, target + ("." + rest if rest else ""), proven))
            self.generic_visit(node)

        def function(self, node):
            for decorator in getattr(node, "decorator_list", ()):
                self.visit(decorator)
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                self.visit(default)
            arguments = (node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                         + ([node.args.vararg] if node.args.vararg else [])
                         + ([node.args.kwarg] if node.args.kwarg else []))
            for argument in arguments:
                if argument.annotation:
                    self.visit(argument.annotation)
            if getattr(node, "returns", None):
                self.visit(node.returns)
            body = node.body if isinstance(node.body, list) else [node.body]
            previous = self.scope
            parent = previous.parent if previous.kind == "class" else previous
            self.scope = _PythonScope(body, parent, (a.arg for a in arguments))
            for child in body:
                self.visit(child)
            self.scope = previous

        visit_FunctionDef = function
        visit_AsyncFunctionDef = function
        visit_Lambda = function

        def visit_ClassDef(self, node):
            for child in node.decorator_list + node.bases + [k.value for k in node.keywords]:
                self.visit(child)
            previous = self.scope
            self.scope = _PythonScope(node.body, previous, kind="class")
            for child in node.body:
                self.visit(child)
            self.scope = previous

        def comprehension(self, node):
            # Python evaluates the first iterable in the enclosing scope.
            self.visit(node.generators[0].iter)
            previous = self.scope
            parent = previous.parent if previous.kind == "class" else previous
            self.scope = _PythonScope([g.target for g in node.generators], parent)
            for index, generator in enumerate(node.generators):
                if index:
                    self.visit(generator.iter)
                for condition in generator.ifs:
                    self.visit(condition)
            for child in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                self.visit(child)
            self.scope = previous

        visit_ListComp = comprehension
        visit_SetComp = comprehension
        visit_DictComp = comprehension
        visit_GeneratorExp = comprehension

    Visitor().visit(tree)
    return calls


def _make_to_shell(text: str) -> str:
    """Preserve Make variables as opaque expansions; $$ becomes one shell $."""
    out, i = [], 0
    while i < len(text):
        if text.startswith("$$", i):
            out.append("$")
            i += 2
        elif text.startswith(("$(", "${"), i):
            opening = text[i + 1]
            closing = ")" if opening == "(" else "}"
            depth, end = 1, i + 2
            while end < len(text) and depth:
                depth += (text[end] == opening) - (text[end] == closing)
                end += 1
            value = text[i + 2:end - 1]
            # A nested Make expression is opaque; its $(VAR) is not a shell command.
            name = value if re.fullmatch(r"[A-Za-z_]\w*", value) else "MAKE_EXPRESSION"
            out.append("${" + name + "}" if depth == 0 else text[i:end])
            i = end
        elif text[i] == "$" and i + 1 < len(text):
            # Make's short variable form consumes one character, unlike shell $NAME.
            out.append("${" + text[i + 1] + "}")
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _gn_tokens(text: str) -> list[Token]:
    """GN lexical tokens; quoted braces/comments do not affect block matching."""
    tokens, i, line = [], 0, 1
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            line += ch == "\n"
            i += 1
            continue
        if ch == "#":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        start, first_line = i, line
        if ch == '"':
            i += 1
            value, dynamic = [], False
            while i < len(text) and text[i] != '"':
                if text[i] == "\\" and i + 1 < len(text):
                    value.append(text[i + 1])
                    line += text[i + 1] == "\n"
                    i += 2
                else:
                    dynamic |= text[i] == "$"
                    line += text[i] == "\n"
                    value.append(text[i])
                    i += 1
            if i >= len(text):
                raise ShellSyntaxError("Unclosed GN string", first_line)
            i += 1
            tokens.append(Token("".join(value), text[start:i], first_line, start, i,
                                "string", dynamic, True))
        elif ch.isalpha() or ch == "_":
            i += 1
            while i < len(text) and (text[i].isalnum() or text[i] == "_"):
                i += 1
            tokens.append(Token(text[start:i], text[start:i], first_line, start, i))
        else:
            tokens.append(Token(ch, ch, line, i, i + 1, "op"))
            i += 1
    return tokens


def _closing(tokens: list[Token], start: int) -> int:
    pairs, stack = {"(": ")", "[": "]", "{": "}"}, []
    for i in range(start, len(tokens)):
        t = tokens[i]
        if t.kind != "op":
            continue
        if t.value in pairs:
            stack.append(pairs[t.value])
        elif t.value in pairs.values():
            if not stack or stack.pop() != t.value:
                raise ShellSyntaxError("Unbalanced GN delimiter", t.line)
            if not stack:
                return i
    raise ShellSyntaxError("Unclosed GN block", tokens[start].line)
