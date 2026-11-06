"""Independent component declarations; definitions and selections stay distinct."""
import posixpath

from .source_model import Run


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


class ComponentReader:
    def __init__(self, run, context=None):
        self.run, self.context, self.seen = run, context or {}, {}

    def invalid(self, message, document, pointer=(), code='INVALID_STRUCTURE'):
        self.run.diagnostic(code, message, ('input', 'resolution'), self.run.evidence(document, pointer))

    def selections(self, document, classification, parts=False, base=False):
        data, result = document['json'].data, []
        if base or parts:
            mapping = data if base else data.get('parts', {})
            prefix = () if base else ('parts',)
            if not isinstance(mapping, dict):
                self.invalid('parts must be an object', document, prefix)
                return result
            for key, raw in mapping.items():
                pointer = prefix + (key,)
                if not isinstance(key, str) or key.count(':') != 1 or not all(key.split(':')) or not isinstance(raw, dict):
                    self.invalid('parts key must be subsystem:component and value an object', document, pointer)
                    continue
                subsystem, name = key.split(':')
                declaration = self.run.declare('selection', raw, self.run.evidence(document, pointer),
                    classification=classification, subsystem=subsystem, name=name, key=key, selection_format='parts')
                if declaration:
                    result.append(declaration)
            return result
        subsystems = data.get('subsystems', [])
        if not isinstance(subsystems, list):
            self.invalid('subsystems must be an array', document, ('subsystems',))
            return result
        for i, subsystem in enumerate(subsystems):
            pointer = ('subsystems', i)
            if not isinstance(subsystem, dict) or not nonempty(subsystem.get('subsystem')):
                self.invalid('irregular subsystem entry ignored by upstream conversion', document, pointer,
                             'IGNORED_BY_UPSTREAM_PROFILE')
                continue
            components = subsystem.get('components', [])
            if not isinstance(components, list):
                self.invalid('components must be an array', document, pointer + ('components',))
                continue
            for j, raw in enumerate(components):
                at = pointer + ('components', j)
                if not isinstance(raw, dict) or not nonempty(raw.get('component')):
                    self.invalid('selection.component must be a nonempty string', document, at, 'MISSING_IDENTITY')
                    continue
                declaration = self.run.declare('selection', raw, self.run.evidence(document, at),
                    classification=classification, subsystem=subsystem['subsystem'], name=raw['component'],
                    key=subsystem['subsystem'] + ':' + raw['component'], selection_format='subsystems')
                if declaration:
                    result.append(declaration)
        return result

    def definition(self, document, pointer, raw, name, subsystem, classification):
        declaration = self.run.declare('definition', raw, self.run.evidence(document, pointer),
            classification=classification, name=name, subsystem=subsystem,
            key=(subsystem + ':' + name) if nonempty(subsystem) else None)
        if declaration is None:
            return
        if not nonempty(subsystem):
            self.invalid('subsystem is absent or empty; identity is unqualified', document, pointer, 'MISSING_SUBSYSTEM')
        for field in ('features', 'adapted_system_type', 'adapted_kernel'):
            if field in raw and not isinstance(raw[field], list):
                self.invalid(field + ' must be an array', document, pointer + (field,))
        deps = raw.get('deps', {})
        if not isinstance(deps, dict):
            self.invalid('deps must be an object', document, pointer + ('deps',))
            return
        for category in ('components', 'third_party'):
            values = deps.get(category, [])
            if not isinstance(values, list):
                self.invalid('dependency category must be an array', document, pointer + ('deps', category))
                continue
            for index, target in enumerate(values):
                at = pointer + ('deps', category, index)
                if not nonempty(target):
                    self.invalid('dependency must be a nonempty name', document, at)
                    continue
                self.run.reference(declaration, 'deps.' + category, target, occurrence=index,
                    evidence=self.run.evidence(document, at))
        for category in ('kernel_special', 'board_special'):
            if deps.get(category):
                self.run.reference(declaration, 'deps.' + category, deps[category], status='condition_unknown',
                    evidence=self.run.evidence(document, pointer + ('deps', category)))
        build = raw.get('build')
        if build is not None:
            if not isinstance(build, dict):
                self.invalid('build must be an object', document, pointer + ('build',))
            else:
                for key in ('sub_component', 'modules', 'group_type'):
                    if key in build:
                        self.run.reference(declaration, 'build.' + key, build[key],
                            evidence=self.run.evidence(document, pointer + ('build', key)))
                winner = next((key for key in ('sub_component', 'modules', 'group_type') if key in build), None)
                if winner:
                    declaration['derived_module_source'] = dict(field=winner, value=build[winner],
                        rule_id='C1-build-priority', input_declaration_ids=[declaration['id']])

    def read(self, path, selection_context=False, base=False):
        cache_key = (path, selection_context, base)
        if cache_key in self.seen:
            return self.seen[cache_key]
        if posixpath.basename(path) == 'BUILD.gn':
            from .gn_components import read_gn_components
            read_gn_components(self.run, path)
            self.seen[cache_key] = (None, [])
            return None, []
        if not (path.endswith('.json') or posixpath.basename(path) == 'ohos.build'):
            self.run.diagnostic('UNSUPPORTED_SEMANTICS', 'only JSON component inputs are interpreted', ('input',), path=path)
            self.seen[cache_key] = (None, [])
            return None, []
        document = self.run.json(path, 'resolution')
        if document is None:
            return None, []
        data = document['json'].data
        if not isinstance(data, dict):
            self.invalid('expected a JSON object', document)
            return document, []
        selected = []
        if base:
            selected = self.selections(document, 'C5', parts=True, base=True)
        elif posixpath.basename(path) == 'ohos.build' and isinstance(data.get('parts'), dict):
            for name, raw in data['parts'].items():
                if not nonempty(name) or not isinstance(raw, dict):
                    self.invalid('ohos.build part must be a named object', document, ('parts', name))
                    continue
                self.definition(document, ('parts', name), raw, name, data.get('subsystem'), 'C6')
        elif 'component' in data:
            raw = data['component']
            if not isinstance(raw, dict) or not nonempty(raw.get('name')):
                self.invalid('component.name required; top-level name is not a fallback', document, ('component',), 'MISSING_IDENTITY')
            else:
                self.definition(document, ('component',), raw, raw['name'], raw.get('subsystem'), 'C1')
                self.run.declare('package_metadata', {k: v for k, v in data.items() if k != 'component'},
                                 self.run.evidence(document), classification='N1')
        elif isinstance(data.get('components'), list):
            known_dirs = self.context.get('legacy_component_roots', ['build/lite/components', 'build_lite/components'])
            subsystem = posixpath.splitext(posixpath.basename(path))[0] if posixpath.dirname(path) in known_dirs else None
            for index, raw in enumerate(data['components']):
                if not isinstance(raw, dict) or not nonempty(raw.get('component')) or not any(
                        key in raw for key in ('deps', 'targets', 'dirs', 'adapted_kernel')):
                    self.invalid('legacy component requires identity and definition fields', document, ('components', index))
                    continue
                self.definition(document, ('components', index), raw, raw['component'], subsystem, 'C4')
        elif selection_context or 'subsystems' in data or 'product_name' in data:
            self.run.declare('product_config', data, self.run.evidence(document),
                             classification='C2' if 'product_name' in data else 'C3')
            if data.get('parts') and (selection_context or 'product_name' in data):
                selected = self.selections(document, 'C5', parts=True)
            else:
                selected = self.selections(document, 'C2' if 'product_name' in data else 'C3')
        else:
            self.run.declare('metadata', data, self.run.evidence(document), classification='N1')
        if selection_context or 'subsystems' in data or 'product_name' in data:
            for category in ('inherit', 'system_component'):
                values = data.get(category, []) if category == 'inherit' else ([data[category]] if data.get(category) else [])
                if not isinstance(values, list):
                    self.invalid(category + ' must be an array', document, (category,))
                    continue
                for index, target in enumerate(values):
                    pointer = (category, index) if category == 'inherit' else (category,)
                    if not nonempty(target):
                        self.invalid('reference must be a nonempty path', document, pointer)
                        continue
                    declaration = self.run.declare('product_reference', target, self.run.evidence(document, pointer),
                                                   classification='C3')
                    if declaration:
                        self.run.reference(declaration, category, target, resolved_path=target, path_base='checkout_root')
        self.seen[cache_key] = (document, selected)
        return document, selected


def read_components(provider, files, context=None):
    run = Run(provider, 'read_components', ('input', 'references'), files=sorted(set(files)), context=context or {})
    reader = ComponentReader(run, context)
    for path in sorted(set(files)):
        reader.read(path, selection_context=path in (context or {}).get('selection_files', []),
                    base=path in (context or {}).get('base_files', []))
    return run.finish()
