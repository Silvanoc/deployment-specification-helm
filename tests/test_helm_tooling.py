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
        text = schema.read_text().replace('  CpuCharacteristic:\n', '''  NewCharacteristic:
    is_a: HelmDeploymentCharacteristic
    annotations:
      margo_profile_kind: characteristic
      margo_profile_characteristic: NewCharacteristic
    slot_usage:
      key:
        equals_string: vendor.example/new
        pattern: '^vendor\\.example/new$'
  CpuCharacteristic:
''')
        schema.write_text(text)
        for kind, body in [('valid', '{"key":"vendor.example/new","properties":{}}'), ('invalid', '{"key":"vendor.example/new"}')]:
            (self.root / 'model/examples/profile' / kind / 'NewCharacteristic-001.json').write_text(body)
        doc = self.root / 'specification/margo-management-interface/helm-profile-characteristics.md'
        doc.write_text(doc.read_text() + '\n```json\n{"key":"vendor.example/new","properties":{}}\n```\n')
        validator = self.run_check()
        self.assertTrue(any(c.args[1] == 'NewCharacteristic' for c in validator.call_args_list))
        self.assertEqual(profile_target({'key': 'vendor.example/new'}, schema), 'NewCharacteristic')

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
