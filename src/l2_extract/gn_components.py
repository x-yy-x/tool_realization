"""Read lite_subsystem declarations, without executing GN or evaluating imports.

This is a bounded structural reader, not a GN interpreter. Assignment events
retain order, operators and guards; symbolic expressions never become names.
"""
import re
from dataclasses import dataclass


@dataclass
class Token:
    kind: str
    text: str
    start: int
    end: int


class SyntaxFailure(ValueError):
    def __init__(self, message, offset):
        super().__init__(message)
        self.offset = offset


def tokenize(text):
    tokens, i = [], 0
    while i < len(text):
        start, ch = i, text[i]
        if ch == '#':
            i = text.find('\n', i)
            if i < 0:
                break
            continue
        if ch in ' \t\r':
            i += 1
            continue
        if ch == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                if text[i] == '\n':
                    raise SyntaxFailure('newline in GN string', i)
                i += 2 if text[i] == '\\' else 1
            if i >= len(text):
                raise SyntaxFailure('unterminated GN string', start)
            i += 1
            kind = 'string'
        elif ch.isalpha() or ch == '_':
            i += 1
            while i < len(text) and (text[i].isalnum() or text[i] == '_'):
                i += 1
            kind = 'id'
        else:
            i += 2 if text[i:i + 2] in ('+=', '-=', '==', '!=', '<=', '>=', '&&', '||') else 1
            kind = 'newline' if ch == '\n' else 'punct'
        tokens.append(Token(kind, text[start:i], start, i))
    tokens.append(Token('eof', '', len(text), len(text)))
    return tokens


def literal(token):
    """GN only escapes quote, dollar and backslash; other escapes stay raw."""
    return re.sub(r'\\(["$\\])', r'\1', token.text[1:-1])


def symbolic(token):
    return bool(re.search(r'(?<!\\)(?:\\\\)*\$', token.text[1:-1]))


