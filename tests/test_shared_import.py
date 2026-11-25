import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from linkml_runtime.utils.schemaview import SchemaView

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from schema_validation import generated_schema, reject_unverified_numeric_constraints, semantic_errors
from generate import render_page


class SharedImportTests(unittest.TestCase):
    def test_live_shared_schema_closure_and_outputs(self):
        root = Path(__file__).resolve().parents[1]
        path = root / 'model/helm-application.linkml.yaml'
        view = SchemaView(str(path))
        alias = 'margo-common:application-description.linkml'
        self.assertIn(alias, view.schema.imports)
        self.assertIn(alias, view.imports_closure())
        shared = view.schema_map[alias]
        self.assertEqual(shared.source_file, 'https://raw.githubusercontent.com/Silvanoc/margo-specification/externalize-component-specs/model/application-description.linkml.yaml')
        expected = {'Metadata', 'Catalog', 'ApplicationMetadata', 'Author', 'Organization',
                    'Configuration', 'Section', 'Setting', 'Schema', 'TextValidationSchema',
                    'BooleanValidationSchema', 'NumericIntegerValidationSchema',
                    'NumericDoubleValidationSchema', 'SelectValidationSchema'}
        self.assertTrue(expected.issubset(shared.classes))
        shared_view = SchemaView(shared.source_file)
        for name in expected | {'ApplicationDescription'}:
            self.assertEqual(view.get_class(name).from_schema, shared.id)
            self.assertEqual(view.get_class(name).description, shared.classes[name].description)
            def slot_contract(schema_view):
                return {s.name: (s.range, s.required, s.multivalued, s.inlined, s.inlined_as_list, s.pattern)
                        for s in schema_view.class_induced_slots(name)}
            self.assertEqual(slot_contract(view), slot_contract(shared_view))
        self.assertTrue(shared.classes['ApplicationDescription'].tree_root)
        self.assertEqual(view.get_class('HelmComponentProperties').is_a, 'OCIComponentProperties')
        self.assertEqual(view.get_uri(view.get_class('DeploymentProfile'), expand=True), 'https://specification.margo.org/DeploymentProfile')
        slots = {slot.name: slot for slot in view.class_induced_slots('Metadata')}
        self.assertTrue(slots['name'].required)
        self.assertTrue(slots['version'].required)
        self.assertTrue(slots['catalog'].required)
        self.assertFalse(slots['description'].required)
        artifact = generated_schema(path)
        self.assertEqual(set(artifact['$defs']['Metadata']['required']), {'name', 'version', 'catalog'})
        page = render_page('application', root, path)
        self.assertIn('metadata:', page)
        self.assertIn('catalog', page)

    def test_profile_shared_classes(self):
        root = Path(__file__).resolve().parents[1]
        view = SchemaView(str(root / 'model/helm-profile.linkml.yaml'))
        view.imports_closure()
        shared = next(schema for schema in view.schema_map.values() if 'CpuCharacteristic' in schema.classes)
        self.assertIs(view.get_class('CpuCharacteristic'), shared.classes['CpuCharacteristic'])
        self.assertEqual(view.get_class('HelmOtherCharacteristic').is_a, 'OtherCharacteristic')

    def test_profile_inherits_shared_contract(self):
        root = Path(__file__).resolve().parents[1]
        view = SchemaView(str(root / 'model/helm-profile.linkml.yaml'))
        profile = view.get_class('HelmProfile')
        self.assertTrue(profile.tree_root)
        self.assertEqual(profile.is_a, 'WorkloadTargetProfile')
        self.assertIn('WorkloadTargetProfile', view.class_ancestors('HelmProfile'))
        self.assertTrue(view.get_class('WorkloadTargetProfile').annotations['margo_extensions_pattern'].value)
        common = view.induced_slot('characteristics', 'WorkloadTargetProfile')
        slot = view.induced_slot('characteristics', 'HelmProfile')
        self.assertEqual(common.annotations['margo_unique_by'].value, 'key')
        self.assertEqual((slot.required, slot.multivalued, slot.inlined, slot.inlined_as_list), (True, True, True, True))
        self.assertEqual([x.range for x in slot.any_of], ['HelmDeploymentCharacteristic', 'CpuCharacteristic',
            'MemoryCharacteristic', 'StorageCharacteristic', 'PeripheralsCharacteristic',
            'InterfacesCharacteristic', 'HelmOtherCharacteristic'])
        self.assertEqual(set(json.loads(slot.annotations['margo_required_characteristics'].value)), {
            'margo.org/deployment/helm', 'margo.org/resource/cpu', 'margo.org/resource/memory',
            'margo.org/resource/storage', 'margo.org/peripherals', 'margo.org/interfaces'})
        pattern = view.induced_slot('key', 'HelmOtherCharacteristic').pattern
        for key in ('vendor.example/feature', 'margo.org/deployment/compose', 'margo.org/deployment/helm-v2'):
            self.assertIsNotNone(re.search(pattern, key))
        self.assertIsNone(re.search(pattern, 'margo.org/deployment/helm'))

    def test_imported_class_keeps_original_namespace(self):
        root = Path(__file__).resolve().parents[1]
        view = SchemaView(str(root / 'model/helm-application.linkml.yaml'))
        self.assertEqual(view.get_uri(view.get_class('Metadata'), expand=True),
                         'https://specification.margo.org/Metadata')

    def test_imported_annotations_and_source_constraints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'shared.yaml').write_text('''id: https://example.org/shared
name: shared
imports: [linkml:types]
classes:
  Choice:
    annotations:
      margo_typed_options: '{"type_slot":"dataType","options_slot":"options","types":{"integer":"integer"}}'
    attributes:
      dataType:
        range: string
        required: true
      options:
        range: Any
        multivalued: true
        required: true
''')
            (path / 'root.yaml').write_text('''id: https://example.org/root
name: root
imports: [linkml:types, shared]
classes:
  Root:
    tree_root: true
    attributes:
      choice:
        range: Choice
        required: true
''')
            schema = generated_schema(path / 'root.yaml')
            self.assertIn('x-margo-typed-options', schema['$defs']['Choice'])
            self.assertTrue(semantic_errors({'choice': {'dataType': 'integer', 'options': ['wrong']}}, schema)[0])
            self.assertIn('choice', schema['required'])
            shared = path / 'shared.yaml'
            shared.write_text(shared.read_text() + '        minimum_value: 1\n')
            with self.assertRaisesRegex(ValueError, 'numeric constraint'):
                generated_schema(path / 'root.yaml')

    def test_remote_source_numeric_check_fails_closed(self):
        view = SimpleNamespace(imports_closure=lambda: ['remote'], schema_map={
            'remote': SimpleNamespace(source_file='https://example.org/shared.yaml')})
        with patch('schema_validation.urlopen', return_value=io.BytesIO(b'classes: {}\n')) as fetch:
            reject_unverified_numeric_constraints(view)
            fetch.assert_called_once_with('https://example.org/shared.yaml', timeout=30)
        with patch('schema_validation.urlopen', return_value=io.BytesIO(b'minimum_value: 1\n')):
            with self.assertRaisesRegex(ValueError, 'numeric constraint'):
                reject_unverified_numeric_constraints(view)
        with patch('schema_validation.urlopen', side_effect=OSError('unavailable')):
            with self.assertRaisesRegex(ValueError, 'Cannot verify'):
                reject_unverified_numeric_constraints(view)
