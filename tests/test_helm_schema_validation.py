import copy
import json
import re
import sys
import tempfile
import subprocess
from decimal import Decimal
import unittest
from unittest.mock import patch
from pathlib import Path
import yaml
import simplejson
from linkml_runtime.utils.yamlutils import DupCheckYamlLoader
from linkml_runtime.utils.schemaview import SchemaView

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from common import ROOT, parse_data, semantic_equal
from schema_validation import ValidationEngine, generated_schema, precise_validator, semantic_errors
import check_examples


class SchemaValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = ValidationEngine()
        cls.profile_schema = ROOT / 'model/helm-profile.linkml.yaml'
        cls.profile = parse_data((ROOT / 'model/examples/profile/valid/HelmProfile-001.json').read_text(), '.json')

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def validate(self, schema, target, data, **kwargs):
        return self.engine.validate(schema, target, data, **kwargs)

    def test_profile_dispatch_and_open_generic_types(self):
        schema = ROOT / 'model/helm-application.linkml.yaml'
        app = parse_data((ROOT / 'model/examples/application/valid/ApplicationDescription-001.yaml').read_text(), '.yaml')
        helm = copy.deepcopy(app['deploymentProfiles'][0])
        unknown = {'id': 'other', 'key': 'vendor.example/deployment/other',
                   'newProfileField': 3, 'x_runtime': 1, 'x-vendor-field': 2,
                   'components': [{'name': 'other', 'x_runtime': True,
                   'newComponentField': True, 'properties': {'nonOci': {'arbitrary': 1},
                   'repository': 'oci://BAD host/foo', 'revision': '1.0.0'}}]}
        compose = copy.deepcopy(unknown)
        compose['key'] = 'margo.org/deployment/compose'
        app['deploymentProfiles'] = [unknown, compose, helm, copy.deepcopy(helm)]
        app['parameters']['idpName']['targets'][0]['vendorTargetField'] = {'custom': True}
        self.assertEqual(self.validate(schema, 'DeploymentProfile', unknown)[0], [])
        errors, notes = self.validate(schema, 'ApplicationDescription', app)
        self.assertEqual(errors, [])
        self.assertTrue(any('generically only' in n for n in notes))
        app['deploymentProfiles'][2]['components'][0]['properties']['repository'] = 'oci://BAD host/foo'
        errors, _ = self.validate(schema, 'ApplicationDescription', app)
        self.assertTrue(any('valid OCI' in e for e in errors), errors)
        app['deploymentProfiles'][2] = copy.deepcopy(unknown)
        app['deploymentProfiles'][2]['key'] = 'margo.org/deployment/helm'
        self.assertTrue(self.validate(schema, 'ApplicationDescription', app)[0])
        app['deploymentProfiles'] = [unknown]
        unknown['x-acme-extensions'] = {}
        self.assertEqual(self.validate(schema, 'ApplicationDescription', app)[0], [])
        unknown['x_bad-extensions'] = {}
        self.assertTrue(self.validate(schema, 'ApplicationDescription', app)[0])
        del unknown['x_bad-extensions']
        unknown['x-acme-extensions'] = 'not an object'
        self.assertTrue(self.validate(schema, 'ApplicationDescription', app)[0])

    def test_bundle_inherits_root_nonnull_and_rejects_duplicate_keys(self):
        helm = ROOT / 'model/helm-application.linkml.yaml'
        prefixes = {'margo-common': yaml.safe_load(helm.read_text())['prefixes']['margo-common']}
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            other = directory / 'other.yaml'
            other.write_text(yaml.safe_dump({'id': 'https://example.org/other', 'name': 'Other',
                'prefixes': prefixes,
                'imports': [str(helm.with_suffix(''))],
                'classes': {'OtherDeploymentProfile': {'is_a': 'DeploymentProfile',
                    'slot_usage': {'key': {'equals_string': 'example.org/deployment/other'}}}}}))
            bundle = directory / 'bundle.yaml'
            bundle.write_text(yaml.safe_dump({'id': 'https://example.org/bundle', 'name': 'Bundle',
                'prefixes': prefixes,
                'imports': [str(helm.with_suffix('')), str(other.with_suffix(''))]}))
            artifact = generated_schema(bundle)
            app = parse_data((ROOT / 'model/examples/application/valid/ApplicationDescription-001.yaml').read_text(), '.yaml')
            app['deploymentProfiles'][0]['description'] = None
            self.assertFalse(precise_validator(artifact).is_valid(app))
            derived = directory / 'derived.yaml'
            derived.write_text(yaml.safe_dump({'id': 'https://example.org/derived', 'name': 'Derived',
                'prefixes': prefixes,
                'imports': [str(bundle.with_suffix(''))], 'classes': {
                    'ExtendedApplication': {'is_a': 'ApplicationDescription', 'tree_root': True}}}))
            derived_schema = generated_schema(derived)
            self.assertIn('ExtendedApplication', derived_schema['$defs'])
            self.assertFalse(precise_validator(derived_schema).is_valid(app))
            app['deploymentProfiles'][0]['description'] = 'valid description'
            app['deploymentProfiles'][0]['components'][0]['properties'] = {'custom': 'invalid for Helm'}
            self.assertFalse(precise_validator(derived_schema).is_valid(app))
            other.write_text(other.read_text().replace('example.org/deployment/other', 'margo.org/deployment/helm'))
            with self.assertRaisesRegex(ValueError, 'Duplicate deployment profile key'):
                generated_schema(bundle)

    def test_inherited_open_characteristic(self):
        value = {'key': 'vendor.example/feature', 'properties': {}, 'vendorField': True}
        self.assertEqual(self.validate(self.profile_schema, 'HelmOtherCharacteristic', value)[0], [])

    def test_json_exponents_preserve_yaml_semantics(self):
        for number in ('1e1', '-1E+2', '1e-2', '-0.12345678901234567890123456789e-12', '1e9999'):
            with self.subTest(number=number):
                text = '{"value": ' + number + '}'
                json_data = parse_data(text, '.json')
                yaml_data = parse_data(text, '.yaml')
                self.assertIsInstance(yaml_data['value'], Decimal)
                self.assertTrue(semantic_equal(json_data, yaml_data))
                self.assertEqual(yaml_data['value'], Decimal(number))
        self.assertEqual(parse_data('value: "1e1"', '.yaml')['value'], '1e1')
        for value in ('+1e1', '01e1', '.1e1'):
            self.assertEqual(parse_data('value: ' + value, '.yaml')['value'], value)
        self.assertEqual(yaml.safe_load('value: 1e1')['value'], '1e1')
        data = parse_data('defaults: &defaults {enabled: yes, number: 0x10}\nitem: {<<: *defaults, enabled: off, date: 2020-01-01, word: "on"}\n', '.yaml')
        self.assertEqual(data['item']['enabled'], False)
        self.assertEqual(data['item']['number'], Decimal(16))
        self.assertEqual(str(data['item']['date']), '2020-01-01')
        self.assertEqual(data['item']['word'], 'on')

    def test_exponents_in_real_instances_and_extensions(self):
        cpu = parse_data('{"cores": 1e1}', '.yaml')
        before = copy.deepcopy(cpu)
        self.assertEqual(self.validate(self.profile_schema, 'Cpu', cpu)[0], [])
        self.assertEqual(cpu, before)
        self.assertIsInstance(cpu['cores'], Decimal)
        app_schema = ROOT / 'model/helm-application.linkml.yaml'
        parameter = parse_data('{"name":"p", "value":1e-2, "targets":[]}', '.yaml')
        before = copy.deepcopy(parameter)
        self.assertEqual(self.validate(app_schema, 'Parameter', parameter)[0], [])
        self.assertEqual(parameter, before)
        self.assertIsInstance(parameter['value'], Decimal)
        profile = copy.deepcopy(self.profile)
        profile['x-acme-extensions'] = parse_data('{"number":1e1}', '.yaml')
        self.assertEqual(self.validate(self.profile_schema, 'HelmProfile', profile)[0], [])
        self.assertIsInstance(profile['x-acme-extensions']['number'], Decimal)

    def test_empty_object_is_not_missing_json_and_yaml(self):
        for text, suffix in (('{"key":"margo.org/deployment/helm","properties":{}}', '.json'), ('key: margo.org/deployment/helm\nproperties: {}\n', '.yaml')):
            data = parse_data(text, suffix)
            self.assertEqual(self.validate(self.profile_schema, 'HelmDeploymentCharacteristic', data)[0], [])
            del data['properties']
            self.assertTrue(self.validate(self.profile_schema, 'HelmDeploymentCharacteristic', data)[0])

    def test_required_characteristics_and_unique_keys(self):
        self.assertEqual(self.validate(self.profile_schema, 'HelmProfile', self.profile)[0], [])
        for i in range(len(self.profile['characteristics'])):
            data = copy.deepcopy(self.profile)
            data['characteristics'].pop(i)
            self.assertTrue(self.validate(self.profile_schema, 'HelmProfile', data)[0], i)
        data = copy.deepcopy(self.profile)
        data['characteristics'].extend([{'key': 'vendor.example/unknown', 'properties': {}}, {'key': 'vendor.example/unknown', 'properties': {'different': 1}}])
        self.assertTrue(any('duplicate key' in e for e in self.validate(self.profile_schema, 'HelmProfile', data)[0]))

    def test_unique_keys_string_fast_path_and_generic_values(self):
        schema = {'type': 'array', 'x-margo-unique-by': 'key'}
        data = [{'key': f'vendor.example/{i}'} for i in range(5000)]
        data.extend([{'key': 'margo.org/resource/cpu'}, {'key': 'unknown.vendor/feature'}])
        self.assertEqual(semantic_errors(data, schema)[0], [])
        data.extend([{'key': 'margo.org/resource/cpu'}, {'key': 'unknown.vendor/feature'}])
        self.assertEqual(len(semantic_errors(data, schema)[0]), 2)
        errors, _ = semantic_errors([{'key': True}, {'key': 1}, {'key': 1.0}, {'key': False}, {'key': 0}], schema)
        self.assertEqual(len(errors), 1)

    def test_extensions(self):
        for key, value, valid in (('x-acme-extensions', {}, True), ('x-acme-extensions', 3, False), ('x-Acme-extensions', {}, False), ('unknown', {}, False)):
            data = copy.deepcopy(self.profile)
            data[key] = value
            self.assertEqual(not self.validate(self.profile_schema, 'HelmProfile', data)[0], valid, key)
        artifact = generated_schema(self.profile_schema)
        pattern = SchemaView(str(self.profile_schema)).get_class('HelmProfile').annotations['margo_extensions_pattern'].value
        self.assertEqual(artifact['patternProperties'][pattern], {'type': 'object'})
        self.assertIn('allOf', artifact['properties']['characteristics'])
        self.assertEqual(artifact['properties']['characteristics']['x-margo-unique-by'], 'key')

    def test_known_characteristic_keys_require_absolute_end(self):
        targets = ('HelmDeploymentCharacteristic', 'CpuCharacteristic', 'MemoryCharacteristic',
                   'StorageCharacteristic', 'PeripheralsCharacteristic', 'InterfacesCharacteristic')
        view = SchemaView(str(self.profile_schema))
        for target in targets:
            path = ROOT / 'model/examples/profile/valid' / f'{target}-001.json'
            fixture = parse_data(path.read_text(), '.json')
            self.assertEqual(fixture['key'], view.induced_slot('key', target).equals_string)
            validator = precise_validator(generated_schema(self.profile_schema, target))
            for suffix in ('', '\n', '\r\n'):
                with self.subTest(target=target, suffix=repr(suffix)):
                    data = {**fixture, 'key': fixture['key'] + suffix}
                    self.assertEqual(not self.validate(self.profile_schema, target, data)[0], not suffix)
                    self.assertEqual(validator.is_valid(data), not suffix)

    def test_profile_extension_key_requires_absolute_end(self):
        validator = precise_validator(generated_schema(self.profile_schema, 'HelmProfile'))
        for suffix in ('', '\n', '\r\n'):
            with self.subTest(suffix=repr(suffix)):
                data = copy.deepcopy(self.profile)
                data['x-acme-extensions' + suffix] = {}
                self.assertEqual(not self.validate(self.profile_schema, 'HelmProfile', data)[0], not suffix)
                self.assertEqual(validator.is_valid(data), not suffix)

    def test_profile_optional_typed_fields_are_not_nullable(self):
        cases = (('Cpu', {'cores': 1}, 'architecture', ('amd64',)),
                 ('Peripheral', {'type': 'gpu'}, 'manufacturer', ('', 'vendor')),
                 ('Peripheral', {'type': 'gpu'}, 'model', ('', 'model')))
        for target, base, field, values in cases:
            validator = precise_validator(generated_schema(self.profile_schema, target))
            for data, valid in [(base, True), ({**base, field: None}, False)] + [({**base, field: value}, True) for value in values]:
                with self.subTest(target=target, field=field, data=data):
                    self.assertEqual(not self.validate(self.profile_schema, target, data)[0], valid)
                    self.assertEqual(validator.is_valid(data), valid)

    def test_application_optional_typed_fields_reject_null(self):
        schema = ROOT / 'model/helm-application.linkml.yaml'
        fixture = ROOT / 'model/examples/application/valid/ApplicationDescription-001.yaml'
        original = parse_data(fixture.read_text(), '.yaml')
        published = precise_validator(generated_schema(schema))
        absent = copy.deepcopy(original)
        absent['parameters']['idpName'].pop('value')
        absent['metadata'].pop('description')
        absent['metadata']['catalog']['application'].pop('tagline')
        absent['metadata']['catalog']['application'].pop('tags')
        absent['deploymentProfiles'][0].pop('description', None)
        absent['configuration']['sections'][0]['settings'][0].pop('description')
        self.assertTrue(published.is_valid(absent))
        self.assertFalse(self.validate(schema, 'ApplicationDescription', absent)[0])
        parameter_schema = precise_validator(generated_schema(schema, 'Parameter'))
        parameter = {'name': 'p', 'targets': []}
        self.assertTrue(parameter_schema.is_valid(parameter))
        self.assertFalse(parameter_schema.is_valid(dict(parameter, value=None)))
        self.assertTrue(self.validate(schema, 'Parameter', dict(parameter, value=None))[0])

        paths = (
            ('parameters', 'idpName', 'value'),
            ('metadata', 'description'),
            ('metadata', 'catalog', 'application', 'tagline'),
            ('metadata', 'catalog', 'application', 'tags'),
            ('deploymentProfiles', 0, 'description'),
            ('configuration', 'sections', 0, 'settings', 0, 'description'),
            ('configuration', 'sections', 0, 'settings', 0, 'immutable'),
            ('configuration', 'sections'),
        )
        for path in paths:
            obj = copy.deepcopy(original)
            parent = obj
            for step in path[:-1]:
                parent = parent[step]
            parent[path[-1]] = None
            with self.subTest(path=path):
                self.assertFalse(published.is_valid(obj))
                self.assertTrue(self.validate(schema, 'ApplicationDescription', obj)[0])

        for value in ('', False, [], ['a', 'b'], [1, 2], [True, False]):
            obj = copy.deepcopy(original)
            obj['parameters']['idpName']['value'] = value
            obj['metadata']['description'] = ''
            obj['metadata']['catalog']['application']['tagline'] = ''
            obj['metadata']['catalog']['application']['tags'] = []
            obj['x-acme-extensions'] = {'nested': {'arbitrary': None}}
            with self.subTest(value=value):
                self.assertTrue(published.is_valid(obj))
                self.assertFalse(self.validate(schema, 'ApplicationDescription', obj)[0])

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'instance.json'
            command = [sys.executable, str(ROOT / 'tools/validate.py'), '--schema', str(schema), '--target-class', 'ApplicationDescription', str(path)]
            path.write_text(simplejson.dumps(absent, use_decimal=True))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            absent['parameters']['idpName']['value'] = None
            path.write_text(simplejson.dumps(absent, use_decimal=True))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)

    def test_nonnull_schema_metadata_validation(self):
        source = (ROOT / 'model/helm-application.linkml.yaml').read_text()
        with tempfile.TemporaryDirectory() as temp:
            schema = Path(temp) / 'schema.yaml'
            pattern = r'(?m)^([ \t]*margo_optional_typed_nonnull:[ \t]*)([\'"])true\2[ \t]*$'
            for quote in ("'", '"'):
                with self.subTest(quote=quote):
                    invalid, count = re.subn(pattern, lambda match: f'{match[1]}{quote}invalid{quote}', source)
                    self.assertEqual(count, 1)
                    schema.write_text(invalid)
                    with self.assertRaisesRegex(ValueError, 'Invalid margo_optional_typed_nonnull'):
                        generated_schema(schema)

    def test_oci_repository_uri_direct_and_nested(self):
        schema = ROOT / 'model/helm-application.linkml.yaml'
        valid = ('oci://localhost:5000/team/name', 'oci://my--registry.example/team/name', 'oci://registry.example/team/foo.bar_baz__qux--end', 'oci://127.0.0.1:5000/team/name', 'oci://[2001:db8::1]:5000/team/name')
        invalid = ('oci://not a URI', 'oci://registry.example/repo\nother', 'oci:///?', 'oci://registry.example/repo?', 'oci://registry.example/repo#', 'oci://user@registry.example/repo', 'oci://registry.example/repo:tag', 'oci://registry.example/repo@sha256:abc', 'oci://registry.example/repo/', 'oci://registry.example/Bad', 'oci://registry.example/a..b', 'oci://-registry.example/team/name', 'oci://registry-.example/team/name', 'oci://registry.example:/repo', 'oci://registry.example:port/repo', 'oci://[2001:db8::zz]/repo', 'oci://registry.example')
        for uri in valid + invalid:
            with self.subTest(uri=uri):
                data = {'repository': uri, 'revision': '1.0.0'}
                self.assertEqual(not self.validate(schema, 'HelmComponentProperties', data)[0], uri in valid)
        fixture = ROOT / 'model/examples/application/valid/ApplicationDescription-001.yaml'
        app = parse_data(fixture.read_text(), '.yaml')
        self.assertFalse(self.validate(schema, 'ApplicationDescription', app)[0])
        app['deploymentProfiles'][0]['components'][0]['properties']['repository'] = 'oci://registry.example/repo:tag'
        self.assertTrue(any('OCI repository URI' in e for e in self.validate(schema, 'ApplicationDescription', app)[0]))
        artifact = generated_schema(schema, 'HelmComponentProperties')
        self.assertTrue(artifact['properties']['repository']['x-margo-oci-repository'])
        self.assertTrue(artifact['$defs']['HelmComponentProperties']['properties']['repository']['x-margo-oci-repository'])

    def test_select_options_follow_declared_scalar_type(self):
        schema = ROOT / 'model/helm-application.linkml.yaml'
        cases = [('integer', [30, 60], True), ('boolean', [True, False], True), ('string', ['a', 'b'], True), ('double', [Decimal('1.5'), 2], True), ('integer', [True], False), ('integer', ['30'], False), ('boolean', [1], False), ('string', [2], False), ('double', [True], False)]
        for datatype, options, valid in cases:
            with self.subTest(datatype=datatype, options=options):
                data = {'name': 'selection', 'dataType': datatype, 'options': options}
                self.assertEqual(not self.validate(schema, 'SelectValidationSchema', data)[0], valid)
        data = {'name': 'selection', 'dataType': 'array[string]', 'options': [['a']]}
        errors, notes = self.validate(schema, 'SelectValidationSchema', data)
        self.assertFalse(errors)
        self.assertTrue(any('not verified' in n for n in notes))
        artifact = generated_schema(schema, 'SelectValidationSchema')
        self.assertEqual(artifact['x-margo-typed-options'], artifact['$defs']['SelectValidationSchema']['x-margo-typed-options'])
        fixture = ROOT / 'model/examples/application/valid/ApplicationDescription-001.yaml'
        app = parse_data(fixture.read_text(), '.yaml')
        app['configuration']['schema'].append({'name': 'selection', 'dataType': 'integer', 'options': [True]})
        self.assertTrue(any('options' in e and 'integer' in e for e in self.validate(schema, 'ApplicationDescription', app)[0]))

    def test_parsing_and_semantic_roundtrip(self):
        text = '{"@context":{}, "array":[], "null":null, "object":{}}'
        data = parse_data(text, '.json')
        self.assertEqual(data, parse_data(json.dumps(data, sort_keys=True, indent=2), '.json'))
        for key in ('@context', 'array', 'null', 'object'):
            other = dict(data)
            del other[key]
            self.assertNotEqual(data, other)
        scalars = 'switch: on\ndisabled: off\ndate: 2020-01-01\ntime: 2020-01-01T01:02:03Z\nquoted: "on"\n'
        self.assertEqual(parse_data(scalars, '.yaml'), yaml.load(scalars, Loader=DupCheckYamlLoader))
        for text, suffix in (( '{"a":1,"a":2}', '.json'), ('{"a":NaN}', '.json'), ('{bad', '.json'), ('a: 1\na: 2\n', '.yaml'), ('a: [\n', '.yaml'), ('a: .nan\n', '.yaml')):
            with self.subTest(text=text), self.assertRaises(Exception):
                parse_data(text, suffix)

    def test_lossless_numbers_through_linkml_plugin(self):
        schema_text = '''id: https://example.org/numbers
name: numbers
prefixes:
  linkml: https://w3id.org/linkml/
imports:
  - linkml:types
classes:
  NumericRecord:
    tree_root: true
    attributes:
      integerValue:
        range: integer
        required: true
      numberValue:
        range: double
        required: true
'''
        a = parse_data('{"integerValue":9007199254740993.0,"numberValue":0.123456789012345678901}', '.json')
        b = parse_data('{"integerValue":9007199254740992.0,"numberValue":0.123456789012345678901}', '.json')
        yaml_value = parse_data('integerValue: 9007199254740993.0\nnumberValue: 0.123456789012345678901\n', '.yaml')
        self.assertEqual(a['integerValue'], Decimal('9007199254740993.0'))
        self.assertNotEqual(a['integerValue'], b['integerValue'])
        self.assertFalse(semantic_equal(a, b))
        self.assertTrue(semantic_equal(a, yaml_value))
        self.assertTrue(semantic_equal(parse_data('1', '.json'), parse_data('1.0', '.json')))
        self.assertFalse(semantic_equal(parse_data('true', '.json'), parse_data('1', '.json')))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'numbers.yaml'
            path.write_text(schema_text)
            huge_text = '9' * 4301
            huge = parse_data('{"integerValue":' + huge_text + ',"numberValue":0.125}', '.json')
            self.assertEqual(huge['integerValue'], Decimal(huge_text))
            self.assertFalse(semantic_equal(True, huge['integerValue']))
            self.assertFalse(self.validate(path, 'NumericRecord', huge)[0])
            fixture = Path(temp) / 'huge.json'
            fixture.write_text('{"integerValue":' + huge_text + ',"numberValue":0.125}')
            command = [sys.executable, str(ROOT / 'tools/validate.py'), '--schema', str(path), '--target-class', 'NumericRecord', str(fixture)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with self.assertRaisesRegex(ValueError, 'safe multipleOf precision limit'):
                precise_validator({'type': 'number', 'multipleOf': 2}).is_valid(huge['integerValue'])
            for value, valid in ((a, True), (yaml_value, True), (parse_data('{"integerValue":1.0,"numberValue":1e1000000000}', '.json'), True), (parse_data('{"integerValue":1e1000000000,"numberValue":0.1}', '.json'), True), (parse_data('{"integerValue":0e-1000000000,"numberValue":0.1}', '.json'), True), (parse_data('{"integerValue":1e-1000000000,"numberValue":0.1}', '.json'), False), (parse_data('{"integerValue":1.5,"numberValue":0.1}', '.json'), False), (parse_data('{"integerValue":true,"numberValue":0.1}', '.json'), False)):
                with self.subTest(value=value):
                    errors, _ = self.validate(path, 'NumericRecord', value)
                    self.assertEqual(not errors, valid, errors)
        self.assertTrue(precise_validator({'type': 'number', 'multipleOf': 0.1}).is_valid(Decimal('1e1000000000')))
        self.assertFalse(precise_validator({'type': 'number', 'multipleOf': 0.1}).is_valid(Decimal('0.10000000000000000001')))
        self.assertFalse(precise_validator({'type': 'number', 'maximum': 9007199254740992}).is_valid(a['integerValue']))

    def test_lossless_yaml_float_scalars(self):
        value = parse_data('high: 9007199254740993.0\nfraction: 0.123456789012345678901\nnegative_zero: -0.0\nsexagesimal: 1:20.5\nunderscores: 1_000.25\nhuge: 1.0e+1000000000\n', '.yaml')
        self.assertEqual(value['high'], Decimal('9007199254740993.0'))
        self.assertEqual(value['fraction'], Decimal('0.123456789012345678901'))
        self.assertTrue(value['negative_zero'].is_signed())
        self.assertEqual(value['sexagesimal'], Decimal('80.5'))
        self.assertEqual(value['underscores'], Decimal('1000.25'))
        self.assertEqual(value['huge'], Decimal('1e1000000000'))
        for token in ('.inf', '-.inf', '.nan'):
            with self.subTest(token=token), self.assertRaises(ValueError):
                parse_data('number: ' + token, '.yaml')

    def test_lossless_yaml_integer_scalars_and_validator(self):
        scalars = 'positive: +1_234\nnegative: -12\nbinary: -0b1010\noctal: 075\nhex: +0x2a\nsexagesimal: 1:02:03\n'
        data = parse_data(scalars, '.yaml')
        self.assertEqual(data, yaml.safe_load(scalars))
        self.assertEqual(data, {'positive': 1234, 'negative': -12, 'binary': -10, 'octal': 61, 'hex': 42, 'sexagesimal': 3723})
        digits = '9' * 4301
        yaml_value = parse_data('integerValue: ' + digits + '\nnumberValue: 0.125\n', '.yaml')
        json_value = parse_data('{"integerValue":' + digits + ',"numberValue":0.125}', '.json')
        self.assertTrue(semantic_equal(yaml_value, json_value))
        self.assertEqual(yaml_value['integerValue'], Decimal(digits))
        schema = '''id: https://example.org/yaml-integers
name: yaml-integers
prefixes:
  linkml: https://w3id.org/linkml/
imports:
  - linkml:types
classes:
  Record:
    tree_root: true
    attributes:
      integerValue:
        range: integer
        required: true
      numberValue:
        range: double
        required: true
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'schema.yaml'
            path.write_text(schema)
            self.assertFalse(self.validate(path, 'Record', yaml_value)[0])
            fixture = Path(temp) / 'value.yaml'
            fixture.write_text('integerValue: ' + digits + '\nnumberValue: 0.125\n')
            command = [sys.executable, str(ROOT / 'tools/validate.py'), '--schema', str(path), '--target-class', 'Record', str(fixture)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_generated_numeric_schema_preserves_decimal_json(self):
        schema_text = '''id: https://example.org/generated
name: generated
prefixes:
  linkml: https://w3id.org/linkml/
imports:
  - linkml:types
classes:
  Record:
    tree_root: true
    attributes:
      value:
        range: double
'''
        raw = '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{"value":{"type":"number","minimum":9007199254740993.0}},"$defs":{"Record":{"type":"object","properties":{"value":{"type":"number","minimum":9007199254740993.0}}}}}'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'schema.yaml'
            path.write_text(schema_text)
            with patch('schema_validation.subprocess.run', return_value=subprocess.CompletedProcess([], 0, raw, '')):
                artifact = generated_schema(path)
            bound = artifact['properties']['value']['minimum']
            self.assertEqual(bound, Decimal('9007199254740993.0'))
            serialized = simplejson.dumps(artifact, use_decimal=True)
            self.assertIn('"minimum": 9007199254740993.0', serialized)
            self.assertEqual(json.loads(serialized, parse_float=Decimal)['properties']['value']['minimum'], bound)
            validator = precise_validator(artifact)
            self.assertFalse(validator.is_valid({'value': Decimal('9007199254740992.0')}))
            self.assertTrue(validator.is_valid({'value': Decimal('9007199254740993.0')}))
            path.write_text(schema_text.replace('range: double', 'range: double\n        minimum_value: 9007199254740993.0'))
            with self.assertRaisesRegex(ValueError, 'Cannot verify LinkML numeric constraint minimum_value'):
                generated_schema(path)

    def test_nested_schema_dialect_is_rejected(self):
        schema = {'type': 'object', 'properties': {'value': {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'integer'}}}
        with self.assertRaisesRegex(ValueError, r'Nested \$schema'):
            precise_validator(schema)
        for keyword, literal in (('const', {'$schema': 'example'}), ('enum', [{'$schema': 'example'}]), ('examples', [{'$schema': 'example'}]), ('default', {'$schema': 'example'})):
            with self.subTest(keyword=keyword):
                precise_validator({keyword: literal})
        with self.assertRaisesRegex(ValueError, r'Nested \$schema'):
            precise_validator({'allOf': [{'$schema': 'https://json-schema.org/draft/2020-12/schema'}]})

    def test_yaml_merge_precedence_and_duplicates(self):
        text = 'first: &first\n  a: 1\n  shared: first\nsecond: &second\n  b: 2\n  shared: second\nmerged:\n  <<: [*first, *second]\n  a: 3\nalias: *first\n'
        parsed = parse_data(text, '.yaml')
        self.assertEqual(parsed, yaml.safe_load(text))
        self.assertEqual(parsed['merged']['a'], 3)
        self.assertEqual(parsed['merged']['b'], 2)
        self.assertEqual(parsed['alias'], {'a': 1, 'shared': 'first'})
        for text in ('base: &base {a: 1}\nmerged: {<<: *base, a: 2, a: 3}\n', 'first: &first {a: 1, a: 2}\nmerged: {<<: *first}\n', 'merged: {<<: {}, <<: {}}\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_data(text, '.yaml')

    def test_validation_receives_unmodified_instance(self):
        self.validate(self.profile_schema, 'HelmProfile', self.profile)
        validator = self.engine.cache[(str(self.profile_schema.resolve()), 'HelmProfile')][1]
        value = parse_data('{"@context":{},"characteristics":[],"null":null,"object":{}}', '.json')
        with patch.object(validator, 'validate', wraps=validator.validate) as call:
            self.validate(self.profile_schema, 'HelmProfile', value)
            self.assertEqual(call.call_args.args[0], value)
            self.assertEqual(call.call_args.args[0]['@context'], {})
            self.assertEqual(call.call_args.args[0]['characteristics'], [])
            self.assertIsNone(call.call_args.args[0]['null'])
            self.assertEqual(call.call_args.args[0]['object'], {})
            reduced = {}
            self.validate(self.profile_schema, 'HelmProfile', reduced)
            self.assertEqual(call.call_args.args[0], {})

    def test_real_invalid_fixtures(self):
        for group, target, suffix in (('profile', 'HelmProfile', '.json'),):
            schema = ROOT / 'model' / f'helm-{group}.linkml.yaml'
            path = ROOT / 'model/examples' / group / 'invalid' / f'{target}-001{suffix}'
            data = parse_data(path.read_text(), suffix)
            self.assertTrue(self.validate(schema, target, data)[0])

    def test_inherited_extensions(self):
        schema_text = '''id: https://example.org/inheritance
name: inheritance
prefixes:
  linkml: https://w3id.org/linkml/
imports:
  - linkml:types
classes:
  Base:
    annotations:
      margo_extensions_pattern: '^x-[a-z]+-extensions$'
    attributes:
      name:
        range: string
  Child:
    is_a: Base
    tree_root: true
    attributes:
      count:
        range: integer
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'inheritance.yaml'
            path.write_text(schema_text)
            artifact = generated_schema(path)
            self.assertIn('^x-[a-z]+-extensions$', artifact['$defs']['Child']['patternProperties'])
            self.assertFalse(self.validate(path, 'Child', {'name': 'n', 'x-ok-extensions': {}})[0])
            self.assertTrue(self.validate(path, 'Child', {'name': 'n', 'x-ok-extensions': 'bad'})[0])

    def test_selector_rules_and_nested_expressions(self):
        schema = ROOT / 'model/helm-application.linkml.yaml'
        artifact = generated_schema(schema, 'MatchExpression')
        rules = artifact['x-margo-selector-rules']
        self.assertEqual(rules, artifact['$defs']['MatchExpression']['x-margo-selector-rules'])
        self.assertNotIn('object_array', artifact['$defs']['ItemMatchExpression']['x-margo-selector-rules'])
        def expression(operator, **kwargs):
            return {'key': 'example/key', 'operator': operator, **kwargs}
        cases = [
            (expression('In', property='/p', values=[True]), True),
            (expression('In', property='/p', values=['a', 'b']), True),
            (expression('In', property='/p', values=[1, 2.5]), True),
            (expression('In', property='/p', values=['a', 2]), False),
            (expression('In', property='/p', values=[True, False]), False),
            (expression('In', property='/p', values=[True, 1]), False),
            (expression('In', property='/p', values=[]), False),
            (expression('In', property='/p', values=None), False),
            (expression('In', values=['a']), False),
            (expression('Gt', property='/p', values=['-1.25e+2']), True),
            (expression('Gt', property='/p', values=['NaN']), False),
            (expression('Gt', property='/p', values=['1_000']), False),
            (expression('Gt', property='/p', values=[True]), False),
            (expression('Gt', property='/p', values=[1, 2]), False),
            (expression('Exists'), True),
            (expression('Exists', values=None), False),
            (expression('Exists', values=[]), False),
            (expression('ContainsAny', property='/p', itemSelector={'matchExpressions': []}), False),
            (expression('ContainsAny', property='/p', values=[], itemSelector={'matchExpressions': [{'property': '/n', 'operator': 'Exists'}]}), False),
            (expression('ContainsAny', property='/p', itemSelector={'matchExpressions': [{'property': '/n', 'operator': 'Gt', 'values': ['5']}]}), True),
            # Nested object-array semantics are deliberately unspecified.
            (expression('ContainsAny', property='/p', itemSelector={'matchExpressions': [{'property': '/n', 'operator': 'ContainsAny'}]}), True),
        ]
        for value, valid in cases:
            with self.subTest(value=value):
                errors, _ = self.validate(schema, 'MatchExpression', value)
                self.assertEqual(not errors, valid, errors)

    def test_selector_rules_apply_inside_application(self):
        schema = ROOT / 'model/helm-application.linkml.yaml'
        fixture = ROOT / 'model/examples/application/valid/ApplicationDescription-001.yaml'
        data = parse_data(fixture.read_text(), '.yaml')
        self.assertFalse(self.validate(schema, 'ApplicationDescription', data)[0])
        expression = data['deploymentProfiles'][0]['placementConstraints'][0]['matchExpressions'][0]
        expression['itemSelector']['matchExpressions'][1]['values'] = [True, False]
        self.assertTrue(any('homogeneous' in error for error in self.validate(schema, 'ApplicationDescription', data)[0]))
