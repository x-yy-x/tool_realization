"""Conservative shell lexer shared by S1 (tools) and S5 (link arguments).

This is a static parser, not a shell interpreter. Words retain their raw spelling,
physical line number and expansion status. ``iter_commands`` also visits command
substitutions; it never expands variables, globs or executes source text.
"""

from dataclasses import dataclass, replace
import re
from typing import Iterator

__all__ = [
    "Command", "Invocation", "ShellSyntaxError", "Token", "function_names",
    "iter_commands", "tokenize", "unwrap",
]


class ShellSyntaxError(ValueError):
    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.line = line


@dataclass(frozen=True)
class Token:
    value: str
    raw: str
    line: int
    start: int = 0
    end: int = 0
    kind: str = "word"
    dynamic: bool = False
    quoted: bool = False
    substitutions: tuple = ()


@dataclass(frozen=True)
class Command:
    tokens: tuple[Token, ...]
    line: int
    text: str

    @property
    def argv(self) -> tuple[str, ...]:
        """S5 can inspect ALL arguments, including $CC, -lfoo and -L paths."""
        return tuple(t.value for t in self.tokens)


@dataclass(frozen=True)
class Invocation:
    tokens: tuple[Token, ...]
    reason: str = ""
    external: bool = False

    @property
    def head(self) -> Token:
        return self.tokens[0]


_OPS = ("&>>", "<<-", "<<<", "&&", "||", ";;&", ";;", ";&", ">>", "<<", ">&", "<&", "&>", ">|",
        ";", "|", "&", "\n", "(", ")", "<", ">")
_REDIRECTS = {"&>>", "<<-", "<<<", ">>", "<<", ">&", "<&", "&>", ">|", "<", ">"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*(?:\[[^\]]+\])?\+?=")
_LEADING = {"if", "elif", "then", "else", "do", "while", "until", "!"}
_DECLARATIONS = {"for", "select", "case", "function", "fi", "done", "esac", "in"}


def _backtick_end(text: str, start: int, line: int) -> int:
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == "`":
            return i + 1
        else:
            i += 1
    raise ShellSyntaxError("Unclosed backtick substitution", line)


def _balanced_end(text: str, start: int, opening: str, closing: str, line: int, _depth=0,
                  *, command_context=None) -> int:
    """Find an expansion end without confusing case patterns or comments with it.

    Nested expansions have their own quote context. Arithmetic/parameter bodies
    are balanced as data; command bodies also track the shell's case delimiters.
    """
    if _depth > 32:
        raise ShellSyntaxError("Shell expansion nesting exceeds 32", line)
    command_body = ((opening == "(" and text[start:start + 1] != "(")
                    if command_context is None else command_context)
    depth, quote, i = 1, None, start
    boundary, command_start, cases = True, True, []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote != "'":
            i += 2
            continue
        if quote != "'" and (text.startswith("$(", i) or text.startswith("${", i)):
            i = _balanced_end(text, i + 2, text[i + 1], ")" if text[i + 1] == "(" else "}",
                              line + text[start:i].count("\n"), _depth + 1)
            boundary, command_start = False, False
            continue
        if ch == "`" and quote != "'":
            i = _backtick_end(text, i, line + text[start:i].count("\n"))
            boundary, command_start = False, False
            continue
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            boundary, command_start = False, False
        elif command_body and ch == "#" and boundary:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif command_body and boundary and (ch.isalpha() or ch == "_"):
            match = re.match(r"[A-Za-z_][A-Za-z_0-9]*", text[i:])
            word, end = match.group(0), i + len(match.group(0))
            if end == len(text) or text[end].isspace() or text[end] in ";|&()<>{}":
                if word == "case" and command_start:
                    cases.append(["header", depth])
                elif cases and word == "in" and cases[-1][0] == "header":
                    cases[-1][0] = "pattern"
                elif word == "esac" and cases and (command_start or cases[-1][0] == "pattern"):
                    cases.pop()
                command_start = word in _LEADING or (word == "case")
                boundary = False
                i = end
                continue
            boundary, command_start = False, False
        elif ch == opening:
            depth += 1
            boundary, command_start = True, True
        elif ch == closing:
            if command_body and cases and cases[-1][0] == "pattern" and cases[-1][1] == depth:
                cases[-1][0] = "body"
                boundary, command_start = True, True
                i += 1
                continue
            depth -= 1
            if depth == 0:
                return i + 1
            boundary, command_start = True, True
        elif command_body and (text.startswith(";;", i) or text.startswith(";&", i)):
            if cases:
                cases[-1][0] = "pattern"
            i += 3 if text.startswith(";;&", i) else 2
            boundary, command_start = True, True
            continue
        elif command_body and ch in ";\n|&":
            boundary, command_start = True, True
        elif (command_body and boundary and ch in "{}!"
              and (i + 1 == len(text) or text[i + 1].isspace() or text[i + 1] in ";()")):
            boundary, command_start = True, True
        elif ch.isspace():
            boundary = True
        else:
            boundary, command_start = False, False
        i += 1
    raise ShellSyntaxError("Unclosed shell expansion", line)


