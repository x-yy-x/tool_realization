"""Offline sources and evidence for independent S2/S3 extraction.

No source is executed. Logical paths are checkout paths; an explicit mapping
can bind them to archives. Each invocation owns its cache and resource budget.
"""
import copy
import hashlib
import json
import math
import posixpath
from pathlib import Path

from .core import InputError, JsonSource, Source


def stable_id(*values):
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]


def logical_path(path):
    if not isinstance(path, str) or not path or '\\' in path or '\x00' in path:
        raise InputError('logical path must be a nonempty POSIX path')
    normalized = posixpath.normpath(path)
    if normalized.startswith('/') or normalized == '..' or normalized.startswith('../'):
        raise InputError('path leaves logical source root: ' + path)
    return normalized


class SourceProvider:
    def __init__(self, root, source_set_id, files=None, mapping=None,
                 allowed_roots=None, metadata=None, limits=None):
        self.root = Path(root).resolve()
        if not self.root.is_dir() or not isinstance(source_set_id, str) or not source_set_id:
            raise InputError('existing root and explicit source_set_id required')
        self.source_set_id = source_set_id
        self.files = None if files is None else {logical_path(p) for p in files}
        self.mapping = {logical_path(k): Path(v) if Path(v).is_absolute() else self.root / v
                        for k, v in (mapping or {}).items()}
        self.roots = [self.root] + [Path(p).resolve() for p in (allowed_roots or [])]
        self.metadata = metadata or {}
        self.limits = dict(max_depth=64, max_bytes=64 * 1024 * 1024,
                           max_files=10000, max_declarations=200000)
        self.limits.update(limits or {})
        if any(type(v) is not int or v < 1 for v in self.limits.values()):
            raise InputError('limits must be positive integers')

    def locate(self, path):
        try:
            path = logical_path(path)
        except InputError as exc:
            return None, 'REFERENCE_OUT_OF_SCOPE', str(exc)
        if self.files is not None and path not in self.files:
            return None, 'REFERENCE_NOT_PROVIDED', 'not present in the supplied file list'
        actual = self.mapping.get(path, self.root / path).resolve()
        if not any(actual == root or root in actual.parents for root in self.roots):
            return None, 'REFERENCE_OUT_OF_SCOPE', str(actual)
        if not actual.is_file():
            return None, 'REFERENCE_MISSING', str(actual)
        return actual, None, None


LAYERS = ('input', 'references', 'resolution', 'dependencies')


def json_error_span(text):
    """Locate strict-decoding failures structurally, never by string search.

    Invoked only on invalid input. The regular decoder handles grammar; this
    walk adds key occurrence and scalar validity positions it does not expose.
    """
    decoder = json.JSONDecoder()
    class Located(ValueError):
        def __init__(self, start, end):
            self.span = (start, end)
    def ws(index):
        while index < len(text) and text[index].isspace():
            index += 1
        return index
    def scalar(index):
        value, end = decoder.raw_decode(text, index)
        try:
            if isinstance(value, str):
                value.encode('utf-8')
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError('non-finite')
        except (UnicodeError, ValueError):
            raise Located(index, end)
        return value, end
    def walk(index):
        index = ws(index)
        if index >= len(text):
            raise Located(index, index)
        if text[index] == '{':
            index, keys = ws(index + 1), set()
            while index < len(text) and text[index] != '}':
                start = index
                key, index = scalar(index)
                if not isinstance(key, str) or key in keys:
                    raise Located(start, index)
                keys.add(key)
                index = ws(index)
                if index >= len(text) or text[index] != ':':
                    raise Located(index, index + 1)
                index = ws(walk(index + 1))
                if index < len(text) and text[index] == ',':
                    index = ws(index + 1)
                elif index >= len(text) or text[index] != '}':
                    raise Located(index, index + 1)
            return index + 1
        if text[index] == '[':
            index = ws(index + 1)
            while index < len(text) and text[index] != ']':
                index = ws(walk(index))
                if index < len(text) and text[index] == ',':
                    index = ws(index + 1)
                elif index >= len(text) or text[index] != ']':
                    raise Located(index, index + 1)
            return index + 1
        return scalar(index)[1]
    try:
        # Grammar failures have an exact position without a second walk.
        json.loads(text)
        walk(0)
    except Located as exc:
        return exc.span
    except json.JSONDecodeError as exc:
        return exc.pos, min(exc.pos + 1, len(text))
    except RecursionError:
        return None
    return None


