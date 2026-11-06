"""S3-only adapter for the 2026-10-26 path/classes routing contract."""
import re
from pathlib import PurePosixPath

from .component_reader import read_components
from .core import InputError
from .source_model import SourceProvider


CLASS_ORDER = ('S1', 'S3', 'S4', 'S5', 'S6', 'S7')


def s3_files(filelists):
    if not isinstance(filelists, list):
        raise InputError('filelists.json must be an array of {path, classes}; old kind/meta input is not supported')
    paths, selected = set(), []
    for index, row in enumerate(filelists):
        if not isinstance(row, dict) or set(row) != {'path', 'classes'}:
            raise InputError('filelists[{}] must contain only path and classes'.format(index))
        path, classes = row['path'], row['classes']
        if (not isinstance(path, str) or not path or path.startswith(('/', '~')) or
                re.match(r'^[A-Za-z]:', path) or any(c in path for c in ('\\', '\n', '\r', '\x00')) or
                any(p in ('', '.', '..') for p in path.split('/'))):
            raise InputError('filelists[{}]: invalid relative POSIX path'.format(index))
        if path in paths:
            raise InputError('duplicate filelists path: ' + path)
        paths.add(path)
        if (not isinstance(classes, list) or not classes or
                any(not isinstance(c, str) or c not in CLASS_ORDER for c in classes) or
                len(set(classes)) != len(classes) or
                classes != sorted(classes, key=CLASS_ORDER.index)):
            raise InputError('filelists[{}]: classes must be unique and ordered S1,S3,S4,S5,S6,S7'.format(index))
        if 'S3' in classes:
            selected.append(path)
    return sorted(selected)


def identity(declaration):
    return declaration.get('key') or declaration.get('name')


def location(evidence):
    return '{}:{}'.format(evidence['path'], evidence['line'])


def compact(result):
    """Keep relationships and locations, omit raw source and internal IDs."""
    records = []
    declarations = {d['id']: d for d in result['declarations']}

    def add(kind, name, evidence, **fields):
        records.append(dict(kind=kind, name=name, at=location(evidence), **fields))

    for declaration in result['declarations']:
        kind, raw, ev = declaration['kind'], declaration['raw'], declaration['evidence']
        name = identity(declaration)
        if kind == 'definition':
            fields = {k: raw[k] for k in ('features', 'adapted_system_type', 'adapted_kernel')
                      if isinstance(raw.get(k), list) and raw[k]}
            add('component', name, ev, **fields)
            for key in ('module_list', 'targets'):
                if key in raw:
                    add('build_targets', name, ev, field=key, targets=raw[key])
        elif kind == 'selection':
            options = {k: v for k, v in raw.items() if k != 'component'}
            add('selects', name, ev, **{'from': ev['path']}, **({'options': options} if options else {}))
        elif kind == 'product_reference':
            pass  # Emitted from references below, with its actual relation.
        elif kind in ('subsystem', 'subsystem_components'):
            fields = {}
            if kind == 'subsystem_components':
                fields['op'] = declaration['operator']
                for key in ('targets', 'expression'):
                    if key in declaration:
                        fields[key] = declaration[key]
            else:
                fields['template'] = declaration['template']
            if declaration.get('conditions'):
                fields['when'] = declaration['conditions']
            if declaration.get('symbolic'):
                fields['symbolic'] = True
            add(kind, name, ev, **fields)

    for reference in result['references']:
        declaration = declarations[reference['from_declaration_id']]
        category, target, ev = reference['category'], reference['target'], reference['evidence']
        if category in ('deps.components', 'deps.third_party'):
            add('requires', target, ev, **{'from': identity(declaration)},
                **({'category': 'third_party'} if category.endswith('third_party') else {}))
        elif category in ('inherit', 'system_component'):
            add('inherits' if category == 'inherit' else 'system_component', target, ev, **{'from': ev['path']})
        elif category.startswith('build.'):
            add('build_targets', identity(declaration), ev, field=category[6:], targets=target)
        elif category in ('deps.kernel_special', 'deps.board_special'):
            add('conditional_requires', identity(declaration), ev, category=category[5:], value=target, symbolic=True)
    # Keep source order, especially = / += / -= and same-line assignments.
    # Python's stable sort preserves declaration order at identical locations.
    return sorted(records, key=lambda r: (r['at'].rsplit(':', 1)[0], int(r['at'].rsplit(':', 1)[1])))


def extract_s3(root, filelists):
    """Return (compact requirements array, concise diagnostics, full evidence).

    Reads only S3-listed files. No traversal, product activation, dependency
    closure, imports, source execution, repository or environment checks.
    """
    files = s3_files(filelists)
    context = dict(selection_files=[], base_files=[])
    for path in files:
        parts = PurePosixPath(path).parts
        if path.endswith('.json') and ('productdefine' in parts or 'productdefine_common' in parts):
            if 'base' in parts:
                context['base_files'].append(path)
            else:
                context['selection_files'].append(path)
        elif path.startswith('vendor/') and path.endswith('/config.json'):
            context['selection_files'].append(path)
    provider = SourceProvider(root, 's3-filelists-v1', files=files)
    result = read_components(provider, files, context)
    messages = []
    for d in result['diagnostics']:
        item = dict(code=d['code'], message=d['message'])
        if d.get('evidence'):
            item['at'] = location(d['evidence'])
        elif d.get('path'):
            item['at'] = '{}:{}'.format(d['path'], d.get('line', 1))
        messages.append(item)
    # Unknown carriers must not silently look like successful empty extraction.
    for d in result['declarations']:
        if d['kind'] == 'metadata':
            path = d['evidence']['path']
            if path not in context['selection_files']:
                messages.append(dict(code='S3_UNRECOGNIZED', message='no supported S3 structure', at=location(d['evidence'])))
    for d in result['declarations']:
        deps = d['raw'].get('deps', {}) if isinstance(d['raw'], dict) else {}
        if d['kind'] == 'definition' and isinstance(deps, dict) and any(deps.get(k) for k in ('kernel_special', 'board_special')):
            messages.append(dict(code='CONDITIONAL_DEPENDENCY', message='conditional dependencies retained without evaluation', at=location(d['evidence'])))
    requirements = compact(result)
    diagnostics = dict(complete=result['run_context']['status'] == 'complete' and not messages,
                       files=len(files), records=len(requirements), messages=messages)
    return requirements, diagnostics, result
