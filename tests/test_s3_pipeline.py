"""Current S3 contract, source semantics, GN edge cases and CLI integration."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from src.l2_extract.__main__ import main
from src.l2_extract.core import InputError
from src.l2_extract.s3_pipeline import extract_s3, s3_files

try:
    import jsonschema
except ImportError:
    jsonschema = None

REPO = Path(__file__).resolve().parents[1]
GN = 'build/lite/config/subsystem/demo/BUILD.gn'


class S3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'source'
        self.root.mkdir()
        self.rows = []

    def put(self, path, content, classes=None):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
        self.rows.append(dict(path=path, classes=classes or ['S3']))
        return file

    def extract(self):
        return extract_s3(self.root, self.rows)

    def gn(self, text):
        self.put(GN, text)
        return self.extract()

    def test_upstream_schema_example_routes_only_s3(self):
        schema = json.loads((REPO / 'schema/filelists.schema.json').read_text())
        self.assertEqual(s3_files(schema['examples'][0]), ['base/hiviewdfx/hilog/bundle.json'])
        self.assertEqual(s3_files([]), [])

    def test_reject_bad_contracts(self):
        invalid = [None, {}, {'files': []}, [{'path': 'x', 'kind': 'bundle'}],
                   [{'path': 'x', 'classes': ['S3'], 'size': 1}]]
        for classes in ([], ['S2'], ['S3', 'S1'], ['S3', 'S3'], 'S3', [3], [{}]):
            invalid.append([dict(path='x', classes=classes)])
        for path in ('', '/x', './x', 'x/../b', 'x//b', 'x/', 'C:/x', '~x', 'x\\b', 'x\nb', 'x\x00b'):
            invalid.append([dict(path=path, classes=['S3'])])
        invalid.append([dict(path='x', classes=['S3'])] * 2)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(InputError):
                s3_files(data)

    def test_bundle_definition_dependencies_and_targets(self):
        self.put('ipc/bundle.json', {'name': '@ohos/package', 'component': {
            'name': 'ipc', 'subsystem': 'communication', 'features': ['ipc_feature'],
            'deps': {'components': ['hilog'], 'third_party': ['bounds_checking_function']},
            'build': {'sub_component': ['//ipc:ipc']}}})
        rows, diag, _ = self.extract()
        self.assertTrue(diag['complete'])
        self.assertEqual(rows[0]['name'], 'communication:ipc')
        self.assertNotIn('@ohos/package', [r['name'] for r in rows])
        deps = [r for r in rows if r['kind'] == 'requires']
        self.assertEqual({r['name'] for r in deps}, {'hilog', 'bounds_checking_function'})
        self.assertEqual(deps[1]['category'], 'third_party')
        self.assertEqual([r for r in rows if r['kind'] == 'build_targets'][0]['targets'], ['//ipc:ipc'])
        self.assertTrue(all('raw' not in r and 'id' not in r for r in rows))

    def test_products_parts_base_inherit_and_exclusion_are_declarations(self):
        self.put('productdefine/common/products/old.json', {'parts': {'sys:a': {}}})
        self.put('productdefine/common/base/min.json', {'sys:b': {}})
        self.put('productdefine/common/inherit/base.json', {'subsystems': [{'subsystem': 'sys', 'components': [{'component': 'c'}]}]})
        self.put('vendor/demo/board/config.json', {'inherit': ['productdefine/common/inherit/base.json'],
            'subsystems': [{'subsystem': 'sys', 'components': [{'component': 'a', 'features': ['x = true'], 'is_exclude': 'true'}]}]})
        rows, diag, _ = self.extract()
        self.assertTrue(diag['complete'])
        selections = [r for r in rows if r['kind'] == 'selects']
        self.assertEqual([r['name'] for r in selections], ['sys:b', 'sys:c', 'sys:a', 'sys:a'])
        self.assertEqual(selections[-1]['options']['is_exclude'], 'true')
        self.assertEqual(len([r for r in rows if r['kind'] == 'inherits']), 1)
        self.assertFalse(any(r['kind'] == 'component' for r in rows))

    def test_ohos_build_multiple_parts_and_module_lists(self):
        self.put('device/demo/ohos.build', {'subsystem': 'chipset', 'parts': {
            'board': {'module_list': ['//device/demo:board']}, 'drivers': {'module_list': []}}})
        rows, diag, _ = self.extract()
        self.assertTrue(diag['complete'])
        self.assertEqual({r['name'] for r in rows if r['kind'] == 'component'}, {'chipset:board', 'chipset:drivers'})
        self.assertEqual(len([r for r in rows if r['kind'] == 'build_targets']), 2)

    def test_routing_no_traversal_no_execution_no_other_modules(self):
        self.put('not-read/BUILD.gn', 'not valid GN', ['S1', 'S7'])
        self.put('unlisted/bundle.json', 'bad json', ['S6'])
        self.put('good/bundle.json', {'component': {'name': 'a', 'subsystem': 's'}})
        with patch('os.scandir', side_effect=AssertionError('no traversal')), \
             patch('subprocess.run', side_effect=AssertionError('no execution')), \
             patch('os.system', side_effect=AssertionError('no execution')):
            rows, diag, evidence = self.extract()
        self.assertEqual(diag['files'], 1)
        self.assertTrue(diag['complete'])
        self.assertEqual([r['name'] for r in rows], ['s:a'])
        self.assertEqual(len(evidence['sources']), 1)

    def test_broken_sources_report_partial(self):
        for text in ('{bad}', '{"component":1}', '{"component":{"name":"x","subsystem":"s","deps":[]}}',
                     '{"component":{"name":"x","subsystem":"s"},"component":{}}', '{}'):
            with self.subTest(text=text):
                self.rows = []
                self.put('x/bundle.json', text)
                rows, diag, _ = self.extract()
                self.assertFalse(diag['complete'])
                self.assertTrue(diag['messages'])

    def test_missing_file_and_outside_symlink(self):
        outside = self.base / 'outside.json'
        outside.write_text('{}')
        (self.root / 'bundle.json').symlink_to(outside)
        self.rows = [dict(path='bundle.json', classes=['S3']), dict(path='missing/bundle.json', classes=['S3'])]
        _, diag, _ = self.extract()
        self.assertEqual({m['code'] for m in diag['messages']}, {'REFERENCE_MISSING', 'REFERENCE_OUT_OF_SCOPE'})
        self.assertFalse(diag['complete'])

    def test_gn_multiline_comments_strings_and_operators(self):
        rows, diag, _ = self.gn('''# lite_subsystem("fake") { subsystem_components = ["//fake"] }
lite_subsystem("demo") {
  subsystem_components = [
    "//a:one", # comment
    "//b:hash#literal",
  ]
  subsystem_components += [ ":local" ]
  subsystem_components -= [ "//a:one" ]
}
''')
        self.assertTrue(diag['complete'], diag)
        events = [r for r in rows if r['kind'] == 'subsystem_components']
        self.assertEqual([r['op'] for r in events], ['=', '+=', '-='])
        self.assertEqual(events[0]['targets'], ['//a:one', '//b:hash#literal'])
        self.assertEqual(events[0]['at'], GN + ':3')
        self.assertEqual(events[1]['targets'], [':local'])
        self.assertFalse(any(r['kind'] == 'component' for r in rows))

    def test_gn_conditions_else_if_and_unconditional_tail(self):
        rows, diag, _ = self.gn('''lite_subsystem("demo") {
subsystem_components = []
if (board == "a") { subsystem_components += ["//a"] }
else if (board == "b") { subsystem_components += ["//b"] }
else { subsystem_components -= ["//c"] }
subsystem_components += ["//tail"]
}''')
        events = [r for r in rows if r['kind'] == 'subsystem_components']
        self.assertEqual(events[1]['when'], ['board == "a"'])
        self.assertEqual(events[2]['when'], ['!(board == "a")', 'board == "b"'])
        self.assertEqual(events[3]['when'], ['!(board == "a")', '!(board == "b")'])
        self.assertNotIn('when', events[4])
        self.assertFalse(diag['complete'])

    def test_gn_symbolic_preserves_entire_expression(self):
        rows, diag, _ = self.gn('''lite_subsystem("demo") {
subsystem_components = common_components + ["//extra"]
subsystem_components += ["${root}/x:target", "$root/y:target"]
}''')
        events = [r for r in rows if r['kind'] == 'subsystem_components']
        self.assertEqual(events[0]['expression'], 'common_components + ["//extra"]')
        self.assertNotIn('targets', events[0])
        self.assertTrue(all(r['symbolic'] for r in events))
        self.assertFalse(diag['complete'])

    def test_gn_scope_and_multiple_subsystems(self):
        rows, diag, _ = self.gn('''group("not_subsystem") { deps = ["//wrong"] }
lite_subsystem("a") {
subsystem_components = ["//a"]
group("inner") { subsystem_components = ["//wrong"] }
}
lite_subsystem_test("b") { subsystem_components = ["//b"] }
''')
        events = [r for r in rows if r['kind'] == 'subsystem_components']
        self.assertEqual([r['name'] for r in events], ['a', 'b'])
        self.assertEqual([r['targets'] for r in events], [['//a'], ['//b']])
        self.assertFalse(diag['complete'])  # Nested scope explicitly unresolved.

    def test_gn_malformed_or_missing_components_never_complete(self):
        for text in ('lite_subsystem("x") {', 'lite_subsystem("x") { subsystem_components = ["a" }',
                     'lite_subsystem("x") { subsystem_components = ["unterminated] }',
                     'group("x") { deps = [] }', 'lite_subsystem("x") {}'):
            with self.subTest(text=text):
                self.rows = []
                _, diag, _ = self.gn(text)
                self.assertFalse(diag['complete'])

    def test_gn_escaped_strings_and_unicode_evidence(self):
        rows, diag, evidence = self.gn('# 中文\nlite_subsystem("测试") { subsystem_components = ["//a:hash#x", "//a:escaped\\$x"] }')
        self.assertTrue(diag['complete'], diag)
        self.assertEqual(rows[-1]['targets'][-1], '//a:escaped$x')
        raw = (self.root / GN).read_bytes()
        for d in evidence['declarations']:
            ev = d['evidence']
            self.assertEqual(raw[ev['byte_start']:ev['byte_end']].decode(), ev['raw'])

    def test_gn_loop_and_same_line_events(self):
        rows, diag, _ = self.gn('''lite_subsystem("x") { subsystem_components = [] subsystem_components += ["//x"]
foreach (x, things) { subsystem_components += [x] }
}''')
        events = [r for r in rows if r['kind'] == 'subsystem_components']
        self.assertEqual([r['op'] for r in events], ['=', '+=', '+='])
        self.assertEqual(events[-1]['when'], ['foreach (x, things)'])
        self.assertFalse(diag['complete'])

    def test_real_upstream_aafwk_labels_are_not_component_names(self):
        root = REPO / 'tests/fixture_s3_upstream'
        path = 'build/lite/config/subsystem/aafwk/BUILD.gn'
        rows, diag, _ = extract_s3(root, [dict(path=path, classes=['S3'])])
        self.assertFalse(diag['complete'])  # Variables come from external GN context.
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['name'], 'aafwk')
        self.assertEqual(rows[1]['targets'], [
            '${aafwk_lite_path}/frameworks/ability_lite:aafwk_abilitykit_lite',
            '${aafwk_lite_path}/frameworks/abilitymgr_lite:aafwk_abilityManager_lite',
            '${aafwk_lite_path}/services/abilitymgr_lite:aafwk_services_lite'])

    def test_determinism_and_no_input_mutation(self):
        self.put('b/bundle.json', {'component': {'name': 'b', 'subsystem': 's'}})
        self.put('a/bundle.json', {'component': {'name': 'a', 'subsystem': 's'}})
        original = copy.deepcopy(self.rows)
        rows, diag, _ = self.extract()
        again, _, _ = extract_s3(self.root, list(reversed(self.rows)))
        self.assertEqual(rows, again)
        self.assertEqual(self.rows, original)

    def cli(self, *extra):
        listing = self.base / 'filelists.json'
        listing.write_text(json.dumps(self.rows))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(['--root', str(self.root), '--filelists', str(listing), '--output', str(self.base / 'out'), *extra])

    def test_cli_compact_default_and_optional_evidence(self):
        self.put('a/bundle.json', {'component': {'name': 'a', 'subsystem': 's'}})
        self.assertEqual(self.cli(), 0)
        out = self.base / 'out'
        self.assertIsInstance(json.loads((out / 'requirements.json').read_text()), list)
        self.assertEqual({p.name for p in out.iterdir()}, {'requirements.json', 'diagnostics.json'})
        self.assertEqual(self.cli('--evidence'), 0)
        self.assertTrue((out / 'evidence.json').exists())

    def test_cli_partial_and_reject_output_in_source(self):
        self.put(GN, 'lite_subsystem("x") { subsystem_components = unknown }')
        self.assertEqual(self.cli(), 1)
        self.assertEqual(self.cli('--output', str(self.root / 'out')), 2)
        self.assertFalse((self.root / 'out').exists())

    def test_cli_reject_overwriting_input(self):
        self.rows = []
        listing = self.base / 'requirements.json'
        listing.write_text('[]')
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--root', str(self.root), '--filelists', str(listing), '--output', str(self.base)]), 2)
        self.assertEqual(listing.read_text(), '[]')

    @unittest.skipUnless(jsonschema is not None, 'optional jsonschema not installed')
    def test_current_demo_and_real_gn_match_schemas(self):
        input_schema = json.loads((REPO / 'schema/filelists.schema.json').read_text())
        output_schema = json.loads((REPO / 'schema/requirements.s3.schema.json').read_text())
        jsonschema.Draft202012Validator.check_schema(input_schema)
        jsonschema.Draft202012Validator.check_schema(output_schema)
        rows = json.loads((REPO / 'examples/s3/filelists.json').read_text())
        jsonschema.validate(rows, input_schema)
        result, diag, _ = extract_s3(REPO / 'examples/s3/source', rows)
        self.assertTrue(diag['complete'])
        self.assertEqual(len(result), 13)
        jsonschema.validate(result, output_schema)
        result, diag, _ = extract_s3(REPO / 'tests/fixture_s3_upstream', [
            dict(path='build/lite/config/subsystem/aafwk/BUILD.gn', classes=['S3'])])
        jsonschema.validate(result, output_schema)

    def test_no_files_is_empty_complete_extraction(self):
        result, diag, _ = self.extract()
        self.assertEqual(result, [])
        self.assertEqual(diag, dict(complete=True, files=0, records=0, messages=[]))

    def test_json_condition_keeps_raw_container_and_reports_partial(self):
        self.put('x/bundle.json', {'component': {'name': 'x', 'subsystem': 's',
            'deps': {'kernel_special': {'linux': ['foo']}}}})
        rows, diag, _ = self.extract()
        self.assertFalse(diag['complete'])
        conditional = next(r for r in rows if r['kind'] == 'conditional_requires')
        self.assertEqual(conditional['value'], {'linux': ['foo']})

    def test_invalid_feature_type_stays_in_diagnostics_not_typed_output(self):
        self.put('x/bundle.json', {'component': {'name': 'x', 'subsystem': 's', 'features': 'bad'}})
        rows, diag, _ = self.extract()
        self.assertFalse(diag['complete'])
        self.assertNotIn('features', rows[0])

    def test_gn_outer_if_and_two_independent_subsystems(self):
        rows, diag, _ = self.gn('''if (enabled) {
lite_subsystem("x") { subsystem_components = ["//x"] }
} else {
lite_subsystem("y") { subsystem_components = ["//y"] }
}
lite_subsystem("z") { subsystem_components = [] }
''')
        targets = [r for r in rows if r['kind'] == 'subsystem_components']
        self.assertEqual(targets[0]['when'], ['enabled'])
        self.assertEqual(targets[1]['when'], ['!(enabled)'])
        self.assertNotIn('when', targets[2])
        self.assertFalse(diag['complete'])


if __name__ == '__main__':
    unittest.main()
