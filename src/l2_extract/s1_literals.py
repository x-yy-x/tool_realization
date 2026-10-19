"""Stage 3: extract literal executable words and argv, without tool/report policy.

The input is ParsedSource, never a filesystem root. Dynamic expressions are
recorded as unresolved. No variables, AST expressions or commands are evaluated.
"""

import ast
from dataclasses import dataclass, replace
from pathlib import Path
import re

from .shelltoken import Command, Token, unwrap
from .s1_parse import ParsedSource, ParseIssue, SourceDocument, _make_to_shell, parse_source


PY_CALLS = {
    "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
    "subprocess.Popen", "subprocess.getoutput", "subprocess.getstatusoutput",
    "os.system", "os.popen", "os.execv", "os.execvp", "os.execve", "os.execvpe",
    "os.execl", "os.execlp", "shutil.which",
}


def _literal(node):
    # Deliberately do not evaluate concatenation, names, f-strings or arbitrary AST.
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


@dataclass(frozen=True)
class ExecutableLiteral:
    token: Token
    path: Path
    source_context: str
    source_type: str
    expression: str
    functions: frozenset[str] = frozenset()
    external: bool = False
    bare_only: bool = False
    argv_tokens: tuple[Token, ...] = ()

    @property
    def argv(self):
        return tuple(token.value for token in (self.argv_tokens or (self.token,)))


@dataclass(frozen=True)
class ReferenceLiteral:
    value: str
    path: Path
    line: int
    source_type: str
    recurse: bool = False


@dataclass(frozen=True)
class UnresolvedLiteral:
    path: Path
    line: int
    expression: str
    reason: str


@dataclass(frozen=True)
class LiteralBatch:
    values: tuple[ExecutableLiteral, ...]
    references: tuple[ReferenceLiteral, ...]
    unresolved: tuple[UnresolvedLiteral, ...]
    diagnostics: tuple[ParseIssue, ...]