def _nested_substitutions(text: str, line: int, _depth=0, *, double_quoted=False) -> list[tuple[str, int]]:
    """Find executable substitutions inside a data expansion, never parse its data.

    In particular, ``$((count + $(nproc)))`` has one command, not a command named
    count. Parameter-expansion fallback words can contain substitutions too.
    """
    if _depth > 32:
        raise ShellSyntaxError("Shell expansion nesting exceeds 32", line)
    result, i, quote = [], 0, '"' if double_quoted else None
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote != "'":
            line += text[i:i + 2].count("\n")
            i += 2
            continue
        if ch in "\"'" and (quote is None or quote == ch):
            quote = None if quote else ch
            i += 1
            continue
        if quote != "'" and (text.startswith("$(", i) or text.startswith("${", i)):
            end = _balanced_end(text, i + 2, text[i + 1], ")" if text[i + 1] == "(" else "}", line)
            inner = text[i + 2:end - 1]
            if text.startswith("$(", i) and not text.startswith("$((", i):
                result.append((inner, line))
            else:
                result.extend(_nested_substitutions(inner, line, _depth + 1, double_quoted=quote == '"'))
            line += text[i:end].count("\n")
            i = end
            continue
        if ch == "`" and quote != "'":
            end = _backtick_end(text, i, line)
            result.append((text[i + 1:end - 1], line))
            line += text[i:end].count("\n")
            i = end
            continue
        line += ch == "\n"
        i += 1
    return result