class Run:
    def __init__(self, provider, mode, requested, **context):
        self.provider = provider
        self.cache, self.json_cache, self.byte_count = {}, {}, 0
        self.declaration_cache = {}
        self.result = dict(schema_version='1.0', run_context=dict(
            mode=mode, source_set_id=provider.source_set_id, root=str(provider.root),
            input_enumeration='explicit_files' if provider.files is not None else 'root',
            limits=provider.limits.copy(), **context), sources=[], declarations=[],
            references=[], resolved=[], diagnostics=[], coverage={
                key: dict(status='complete' if key in requested else 'not_requested',
                          scope=mode, count=0, reasons=[]) for key in LAYERS})

    def diagnostic(self, code, message, layers=('input',), evidence=None,
                   level='warning', status='partial', **details):
        item = dict(code=code, message=message, layers=list(layers), level=level, **details)
        if evidence is not None:
            item['evidence'] = evidence
        self.result['diagnostics'].append(item)
        for layer in layers:
            target = self.result['coverage'][layer]
            if target['status'] != 'not_requested':
                if target['status'] != 'unsupported':
                    target['status'] = status
                if code not in target['reasons']:
                    target['reasons'].append(code)

    def read(self, path, layer='input'):
        if path in self.cache:
            return self.cache[path]
        actual, code, reason = self.provider.locate(path)
        if code:
            self.diagnostic(code, reason, ('input', layer), path=path)
            return None
        try:
            size = actual.stat().st_size
            limits = self.provider.limits
            if len(self.cache) >= limits['max_files'] or self.byte_count + size > limits['max_bytes']:
                self.diagnostic('LIMIT_REACHED', 'source file/byte budget exceeded', ('input', layer), path=path)
                return None
            raw = actual.read_bytes()
            self.byte_count += len(raw)
            source = dict(path=path, actual_path=str(actual), sha256=hashlib.sha256(raw).hexdigest(),
                          bytes=len(raw), source_set_id=self.provider.source_set_id,
                          metadata=copy.deepcopy(self.provider.metadata.get(path, {})))
            self.result['sources'].append(source)
            text = raw.decode('utf-8')
        except (OSError, UnicodeError) as exc:
            self.diagnostic('INVALID_SYNTAX' if isinstance(exc, UnicodeError) else 'READ_ERROR',
                            str(exc), ('input', layer), path=path, line=1)
            return None
        self.cache[path] = dict(raw=raw, text=text, source=source)
        return self.cache[path]

    def json(self, path, layer='input'):
        if path in self.json_cache:
            return self.json_cache[path]
        document = self.read(path, layer)
        if document is None:
            return None
        try:
            parsed = JsonSource(Source(path, document['text']))
        except (ValueError, RecursionError) as exc:
            code = 'DUPLICATE_JSON_KEY' if 'duplicate JSON key' in str(exc) else 'INVALID_SYNTAX'
            span = json_error_span(document['text'])
            evidence = None
            if span:
                byte_span = tuple(len(document['text'][:p].encode('utf-8')) for p in span)
                evidence = self.evidence(document, span=byte_span)
            self.diagnostic(code, str(exc), ('input', layer), evidence, path=path)
            self.json_cache[path] = None
            return None
        document['json'] = parsed
        self.json_cache[path] = document
        return document

    def evidence(self, document, pointer=(), span=None):
        text = document['text']
        if span is None:
            start, end = document['json'].spans[tuple(pointer)]
            byte_start, byte_end = len(text[:start].encode('utf-8')), len(text[:end].encode('utf-8'))
        else:
            byte_start, byte_end = span
        raw = document['raw'][byte_start:byte_end].decode('utf-8')
        return dict(path=document['source']['path'], sha256=document['source']['sha256'],
                    line=document['raw'][:byte_start].count(b'\n') + 1,
                    byte_start=byte_start, byte_end=byte_end, raw=raw,
                    **({'json_pointer': '/' + '/'.join(str(p).replace('~', '~0').replace('/', '~1') for p in pointer)
                         if pointer else ''} if span is None else {}))

    def declare(self, kind, raw, evidence, occurrence='', **fields):
        identifier = stable_id(self.provider.source_set_id, evidence['path'], evidence['sha256'],
                               evidence['byte_start'], kind, occurrence)
        if identifier in self.declaration_cache:
            return self.declaration_cache[identifier]
        if len(self.result['declarations']) >= self.provider.limits['max_declarations']:
            self.diagnostic('LIMIT_REACHED', 'declaration budget exceeded', ('input', 'resolution'), evidence)
            return None
        item = dict(id=identifier, kind=kind, raw=copy.deepcopy(raw), evidence=evidence,
                    source_set_id=self.provider.source_set_id, **fields)
        self.result['declarations'].append(item)
        self.declaration_cache[identifier] = item
        return item

    def reference(self, declaration, category, target, **fields):
        item = dict(id=stable_id(declaration['id'], category, target, fields.get('occurrence', '')),
                    from_declaration_id=declaration['id'], category=category, target=target,
                    evidence=declaration['evidence'], status='discovered')
        item.update(fields)
        self.result['references'].append(item)
        return item

    def finish(self):
        counts = dict(input=len(self.result['declarations']), references=len(self.result['references']),
                      resolution=len(self.result['resolved']),
                      dependencies=sum(r['category'].startswith('deps.') for r in self.result['references']))
        for key, value in counts.items():
            self.result['coverage'][key]['count'] = value
        states = [v['status'] for v in self.result['coverage'].values() if v['status'] != 'not_requested']
        self.result['run_context']['status'] = 'complete' if all(s == 'complete' for s in states) else 'partial'
        return self.result


def resolved_record(key, value, declaration_ids, rule, previous=None, status='known'):
    return dict(key=key, value=copy.deepcopy(value), status=status,
                applied_declaration_ids=list(declaration_ids),
                shadowed_declaration_ids=(previous['shadowed_declaration_ids'] +
                                          previous['applied_declaration_ids']) if previous else [],
                rule_ids=([*previous['rule_ids'], rule] if previous else [rule]))