class _LiteralCollector:
    def __init__(self, parsed, source_lines=None):
        self.source_lines = parsed.document.text.splitlines() if source_lines is None else source_lines
        self.values, self.references, self.unresolved, self.diagnostics = [], [], [], []

    def context(self, path, line, fallback=""):
        if 0 < line <= len(self.source_lines):
            end = line
            while end < len(self.source_lines) and self.source_lines[end - 1].endswith(chr(92)):
                end += 1
            return "\n".join(self.source_lines[line - 1:end])
        return fallback

    def diagnostic(self, path, line, code, message):
        self.diagnostics.append(ParseIssue(path, line, code, message))

    def uncertain(self, path, line, expression, reason):
        self.unresolved.append(UnresolvedLiteral(path, line, expression, reason))

    def literal(self, token, path, confidence, source, declared_as=None, functions=(), external=False, *, bare_only=False, argv_tokens=()):
        # This stage records constant executable words, including words that a
        # later recognition policy may suppress (builtins, functions, etc.).
        expression = self.context(path, token.line) if declared_as is None else declared_as
        if token.dynamic:
            self.uncertain(path, token.line, expression or token.raw, "Dynamic command name; not guessed")
        elif token.value:
            # Retain source facts here. Only annotate_tool maps these facts to
            # the final high/medium/low confidence field.
            context = ("gn_argument" if source == "gn_args" else "python_literal" if source == "python"
                       else "main_recipe" if source == "make" and confidence == "high"
                       else "build_entry" if confidence == "high" else "ordinary_script")
            self.values.append(ExecutableLiteral(token, path, context, source, expression,
                                                 frozenset(functions), external, bare_only, tuple(argv_tokens)))

    def reference(self, value, path, line, *, source, recurse):
        self.references.append(ReferenceLiteral(value, path, line, source, recurse))

    def consume(self, parsed):
        self.diagnostics.extend(parsed.issues)
        language = parsed.document.language
        if language == "shell":
            self.shell_literals(parsed)
        elif language == "python":
            self.python_literals(parsed)
        elif language == "make":
            for recipe in parsed.recipes:
                self.shell(_make_to_shell(recipe.text), parsed.document.path,
                           line_offset=recipe.line - 1, source="make",
                           confidence="high" if recipe.is_main else "medium")
        elif language == "gn":
            self.gn_literals(parsed)

    def shell(self, text, path, *, line_offset=0, confidence="medium", source="shell", main_lines=(), depth=0, origin=None):
        self.consume(parse_source(SourceDocument(path, text, "shell", source, confidence,
                                                line_offset, depth, origin, tuple(main_lines))))

    def python(self, text, path, *, line_offset=0, depth=0, origin=None):
        self.consume(parse_source(SourceDocument(path, text, "python", "python", "high",
                                                line_offset, depth, origin)))

    def shell_literals(self, parsed):
        document = parsed.document
        path, origin, depth = document.path, document.origin, document.depth
        source, confidence = document.source_type or "shell", document.confidence_hint
        for token in parsed.tokens:
            if token.kind == "heredoc" and token.dynamic and ("$" in token.value or "`" in token.value):
                self.uncertain(path, token.line, token.raw, "Expandable here-document body is not analysed")
        for command in parsed.commands:
            if origin:
                command = Command(tuple(replace(t, line=origin[0]) for t in command.tokens), origin[0], origin[1])
            level = "high" if command.line in document.main_lines else confidence
            if command.tokens[0].value in parsed.functions and not command.tokens[0].dynamic:
                # A local function is not wrapper syntax; defer suppressing its
                # constant name to recognition, without unwrapping its arguments.
                self.literal(command.tokens[0], path, level, source, declared_as=command.text,
                             functions=parsed.functions, argv_tokens=command.tokens)
                continue
            for invocation in unwrap(command):
                head = invocation.head
                evidence = origin[1] if origin else self.context(path, head.line) if source == "make" else command.text
                self.literal(head, path, level, source, declared_as=evidence, functions=parsed.functions,
                             external=invocation.external, argv_tokens=invocation.tokens)
                if invocation.reason:
                    self.uncertain(path, head.line, command.text, invocation.reason)
                self.inline_command(invocation.tokens, path, level, source, depth, origin=origin)

    def inline_command(self, tokens, path, confidence, source, depth, *, origin=None):
        if not tokens or tokens[0].dynamic:
            return
        name = tokens[0].value.rsplit("/", 1)[-1]
        python = re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", name) is not None
        if name not in {"sh", "bash", "dash", "zsh", "ksh"} and not python:
            return
        i = 1
        while i < len(tokens):
            token, code, has_c = tokens[i], None, False
            if token.dynamic:
                self.uncertain(path, token.line, origin[1] if origin else token.raw,
                               "Dynamic interpreter options; -c payload is not guessed")
                return
            word = token.value
            # Options after a script filename or -- are script arguments.
            if word in {"-", "--"} or not word.startswith(("-", "+")):
                return
            if python:
                if word in {"-m", "-h", "-V", "-?", "--help", "--version"} or word.startswith("--help-"):
                    return
                if word == "--check-hash-based-pycs":
                    i += 2
                    continue
                if word.startswith("--check-hash-based-pycs="):
                    i += 1
                    continue
                if word.startswith("--") or word.startswith("+"):
                    self.uncertain(path, token.line, origin[1] if origin else token.raw,
                                   "Unsupported interpreter option: " + word)
                    return
                # Python accepts -cCODE; -W/-X consume the rest of their word.
                position = 1
                while position < len(word):
                    option = word[position]
                    if option in {"m", "h", "V", "?"}:
                        return
                    if option in {"W", "X"}:
                        i += int(position + 1 == len(word))
                        break
                    if option == "c":
                        has_c = True
                        if position + 1 < len(word):
                            code = replace(token, value=word[position + 1:])
                        elif i + 1 < len(tokens):
                            code = tokens[i + 1]
                        break
                    if option not in "bBdEiIOqsSuvxP":
                        self.uncertain(path, token.line, origin[1] if origin else token.raw,
                                       "Unsupported interpreter option: " + word)
                        return
                    position += 1
            else:
                if word in {"--help", "--version"}:
                    return
                if word in {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}:
                    i += 2
                    continue
                if word.startswith("--"):
                    if word not in {"--norc", "--noprofile", "--login", "--posix", "--restricted",
                                    "--verbose", "--debugger", "--dump-po-strings", "--dump-strings"}:
                        self.uncertain(path, token.line, origin[1] if origin else token.raw,
                                       "Unsupported interpreter option: " + word)
                        return
                elif word.startswith("-") and "c" in word[1:]:
                    has_c = True
                    code = tokens[i + 1] if i + 1 < len(tokens) else None
                elif any(option not in "abefhiklmnprstuvxBCEHPT" for option in word[1:]):
                    self.uncertain(path, token.line, origin[1] if origin else token.raw,
                                   "Unsupported interpreter option: " + word)
                    return
            if code is not None:
                if code.dynamic:
                    self.uncertain(path, code.line, origin[1] if origin else code.raw,
                                   "Dynamic interpreter -c payload")
                elif python:
                    self.python(code.value, path, line_offset=code.line - 1,
                                depth=depth + 1, origin=origin)
                else:
                    self.shell(code.value, path, line_offset=code.line - 1,
                               confidence=confidence, source=source, depth=depth + 1, origin=origin)
                return
            if has_c:
                self.uncertain(path, token.line, origin[1] if origin else token.raw,
                               "Missing interpreter -c payload")
                return
            i += 1

    def python_literals(self, parsed):
        document = parsed.document
        text, path = document.text, document.path
        line_offset, depth, origin = document.line_offset, document.depth, document.origin
        for node, fn, proven in parsed.python_calls:
            if fn not in PY_CALLS:
                continue
            args, unknown_keywords = {}, False
            for keyword in node.keywords:
                if keyword.arg is not None:
                    args[keyword.arg] = keyword.value
                elif isinstance(keyword.value, ast.Dict) and all(_literal(key) is not None for key in keyword.value.keys):
                    args.update((_literal(key), value) for key, value in zip(keyword.value.keys, keyword.value.values))
                else:
                    unknown_keywords = True
            argument = node.args[0] if node.args else next((args[k] for k in ("args", "cmd", "command", "path", "file") if k in args), None)
            line = origin[0] if origin else node.lineno + line_offset
            expression = origin[1] if origin else ast.get_source_segment(text, node) or fn
            if argument is None and not unknown_keywords:
                continue
            if not proven:
                self.uncertain(path, line, expression, "Python callable binding is unproven or may be shadowed")
                continue
            popen_call = fn.startswith("subprocess.") and fn not in {"subprocess.getoutput", "subprocess.getstatusoutput"}
            # subprocess.run/call/check_* forward their positional arguments to Popen.
            positional = node.args if not any(isinstance(a, ast.Starred) for a in node.args) else []
            executable = args.get("executable", positional[2] if popen_call and len(positional) > 2 else None) if popen_call else None
            if isinstance(executable, ast.Constant) and executable.value is None:
                executable = None
            if executable is not None and _literal(executable) is None:
                self.uncertain(path, line, expression, "Dynamic Python executable= override")
                continue
            if executable is not None:
                self.literal(Token(_literal(executable), _literal(executable), line), path, "high", "python",
                          declared_as=expression, external=True)
            if unknown_keywords or any(isinstance(a, ast.Starred) for a in node.args):
                self.uncertain(path, line, expression, "Python expanded arguments may change shell/executable semantics")
                continue
            shell_flag = args.get("shell", positional[8] if popen_call and len(positional) > 8 else None) if popen_call else None
            shell_call = fn in {"os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput"}
            if shell_flag is not None:
                if not isinstance(shell_flag, ast.Constant):
                    self.uncertain(path, line, expression, "Dynamic shell= argument")
                    continue
                shell_call = bool(shell_flag.value)
            call_origin = (line, expression)
            if shell_call:
                payload = argument.elts[0] if isinstance(argument, (ast.List, ast.Tuple)) and argument.elts else argument
                value = _literal(payload)
                if value is None:
                    self.uncertain(path, line, expression, "Dynamic or empty Python shell command")
                elif executable is None or _literal(executable).rsplit("/", 1)[-1] in {"sh", "bash", "dash", "zsh", "ksh"}:
                    self.shell(value, path, line_offset=line - 1, confidence="high", source="python",
                               depth=depth + 1, origin=call_origin)
                elif re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", _literal(executable).rsplit("/", 1)[-1]):
                    self.python(value, path, line_offset=line - 1, depth=depth + 1, origin=call_origin)
                else:
                    self.uncertain(path, line, expression, "Shell executable override is not a recognised shell/interpreter")
                continue
            if isinstance(argument, (ast.List, ast.Tuple)):
                if not argument.elts or _literal(argument.elts[0]) is None and executable is None:
                    self.uncertain(path, line, expression, "Dynamic or empty Python argv")
                    continue
                tokens = tuple(Token(_literal(a) if _literal(a) is not None else (ast.get_source_segment(text, a) or "<?>"),
                                     ast.get_source_segment(text, a) or "", line, dynamic=_literal(a) is None)
                               for a in argument.elts)
                if executable is not None:
                    tokens = (Token(_literal(executable), _literal(executable), line),) + tokens[1:]
                command = Command(tokens, line, expression)
                for invocation in unwrap(command, external=True):
                    self.literal(invocation.head, path, "high", "python", declared_as=expression,
                                 external=True, argv_tokens=invocation.tokens)
                    if invocation.reason:
                        self.uncertain(path, line, expression, invocation.reason)
                    self.inline_command(invocation.tokens, path, "high", "python", depth, origin=call_origin)
            elif _literal(argument) is not None:
                value = _literal(argument)
                # With shell=False a string is an executable path, not a shell line.
                if executable is not None:
                    value = _literal(executable)
                self.literal(Token(value, value, line), path, "high", "python", declared_as=expression, external=True)
            else:
                self.uncertain(path, line, expression, "Python concatenation, f-string or indirect reference")

    def gn_literals(self, parsed):
        document = parsed.document
        path, text = document.path, document.text
        for assignment in parsed.assignments:
            field, value = assignment.field, assignment.value
            augmented = assignment.operator != "="
            if field.value == "args" and assignment.operator == "-=":
                self.uncertain(path, field.line, self.context(path, field.line),
                               "GN args subtraction removes candidates; list is not analysed as commands")
            elif field.value == "script":
                if value.kind == "string" and not value.dynamic and not assignment.concatenated and not augmented:
                    self.reference(value.value, path, field.line, source="gn", recurse=True)
                else:
                    self.uncertain(path, field.line, self.context(path, field.line),
                                   "Augmented GN script assignment is not a complete path" if augmented else "Dynamic GN script path")
            elif assignment.elements is not None:
                for element in assignment.elements:
                    if len(element) == 1 and element[0].kind == "string":
                        candidate = element[0]
                        if candidate.dynamic:
                            self.uncertain(path, candidate.line, candidate.raw, "Dynamic GN args element")
                        else:
                            # Even flags/data strings survive this boundary.
                            # Whether they look like tools is recognition policy.
                            self.literal(candidate, path, "low", "gn_args", bare_only=True)
                    else:
                        self.uncertain(path, element[0].line, text[element[0].start:element[-1].end],
                                       "GN args expression is not a direct string; not guessed")
                if assignment.concatenated:
                    self.uncertain(path, field.line, self.context(path, field.line), "Dynamic GN args list expression")
            else:
                self.uncertain(path, field.line, self.context(path, field.line), "Dynamic GN args assignment")


def extract_literals(parsed: ParsedSource, *, source_lines=None) -> LiteralBatch:
    """Expose stage-3 output independently of filtering and report generation.

    Shell/Make contribute executable-position words; Python contributes constants
    from proven process calls; GN contributes direct script/args string elements.
    Embedded interpreter payloads re-enter parse_source before literal extraction.
    """
    collector = _LiteralCollector(parsed, source_lines)
    collector.consume(parsed)
    return LiteralBatch(tuple(collector.values), tuple(collector.references),
                        tuple(collector.unresolved), tuple(collector.diagnostics))