def tokenize(text: str, line_offset: int = 0) -> list[Token]:
    """Lex shell text without losing quote/expansion information or line numbers.

    Here-document payloads are opaque tokens: literal data is never mistaken for
    executable commands. Callers can diagnose skipped, expandable here-documents.
    """
    result, heredocs = [], []
    i, line = 0, 1 + line_offset
    expect_delimiter = None
    while i < len(text):
        ch = text[i]
        if ch in " \t\r":
            i += 1
            continue
        if ch == "\\" and text[i:i + 2] == "\\\n":
            i += 2
            line += 1
            continue
        if ch == "#":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        if text.startswith("((", i):
            end = _balanced_end(text, i + 1, "(", ")", line)
            raw = text[i:end]
            result.append(Token(raw, raw, line, i, end, "arithmetic", True,
                                substitutions=tuple(_nested_substitutions(raw[2:-2], line))))
            line += raw.count("\n")
            i = end
            continue
        # Bash process substitution is one shell word, not a redirection token
        # followed by a subshell. Keep the complete spelling for S5 argv and
        # declared_as evidence, while still visiting its command body below.
        if text.startswith(("<(", ">("), i):
            end = _balanced_end(text, i + 2, "(", ")", line)
            raw = text[i:end]
            result.append(Token(raw, raw, line, i, end, dynamic=True,
                                substitutions=((raw[2:-1], line),)))
            line += raw.count("\n")
            i = end
            continue
        op = next((x for x in _OPS if text.startswith(x, i)), None)
        # Braces are operators only as standalone words (not ${VAR} or foo{a,b}).
        if ch in "{}" and (i + 1 == len(text) or text[i + 1].isspace() or text[i + 1] in ";()"):
            op = ch
        if op:
            result.append(Token(op, op, line, i, i + len(op), "op"))
            i += len(op)
            if op in {"<<", "<<-"}:
                expect_delimiter = op
            if op == "\n":
                line += 1
                for delimiter, strip_tabs, quoted in heredocs:
                    body_start, body_line = i, line
                    while i < len(text):
                        end = text.find("\n", i)
                        end = len(text) if end < 0 else end
                        value = text[i:end].rstrip("\r")
                        if (value.lstrip("\t") if strip_tabs else value) == delimiter:
                            result.append(Token(text[body_start:i], text[body_start:i], body_line,
                                                body_start, i, "heredoc", not quoted))
                            i = end + (end < len(text))
                            line += 1
                            break
                        i = end + (end < len(text))
                        line += 1
                    else:
                        raise ShellSyntaxError("Unclosed here-document", body_line)
                heredocs.clear()
            continue
        start, word_line = i, line
        value, substitutions = [], []
        quote, quoted, dynamic = None, False, False
        while i < len(text):
            ch = text[i]
            if (quote is None and ch == "(" and text[start:i].endswith("=")
                    and _ASSIGNMENT.fullmatch(text[start:i])):
                # A Bash array initializer is data, not a subshell command list.
                end = _balanced_end(text, i + 1, "(", ")", line, command_context=False)
                substitutions.extend(_nested_substitutions(text[i + 1:end - 1], line))
                value.append(text[i:end])
                line += text[i:end].count("\n")
                i = end
                continue
            if quote is None and text.startswith(("<(", ">("), i):
                end = _balanced_end(text, i + 2, "(", ")", line)
                raw = text[i:end]
                substitutions.append((raw[2:-1], line))
                value.append(raw)
                dynamic = True
                line += raw.count("\n")
                i = end
                continue
            if quote is None and (ch.isspace() or any(text.startswith(x, i) for x in _OPS)):
                break
            if ch == "\\" and quote != "'":
                if i + 1 >= len(text):
                    raise ShellSyntaxError("Trailing escape", line)
                nxt = text[i + 1]
                if nxt == "\n":
                    line += 1
                elif quote == '"' and nxt not in '$`"\\':
                    value.extend((ch, nxt))
                else:
                    value.append(nxt)
                i += 2
                continue
            if ch in "\"'" and (quote is None or quote == ch):
                quoted = True
                quote = None if quote else ch
                i += 1
                continue
            if quote != "'" and (text.startswith("$(", i) or text.startswith("${", i)):
                is_command = text[i + 1] == "("
                end = _balanced_end(text, i + 2, text[i + 1], ")" if is_command else "}", line)
                if is_command and not text.startswith("$((", i):
                    substitutions.append((text[i + 2:end - 1], line))
                else:
                    substitutions.extend(_nested_substitutions(text[i + 2:end - 1], line,
                                                              double_quoted=quote == '"'))
                value.append(text[i:end])
                line += text[i:end].count("\n")
                dynamic = True
                i = end
                continue
            if ch == "`" and quote != "'":
                end = _backtick_end(text, i, line)
                substitutions.append((text[i + 1:end - 1], line))
                value.append(text[i:end])
                line += text[i:end].count("\n")
                dynamic = True
                i = end
                continue
            if ch == "$" and quote != "'":
                dynamic = True
            if ch in "*?[" and quote is None:
                dynamic = True
            value.append(ch)
            line += ch == "\n"
            i += 1
        if quote:
            raise ShellSyntaxError("Unclosed quote", word_line)
        token = Token("".join(value), text[start:i], word_line, start, i,
                      dynamic=dynamic, quoted=quoted, substitutions=tuple(substitutions))
        if token.raw == "[":
            # A lone opening bracket has no matching glob bracket expression.
            token = replace(token, dynamic=False)
        result.append(token)
        if expect_delimiter:
            # Here-document delimiters undergo quote removal, not substitution.
            result[-1] = Token(token.value, token.raw, token.line, token.start, token.end,
                               token.kind, False, token.quoted)
            heredocs.append((token.value, expect_delimiter == "<<-", quoted))
            expect_delimiter = None
    if heredocs or expect_delimiter:
        raise ShellSyntaxError("Missing here-document body", line)
    return result