class GNReader:
    def __init__(self, run, document):
        self.run, self.document = run, document
        self.text = document['text']
        self.tokens, self.i, self.found = tokenize(self.text), 0, 0

    def current(self):
        return self.tokens[self.i]

    def skip_lines(self):
        while self.current().kind == 'newline':
            self.i += 1

    def evidence(self, start, end):
        return self.run.evidence(self.document, span=(
            len(self.text[:start].encode('utf-8')), len(self.text[:end].encode('utf-8'))))

    def diagnostic(self, code, message, start, end):
        self.run.diagnostic(code, message, ('input', 'references'), self.evidence(start, end))

    def group(self, opening):
        closing = {'(': ')', '[': ']', '{': '}'}[opening]
        start = self.current()
        if start.text != opening:
            raise SyntaxFailure('expected ' + opening, start.start)
        self.i += 1
        items, stack = [], [closing]
        while stack:
            token = self.current()
            if token.kind == 'eof':
                raise SyntaxFailure('unclosed ' + opening, start.start)
            self.i += 1
            if token.kind != 'string':
                if token.text in ('(', '[', '{'):
                    stack.append({'(': ')', '[': ']', '{': '}'}[token.text])
                    if len(stack) > 64:
                        raise SyntaxFailure('GN nesting exceeds 64', token.start)
                elif token.text in (')', ']', '}'):
                    if token.text != stack.pop():
                        raise SyntaxFailure('mismatched GN delimiter', token.start)
                    if not stack:
                        return items, token
            items.append(token)

    def expression(self):
        self.skip_lines()
        items = []
        while self.current().kind not in ('eof', 'newline') and self.current().text != '}':
            token = self.current()
            # GN permits adjacent statements without semicolons.
            if items and token.kind == 'id' and self.tokens[self.i + 1].text in ('=', '+=', '-='):
                break
            if token.text in ('(', '[', '{'):
                inner, end = self.group(token.text)
                items.extend([token, *inner, end])
            elif token.text in (')', ']'):
                raise SyntaxFailure('unexpected GN delimiter', token.start)
            else:
                self.i += 1
                items.append(token)
            if items[-1].text in ('+', '-', '&&', '||'):
                self.skip_lines()
        if not items:
            raise SyntaxFailure('missing assignment expression', self.current().start)
        return items

    def assignment(self, target, token, operator, items, guards):
        clean = [t for t in items if t.kind != 'newline']
        fields = dict(name=target['name'], operator=operator, conditions=guards,
                      subsystem_declaration_id=target['id'])
        # Only a complete comma-separated string list is a literal list.
        values, is_symbolic, valid = [], False, bool(clean and clean[0].text == '[' and clean[-1].text == ']')
        expect_value = True
        if valid:
            for item in clean[1:-1]:
                if expect_value and item.kind == 'string':
                    values.append(literal(item))
                    is_symbolic |= symbolic(item)
                    expect_value = False
                elif not expect_value and item.text == ',':
                    expect_value = True
                else:
                    valid = False
                    break
        if valid:
            fields['targets'] = values
        else:
            fields['expression'] = self.text[items[0].start:items[-1].end]
            is_symbolic = True
        fields['symbolic'] = is_symbolic or target.get('symbolic', False)
        ev = self.evidence(token.start, items[-1].end)
        self.run.declare('subsystem_components', ev['raw'], ev, **fields)
        if fields['symbolic']:
            self.diagnostic('GN_SYMBOLIC', 'GN variables/expressions retained without evaluation', token.start, items[-1].end)
        if guards:
            self.diagnostic('GN_CONDITIONAL', 'GN guards retained; active branch is not selected', token.start, items[-1].end)

    def block(self, target=None, guards=(), closing=False, depth=0, allow_targets=True):
        if depth > 64:
            raise SyntaxFailure('GN block nesting exceeds 64', self.current().start)
        while True:
            self.skip_lines()
            token = self.current()
            if token.kind == 'eof':
                if closing:
                    raise SyntaxFailure('unclosed GN block', token.start)
                return
            if token.text == '}':
                if not closing:
                    raise SyntaxFailure('unexpected closing brace', token.start)
                self.i += 1
                return
            if token.kind != 'id':
                raise SyntaxFailure('expected GN statement', token.start)
            self.i += 1
            next_token = self.current()
            if next_token.text in ('=', '+=', '-='):
                self.i += 1
                items = self.expression()
                if target and token.text == 'subsystem_components':
                    self.assignment(target, token, next_token.text, items, list(guards))
                continue
            if next_token.text != '(':
                # Dotted/indexed assignments or unsupported syntax are never
                # silently accepted as a fully understood subsystem.
                raise SyntaxFailure('unsupported GN statement: ' + token.text, token.start)
            args, end = self.group('(')
            self.skip_lines()
            if token.text == 'if':
                condition = self.text[next_token.end:end.start].strip()
                if not condition:
                    raise SyntaxFailure('empty GN condition', token.start)
                self.body(target, (*guards, condition), depth, allow_targets)
                self.skip_lines()
                if self.current().text == 'else':
                    self.i += 1
                    self.skip_lines()
                    if self.current().text == 'if':
                        # Parse exactly one else-if chain, not following peers.
                        self.conditional(target, (*guards, '!(' + condition + ')'), depth, allow_targets)
                    else:
                        self.body(target, (*guards, '!(' + condition + ')'), depth, allow_targets)
                continue
            if self.current().text != '{':
                if target:
                    self.diagnostic('GN_DYNAMIC_CALL', 'call inside subsystem may affect its declarations', token.start, end.end)
                continue
            if token.text in ('lite_subsystem', 'lite_subsystem_test') and allow_targets:
                clean = [t for t in args if t.kind != 'newline']
                known = len(clean) == 1 and clean[0].kind == 'string'
                name = literal(clean[0]) if known else self.text[next_token.end:end.start].strip()
                sym = not known or symbolic(clean[0])
                if not name:
                    raise SyntaxFailure('empty subsystem identity', token.start)
                self.found += 1
                declaration = self.run.declare('subsystem', name, self.evidence(token.start, end.end),
                    name=name, symbolic=sym, conditions=list(guards), template=token.text)
                if sym:
                    self.diagnostic('GN_SYMBOLIC', 'subsystem name is symbolic', token.start, end.end)
                if guards:
                    self.diagnostic('GN_CONDITIONAL', 'subsystem declaration is conditional', token.start, end.end)
                before = len(self.run.result['declarations'])
                self.body(declaration, guards, depth, False)
                if not any(d['kind'] == 'subsystem_components' for d in self.run.result['declarations'][before:]):
                    self.diagnostic('GN_MISSING_COMPONENTS', 'subsystem_components assignment not found', token.start, end.end)
            elif token.text == 'foreach':
                guard = 'foreach (' + self.text[next_token.end:end.start].strip() + ')'
                self.diagnostic('GN_LOOP', 'loop body is retained once, without expansion', token.start, end.end)
                self.body(target, (*guards, guard), depth, allow_targets)
            else:
                # A nested target/template has its own scope. Do not mistake
                # its subsystem_components or deps for the enclosing target.
                if target:
                    self.diagnostic('GN_NESTED_SCOPE', 'nested scope is not interpreted', token.start, end.end)
                self.group('{')

    def body(self, target, guards, depth, allow_targets):
        self.skip_lines()
        if self.current().text != '{':
            raise SyntaxFailure('expected GN block', self.current().start)
        self.i += 1
        self.block(target, guards, True, depth + 1, allow_targets)

    def conditional(self, target, guards, depth, allow_targets):
        """Consume one if/else chain without swallowing subsequent statements."""
        if depth > 64:
            raise SyntaxFailure('GN conditional nesting exceeds 64', self.current().start)
        self.i += 1  # if
        self.skip_lines()
        opening = self.current()
        _, end = self.group('(')
        condition = self.text[opening.end:end.start].strip()
        if not condition:
            raise SyntaxFailure('empty GN condition', opening.start)
        self.body(target, (*guards, condition), depth, allow_targets)
        self.skip_lines()
        if self.current().text == 'else':
            self.i += 1
            self.skip_lines()
            guards = (*guards, '!(' + condition + ')')
            if self.current().text == 'if':
                self.conditional(target, guards, depth + 1, allow_targets)
            else:
                self.body(target, guards, depth, allow_targets)


def read_gn_components(run, path):
    document = run.read(path)
    if document is None:
        return
    try:
        reader = GNReader(run, document)
        reader.block()
        if not reader.found:
            run.diagnostic('GN_NO_SUBSYSTEM', 'S3 GN file has no supported lite_subsystem declaration', path=path)
    except (SyntaxFailure, RecursionError) as exc:
        offset = getattr(exc, 'offset', 0)
        start = len(document['text'][:offset].encode('utf-8'))
        run.diagnostic('GN_UNSUPPORTED_SYNTAX', str(exc), ('input', 'references'),
                       run.evidence(document, span=(start, start)), path=path)
