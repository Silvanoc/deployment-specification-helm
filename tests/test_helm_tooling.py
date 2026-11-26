"""Focused parser, semantic and structural tests independent of model generation."""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from common import markdown_fences, parse_data, semantic_equal
from schema_validation import precise_validator, semantic_errors
from generate import check_fences
from check_examples import profile_target


class ToolingTests(unittest.TestCase):
    def test_exact_numbers_and_types(self):
        data = parse_data('{"n": 0.12345678901234567890123456789}', '.json')
        self.assertEqual(data['n'], Decimal('0.12345678901234567890123456789'))
        self.assertFalse(semantic_equal(True, Decimal(1)))
        self.assertTrue(semantic_equal(Decimal('1.0'), Decimal(1)))
        self.assertEqual(parse_data('n: 12345678901234567890.123456789\n', '.yaml')['n'], Decimal('12345678901234567890.123456789'))

    def test_duplicate_and_nonfinite_rejected(self):
        for text, suffix in [('a: 1\na: 2\n', '.yaml'), ('{"a":1,"a":2}', '.json'), ('{"a":NaN}', '.json'), ('a: .inf', '.yaml')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_data(text, suffix)

    def test_exact_schema_multiple(self):
        validator = precise_validator({'type': 'number', 'multipleOf': Decimal('0.00000000000000000001')})
        self.assertTrue(validator.is_valid(Decimal('0.00000000000000000002')))
        self.assertFalse(validator.is_valid(Decimal('0.000000000000000000021')))

    def test_semantics_and_fences(self):
        errors, notes = semantic_errors({}, {'type': 'object'})
        self.assertEqual((errors, notes), ([], []))
        self.assertEqual(markdown_fences('```json\n{}\n```\n', {'json'}), ['{}\n'])
        with self.assertRaises(ValueError):
            check_fences('```json\n{}\n')

class FixtureDiscoveryTests(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = Path(__file__).resolve().parents[1]
        for name in ('model', 'specification'):
            shutil.copytree(source / name, self.root / name)

    def run_check(self):
        from unittest.mock import patch, Mock
        import check_examples
        engine = Mock()
        engine.validate.return_value = ([], [])
        with patch.object(check_examples, 'validate') as validator:
            check_examples._check(self.root, engine)
        return validator

    def test_new_modeled_class_and_fixtures_discovered(self):
        schema = self.root / 'model/helm-profile.linkml.yaml'
        text = schema.read_text().replace('  HelmDeploymentCharacteristic:\n', '''  NewCharacteristic:
    is_a: HelmDeploymentCharacteristic
    slot_usage:
      key:
        equals_string: vendor.example/new
  HelmDeploymentCharacteristic:
''')
        schema.write_text(text)
        for kind, body in [('valid', '{"key":"vendor.example/new","properties":{}}'), ('invalid', '{"key":"vendor.example/new"}')]:
            (self.root / 'model/examples/profile' / kind / 'NewCharacteristic-001.json').write_text(body)
        validator = self.run_check()
        self.assertEqual(sum(c.args[1] == 'NewCharacteristic' for c in validator.call_args_list), 2)
        for path in (schema, str(schema)):
            with self.assertRaisesRegex(ValueError, 'matches 0'):
                profile_target({'key': 'vendor.example/new'}, path)

    def test_examples_do_not_require_profile_display_annotations(self):
        import copy
        import check_examples
        from unittest.mock import patch
        from linkml_runtime import SchemaView
        from linkml_runtime.dumpers import yaml_dumper

        schema = self.root / 'model/helm-profile.linkml.yaml'
        source_view = SchemaView(str(schema))
        alias = 'margo-common:workload-target-characteristics.linkml'
        self.assertIn(alias, source_view.imports_closure())
        shared_schema = copy.deepcopy(source_view.schema_map[alias])
        for cls in shared_schema.classes.values():
            for key in ('margo_profile_example', 'margo_profile_kind', 'margo_profile_characteristic'):
                cls.annotations.pop(key, None)
        shared = self.root / 'model/workload-target-local.yaml'
        yaml_dumper.dump(shared_schema, to_file=str(shared))
        schema.write_text(schema.read_text().replace(
            alias, str(shared.with_suffix(''))))
        view = SchemaView(str(schema))
        view.imports_closure()
        self.assertTrue(view.get_class('HelmDeploymentCharacteristic'))
        self.assertTrue(view.get_class('Cpu'))
        for cls in view.all_classes().values():
            for key in ('margo_profile_example', 'margo_profile_kind', 'margo_profile_characteristic'):
                self.assertNotIn(key, cls.annotations)
        expected = {(fixture.name, kind == 'invalid')
                    for kind in ('valid', 'invalid')
                    for fixture in (self.root / 'model/examples/profile' / kind).iterdir()}
        with patch.object(check_examples, 'validate', wraps=check_examples.validate) as actual:
            check_examples.check(self.root)
        observed = {(call.args[2].name, call.kwargs['negative']) for call in actual.call_args_list
                    if call.args[2].parent.parent.name == 'profile'}
        self.assertEqual(observed, expected)

    def test_optional_and_subclass_fixtures_validate_without_documentation(self):
        import check_examples
        import shutil
        import yaml
        from unittest.mock import patch
        schema = self.root / 'model/helm-profile.linkml.yaml'
        model = yaml.safe_load(schema.read_text())
        model['classes']['OptionalNovel'] = {'title': 'Optional Novel', 'rank': 25, 'attributes': {
            'key': {'range': 'string', 'required': True, 'equals_string': 'example/optional'},
            'value': {'range': 'string', 'required': True}}}
        model['classes']['DerivedCpu'] = {'is_a': 'CpuCharacteristic'}
        schema.write_text(yaml.safe_dump(model, sort_keys=False))
        directory = self.root / 'model/examples/profile'
        (directory / 'valid/OptionalNovel-test.json').write_text('{"key":"example/optional","value":"ok"}\n')
        (directory / 'invalid/OptionalNovel-test.json').write_text('{"key":"example/optional"}\n')
        for kind in ('valid', 'invalid'):
            shutil.copy2(directory / kind / 'CpuCharacteristic-001.json', directory / kind / 'DerivedCpu-test.json')
        with patch.object(check_examples, 'validate', wraps=check_examples.validate) as actual:
            check_examples.check(self.root)
        for target in ('OptionalNovel', 'DerivedCpu'):
            calls = [call for call in actual.call_args_list if call.args[1] == target]
            self.assertEqual(len(calls), 2)
            self.assertEqual({call.kwargs['negative'] for call in calls}, {False, True})
        with self.assertRaisesRegex(ValueError, 'matches 0'):
            profile_target({'key': 'example/optional', 'value': 'ok'}, schema)

    def test_missing_and_wrong_class_fixtures_fail(self):
        valid = self.root / 'model/examples/profile/valid/MemoryCharacteristic-001.json'
        invalid = self.root / 'model/examples/profile/invalid/MemoryCharacteristic-001.json'
        invalid.unlink()
        with self.assertRaisesRegex(ValueError, 'Both positive and negative'):
            self.run_check()
        invalid.write_text('{}')
        valid.rename(valid.with_name('StorageCharacteristic-999.json'))
        invalid.rename(invalid.with_name('StorageCharacteristic-999.json'))
        with self.assertRaisesRegex(ValueError, 'drift.*MemoryCharacteristic'):
            self.run_check()


if __name__ == '__main__':
    unittest.main()