def _command(tokens: list[Token], text: str) -> Command | None:
    if not tokens:
        return None
    original = tokens[:]
    words, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if t.kind == "op" and t.value in _REDIRECTS:
            if words and words[-1].value.isdigit() and words[-1].end == t.start:
                words.pop()
            i += 2  # redirect + destination (substitutions are visited separately)
            continue
        words.append(t)
        i += 1
    while words and not words[0].quoted and words[0].value in _LEADING:
        words.pop(0)
    if not words or (not words[0].quoted and words[0].value in _DECLARATIONS):
        return None
    while words and _ASSIGNMENT.match(words[0].raw):
        words.pop(0)
    if not words:
        return None
    return Command(tuple(words), words[0].line, text[original[0].start:original[-1].end])


def iter_commands(text: str, line_offset: int = 0, _depth: int = 0, *, _tokens=None) -> Iterator[Command]:
    """Yield commands with arguments, including nested $(...) and backticks.

    Case patterns are skipped, while their bodies are retained. Shell function
    definitions are scanned statically; their names are filtered by S1.
    """
    if _depth > 32:
        raise ShellSyntaxError("Command substitution nesting exceeds 32", line_offset + 1)
    tokens = tokenize(text, line_offset) if _tokens is None else _tokens
    group, case_states = [], []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.kind in {"heredoc", "arithmetic"}:
            i += 1
            continue
        at_command_start = all(g.kind == "word" and not g.quoted and g.value in _LEADING for g in group)
        if at_command_start and t.kind == "word" and not t.quoted and t.value == "[[":
            # && and || inside [[ ... ]] belong to a test expression, not commands.
            end = next((j for j in range(i + 1, len(tokens)) if tokens[j].value == "]]" and not tokens[j].quoted), None)
            if end is None:
                raise ShellSyntaxError("Unclosed [[ test", t.line)
            i = end + 1
            continue
        if at_command_start and t.kind == "word" and not t.quoted:
            if t.value == "case":
                case_states.append("header")
                group = []
            elif t.value == "esac" and case_states:
                case_states.pop()
        if case_states and case_states[-1] in {"header", "pattern"}:
            if t.value == "in" and case_states[-1] == "header":
                case_states[-1] = "pattern"
            elif t.kind == "op" and t.value == ")":
                case_states[-1] = "body"
            i += 1
            continue
        if t.kind == "op" and t.value not in _REDIRECTS:
            # foo() { ... } declares a function; foo is not an invocation.
            definition = (t.value == "(" and len(group) == 1 and i + 1 < len(tokens)
                          and tokens[i + 1].value == ")")
            if not definition:
                command = _command(group, text)
                if command:
                    yield command
            group = []
            if definition:
                i += 1
            if t.value in {";;", ";&", ";;&"} and case_states:
                case_states[-1] = "pattern"
        else:
            group.append(t)
        i += 1
    command = _command(group, text)
    if command:
        yield command
    for token in tokens:
        for inner, inner_line in token.substitutions:
            yield from iter_commands(inner, inner_line - 1, _depth + 1)


def function_names(text: str, *, _tokens=None) -> set[str]:
    """Recognize declaration tokens without looking inside comments/strings."""
    tokens = tokenize(text) if _tokens is None else _tokens
    names, at_start = set(), True

    def compound_after(start):
        if (start + 1 < len(tokens) and all(t.kind == "op" for t in tokens[start:start + 2])
                and [t.value for t in tokens[start:start + 2]] == ["(", ")"]):
            start += 2
        while start < len(tokens) and tokens[start].kind == "op" and tokens[start].value == "\n":
            start += 1
        return (start < len(tokens) and tokens[start].kind == "op"
                and tokens[start].value in {"{", "("})

    for i, t in enumerate(tokens):
        if t.kind == "op" and t.value not in _REDIRECTS:
            at_start = True
            continue
        if t.kind != "word":
            continue
        if at_start and not t.quoted and not t.dynamic:
            if t.value == "function" and i + 2 < len(tokens):
                name = tokens[i + 1]
                if (name.kind == "word" and not name.quoted and not name.dynamic
                        and re.fullmatch(r"[A-Za-z_]\w*", name.value)
                        and compound_after(i + 2)):
                    names.add(name.value)
            tail = tokens[i + 1:i + 3]
            if (re.fullmatch(r"[A-Za-z_]\w*", t.value) and len(tail) == 2
                    and all(x.kind == "op" for x in tail)
                    and [x.value for x in tail] == ["(", ")"] and compound_after(i + 3)):
                names.add(t.value)
            if t.value in _LEADING:
                continue
        at_start = False
    return names


_WRAPPERS = {
    "sudo": ({"-u", "-g", "-h", "-p", "-C", "-T", "-R", "-D", "--user", "--group", "--host", "--prompt", "--chdir"},
             {"-n", "-E", "-H", "-S", "-k", "-b", "--non-interactive", "--preserve-env"}),
    "env": ({"-u", "-C", "--unset", "--chdir"}, {"-i", "--ignore-environment", "-0", "--null"}),
    "nohup": (set(), set()),
    "nice": ({"-n", "--adjustment"}, set()),
    "ionice": ({"-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid"}, {"-t", "--ignore"}),
    "setsid": (set(), {"-c", "-f", "-w", "--ctty", "--fork", "--wait"}),
    "stdbuf": ({"-i", "-o", "-e", "--input", "--output", "--error"}, set()),
    "timeout": ({"-k", "-s", "--kill-after", "--signal"}, {"--foreground", "--preserve-status", "-v", "--verbose"}),
    "exec": ({"-a"}, {"-c", "-l"}),
    "command": (set(), {"-p"}),
}


def unwrap(command: Command, *, external=False) -> list[Invocation]:
    """Return each wrapper AND its payload. Unknown wrapper flags stop analysis.

    Options with values, attached short options, --opt=value, env assignments and
    timeout units are handled without treating option values as executables.
    ``external`` distinguishes direct exec/argv calls from shell builtin lookup.
    """
    tokens, i, result = command.tokens, 0, []
    while i < len(tokens):
        head = tokens[i]
        result.append(Invocation(tokens[i:], external=external))
        wrapper = head.value.rsplit("/", 1)[-1]
        if (head.dynamic or wrapper not in _WRAPPERS
                or (wrapper in {"exec", "command"} and (external or head.value != wrapper))):
            break
        value_opts, flags = _WRAPPERS[wrapper]
        i += 1
        while i < len(tokens):
            word = tokens[i].value
            if tokens[i].dynamic:
                result[-1] = replace(result[-1], reason="Dynamic wrapper argument; payload boundary is unresolved")
                return result
            if word == "--":
                i += 1
                break
            # These options print/query information and do not execute operands.
            if word in {"--help", "--version"} or (wrapper == "command" and word in {"-v", "-V"}):
                return result
            if wrapper == "sudo" and word in {"-V", "-l", "--list", "-v", "--validate"}:
                return result
            if not word.startswith("-") or word == "-":
                break
            if word in value_opts:
                if i + 1 >= len(tokens):
                    result[-1] = replace(result[-1], reason="Missing wrapper option value")
                    return result
                if tokens[i + 1].dynamic:
                    result[-1] = replace(result[-1], reason="Dynamic wrapper option value; payload boundary is unresolved")
                    return result
                i += 2
            elif word in flags or (wrapper == "nice" and re.fullmatch(r"-\d+", word)):
                i += 1
            elif any(word.startswith(opt + "=") for opt in value_opts if opt.startswith("--")):
                i += 1
            elif wrapper == "sudo" and word.startswith("--preserve-env="):
                i += 1
            elif any(word.startswith(opt) and len(word) > 2 for opt in value_opts if len(opt) == 2):
                i += 1
            else:
                result[-1] = replace(result[-1], reason="Unsupported wrapper option: " + word)
                return result
        if wrapper == "timeout":
            if (i < len(tokens) and not tokens[i].dynamic
                    and re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)[smhd]?", tokens[i].value)):
                i += 1
            else:
                result[-1] = replace(result[-1], reason="Unresolved timeout duration")
                return result
        if wrapper in {"env", "sudo"}:
            while i < len(tokens) and _ASSIGNMENT.match(tokens[i].value):
                if tokens[i].dynamic:
                    result[-1] = replace(result[-1], reason="Dynamic wrapper assignment; payload boundary is unresolved")
                    return result
                i += 1
        # command uses shell command lookup, including builtins. All other
        # wrappers, and the shell's exec builtin, launch an external executable.
        if wrapper != "command":
            external = True
    return result
