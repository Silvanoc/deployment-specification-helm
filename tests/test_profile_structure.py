import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

import yaml
from linkml_runtime.utils.schemaview import SchemaView

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from common import parse_data
from profile_structure import profile_display, profile_sections, profile_target
from schema_validation import generated_schema

SUBSET = 'DeploymentTechnologyCharacteristics'
DESCRIPTION = 'Characteristics indicating support for a deployment technology.'


class ProfileStructureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo[1]'
        (self.root / 'model').mkdir(parents=True)
        self.examples = self.root / 'model/examples/profile/valid'
        shutil.copytree(ROOT / 'model/examples/profile/valid', self.examples)
        source = ROOT / 'model/helm-profile.linkml.yaml'
        view = SchemaView(str(source))
        source_id = next(i for i in view.imports_closure() if i.endswith('workload-target-characteristics.linkml'))
        location = view.schema_map[source_id].source_file
        if urlsplit(location).scheme in ('http', 'https'):
            with urlopen(location, timeout=30) as response:
                shared = yaml.safe_load(response.read())
        else:
            shared = yaml.safe_load(Path(location).read_text())
        self.model = yaml.safe_load(source.read_text())
        for schema in (shared, self.model):
            for cls in schema.get('classes', {}).values():
                for key in ('margo_profile_kind', 'margo_profile_characteristic'):
                    cls.get('annotations', {}).pop(key, None)
                if cls.get('annotations') == {}:
                    cls.pop('annotations')
        self.model['imports'] = ['shared' if item == source_id else item for item in self.model['imports']]
        self.model['subsets'] = {SUBSET: {'description': DESCRIPTION}}
        self.model['classes']['HelmDeploymentCharacteristic']['in_subset'] = [SUBSET]
        self.shared = shared
        self.save()

    def save(self):
        (self.root / 'model/shared.yaml').write_text(yaml.safe_dump(self.shared, sort_keys=False))
        (self.root / 'model/profile.yaml').write_text(yaml.safe_dump(self.model, sort_keys=False))

    def sections(self):
        self.save()
        return profile_sections(SchemaView(str(self.root / 'model/profile.yaml')))

    def add_root(self, name):
        self.model['classes']['HelmProfile']['slot_usage']['characteristics']['any_of'].append({'range': name})

    def require(self, name):
        self.model['classes']['HelmProfile']['all_of'].append({'slot_conditions': {
            'characteristics': {'has_member': {'range': name}}}})

    def test_real_model_six_sections_and_annotation_independence(self):
        sections = self.sections()
        self.assertEqual([(x.target, x.display, x.kind, x.rank) for x in sections], [
            ('HelmDeploymentCharacteristic', 'HelmDeploymentCharacteristic', 'deployment', 10),
            ('CpuCharacteristic', 'Cpu', 'characteristic', 20),
            ('MemoryCharacteristic', 'MemoryProperties', 'characteristic', 30),
            ('StorageCharacteristic', 'StorageProperties', 'characteristic', 40),
            ('PeripheralsCharacteristic', 'Peripheral', 'characteristic', 50),
            ('InterfacesCharacteristic', 'Interface', 'characteristic', 60),
        ])
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        self.assertIsNotNone(view.get_subset(SUBSET))
        self.assertFalse(any(k in c.annotations for c in view.all_classes().values() for k in ('margo_profile_kind', 'margo_profile_characteristic')))
        self.assertEqual([view.get_class(s.display).title for s in sections],
                         ['Helm Deployment Technology Support', 'CPUs', 'Memory', 'Storage', 'Peripherals', 'Interfaces'])
        self.assertEqual([view.induced_slot('key', s.target).equals_string for s in sections],
                         ['margo.org/deployment/helm', 'margo.org/resource/cpu', 'margo.org/resource/memory',
                          'margo.org/resource/storage', 'margo.org/peripherals', 'margo.org/interfaces'])
        for section in sections:
            self.assertTrue(view.get_class(section.display).title)
            self.assertEqual(view.get_class(section.display).rank, section.rank)
            self.assertTrue(view.induced_slot('key', section.target).equals_string)
        self.assertNotIn('HelmOtherCharacteristic', [s.target for s in sections])
        self.assertIsNone(profile_display(view, 'HelmOtherCharacteristic'))
        self.assertEqual(profile_display(view, 'CpuCharacteristic'), 'Cpu')
        with self.assertRaisesRegex(ValueError, 'Unknown profile characteristic class'):
            profile_display(view, 'MissingClass')

    def test_novel_fixture_not_added_to_profile_union(self):
        self.model['classes']['Novel'] = {'title': 'Novel', 'rank': 25, 'attributes': {'key': {'range': 'string', 'equals_string': 'example/novel'}}}
        (self.examples / 'Novel-custom.json').write_text('{"key":"example/novel"}\n')
        sections = self.sections()
        self.assertFalse(any(x.target == 'Novel' for x in sections))
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        self.assertNotIn('Novel', [x.range for x in view.induced_slot('characteristics', 'HelmProfile').any_of])

    def test_mixin_slots_and_subset_inheritance(self):
        classes = self.model['classes']
        classes['SupportMixin'] = {'mixin': True, 'in_subset': [SUBSET], 'attributes': {
            'key': {'range': 'string', 'equals_string': 'example/mixin'},
            'properties': {'range': 'MixinProperties', 'inlined': True}}}
        classes['MixinTarget'] = {'mixins': ['SupportMixin']}
        classes['MixinProperties'] = {'title': 'Mixin Properties', 'rank': 25}
        self.add_root('MixinTarget')
        self.require('MixinTarget')
        section = next(x for x in self.sections() if x.target == 'MixinTarget')
        self.assertEqual((section.display, section.kind, section.rank), ('MixinProperties', 'deployment', 25))

    def test_mixin_supplies_required_membership(self):
        classes = self.model['classes']
        classes['MixinRequired'] = {'title': 'Required mixin', 'rank': 25, 'attributes': {
            'key': {'range': 'string', 'equals_string': 'example/required-mixin'}}}
        classes['ProfileMixin'] = {'mixin': True, 'all_of': [{'slot_conditions': {
            'characteristics': {'has_member': {'range': 'MixinRequired'}}}}]}
        classes['HelmProfile']['mixins'] = ['ProfileMixin']
        self.add_root('MixinRequired')
        self.assertIn('MixinRequired', [section.target for section in self.sections()])

    def test_slot_level_membership_fails_closed_for_documentation(self):
        self.model['classes']['HelmProfile']['slot_usage']['characteristics']['has_member'] = {
            'range': 'CpuCharacteristic'}
        self.save()
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        self.assertIsNotNone(view.induced_slot('characteristics', 'HelmProfile').has_member)
        with self.assertRaisesRegex(ValueError, 'Slot-level has_member.*unsupported for profile documentation'):
            profile_sections(view)
        with self.assertRaisesRegex(ValueError, 'Slot-level has_member.*unsupported for profile documentation'):
            profile_target(view, {'key': 'margo.org/resource/cpu'})

    def test_cycle_and_ambiguous_display(self):
        classes = self.model['classes']
        classes['CyclicTarget'] = {'attributes': {'key': {'range': 'string', 'equals_string': 'example/cycle'}, 'properties': {'range': 'CycleA', 'inlined': True}}}
        classes['CycleA'] = {'attributes': {'next': {'range': 'CycleB', 'inlined': True}}}
        classes['CycleB'] = {'title': 'Cycle', 'rank': 25, 'attributes': {'next': {'range': 'CycleA', 'inlined': True}}}
        self.add_root('CyclicTarget')
        classes['HelmProfile']['all_of'].append({'slot_conditions': {
            'characteristics': {'has_member': {'range': 'CyclicTarget'}}}})
        (self.examples / 'CyclicTarget-test.json').write_text('{"key":"example/cycle"}\n')
        self.assertEqual(next(x for x in self.sections() if x.target == 'CyclicTarget').display, 'CycleB')
        classes['CycleA'].update(title='Another', rank=26)
        with self.assertRaisesRegex(ValueError, 'Ambiguous display'):
            self.sections()
        classes['CycleA'].pop('title')
        classes['CycleA'].pop('rank')
        classes['CycleB'].pop('title')
        classes['CycleB'].pop('rank')
        with self.assertRaisesRegex(ValueError, 'Missing display'):
            self.sections()

    def test_optional_exact_key_without_display_is_not_a_section(self):
        self.model['classes']['OptionalTarget'] = {'attributes': {
            'key': {'range': 'string', 'equals_string': 'example/optional'}}}
        self.add_root('OptionalTarget')
        self.assertNotIn('OptionalTarget', [section.target for section in self.sections()])
        (self.examples / 'OptionalTarget-test.json').write_text('{"key":"example/optional"}\n')
        (self.examples / 'HelmOtherCharacteristic-test.json').write_text('{"key":"vendor/other","properties":{}}\n')
        sections = [section.target for section in self.sections()]
        self.assertNotIn('OptionalTarget', sections)
        self.assertNotIn('HelmOtherCharacteristic', sections)

    def test_subset_definitions(self):
        self.shared['subsets'] = {SUBSET: {'description': DESCRIPTION}}
        self.assertEqual(len(self.sections()), 6)
        self.shared['subsets'][SUBSET]['description'] = 'Different meaning.'
        with self.assertRaisesRegex(ValueError, 'Conflicting profile subset'):
            self.sections()
        self.shared.pop('subsets')
        self.model.pop('subsets')
        with self.assertRaisesRegex(ValueError, 'Missing profile subset'):
            self.sections()

    def test_subset_and_removed_annotations_do_not_change_instance_schema(self):
        path = self.root / 'model/profile.yaml'
        native = generated_schema(path)
        self.model.pop('subsets')
        deployment = self.model['classes']['HelmDeploymentCharacteristic']
        deployment.pop('in_subset')
        deployment['annotations'] = {'margo_profile_kind': 'deployment',
                                     'margo_profile_characteristic': 'HelmDeploymentCharacteristic'}
        self.shared['classes']['Cpu'].setdefault('annotations', {}).update({
            'margo_profile_kind': 'characteristic', 'margo_profile_characteristic': 'CpuCharacteristic'})
        self.save()
        self.assertEqual(native, generated_schema(path))

    def test_exactly_one_of_and_implicit_inlining(self):
        classes = self.model['classes']
        classes['UnionTarget'] = {'attributes': {
            'key': {'range': 'string', 'equals_string': 'example/union'},
            'properties': {'range': 'UnionContainer', 'inlined': True}}}
        classes['UnionContainer'] = {'attributes': {
            'choice': {'exactly_one_of': [{'range': 'UnionDisplay'}, {'range': 'string'}]}}}
        classes['UnionDisplay'] = {'title': 'Union display', 'rank': 25}
        usage = classes['HelmProfile']['slot_usage']['characteristics']
        usage['exactly_one_of'] = [{'range': 'UnionTarget'}]
        self.require('UnionTarget')
        section = next(x for x in self.sections() if x.target == 'UnionTarget')
        self.assertEqual((section.display, section.rank), ('UnionDisplay', 25))

    def test_duplicate_required_rank_fails(self):
        self.model['classes']['Novel'] = {'title': 'Novel', 'rank': 20, 'attributes': {'key': {'range': 'string', 'equals_string': 'example/novel'}}}
        self.require('Novel')
        with self.assertRaisesRegex(ValueError, 'Duplicate profile display or rank'):
            self.sections()

    def test_invalid_root_and_required_class_fail_closed(self):
        self.require('UnknownTarget')
        with self.assertRaisesRegex(ValueError, 'Missing has_member class'):
            self.sections()
        self.model['classes']['HelmProfile']['all_of'].pop()
        self.model['classes']['HelmProfile']['tree_root'] = False
        with self.assertRaisesRegex(ValueError, 'tree_root'):
            self.sections()

    def test_target_existing_examples_and_unknown(self):
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        for path in self.examples.glob('*.json'):
            target = path.name.split('-')[0]
            data = parse_data(path.read_text(), path.suffix)
            self.assertEqual(profile_target(view, data), target)
        self.assertEqual(profile_target(view, {'characteristics': []}), 'HelmProfile')
        for data in (None, {}, {'key': 1}):
            with self.assertRaises(ValueError):
                profile_target(view, data)
        for key in ('unknown/vendor', 'margo.org/resource/cpu\n'):
            with self.assertRaisesRegex(ValueError, 'matches 0'):
                profile_target(view, {'key': key})

    def test_target_subclass_and_mixin_exact_key(self):
        classes = self.model['classes']
        classes['KeyMixin'] = {'mixin': True, 'attributes': {'key': {'range': 'string', 'equals_string': 'example/mixin-key'}}}
        classes['MixinDerived'] = {'mixins': ['KeyMixin']}
        self.add_root('MixinDerived')
        self.require('MixinDerived')
        classes['DerivedDeployment'] = {'is_a': 'HelmDeploymentCharacteristic', 'slot_usage': {
            'key': {'equals_string': 'example/derived'}}}
        self.require('DerivedDeployment')
        self.save()
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        self.assertEqual(profile_target(view, {'key': 'example/mixin-key'}), 'MixinDerived')
        self.assertEqual(profile_target(view, {'key': 'example/derived'}), 'DerivedDeployment')

    def test_explicit_descendant_remains_ambiguous(self):
        self.model['classes']['DerivedCpu'] = {'is_a': 'CpuCharacteristic'}
        self.add_root('DerivedCpu')
        self.require('DerivedCpu')
        self.save()
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        cpu = self.examples / 'CpuCharacteristic-001.json'
        shutil.copy2(cpu, self.examples / 'DerivedCpu-test.json')
        with self.assertRaisesRegex(ValueError, 'Duplicate profile display or rank'):
            self.sections()
        with self.assertRaisesRegex(ValueError, 'matches 2'):
            profile_target(view, parse_data(cpu.read_text(), cpu.suffix))

    def test_mixin_descendant_fixture_does_not_create_section(self):
        self.model['classes']['DerivedCpu'] = {'mixins': ['CpuCharacteristic']}
        shutil.copy2(self.examples / 'CpuCharacteristic-001.json', self.examples / 'DerivedCpu-test.json')
        self.assertNotIn('DerivedCpu', [section.target for section in self.sections()])
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        data = parse_data((self.examples / 'DerivedCpu-test.json').read_text(), '.json')
        self.assertEqual(profile_target(view, data), 'CpuCharacteristic')

    def test_target_pattern_only_if_required(self):
        self.model['classes']['SpecificPattern'] = {'title': 'Specific', 'rank': 25, 'attributes': {
            'key': {'range': 'string', 'pattern': 'vendor/specific/.*'}}}
        self.add_root('SpecificPattern')
        self.require('SpecificPattern')
        self.save()
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        self.assertEqual(profile_target(view, {'key': 'vendor/specific/a'}), 'SpecificPattern')
        with self.assertRaisesRegex(ValueError, 'matches 0'):
            profile_target(view, {'key': 'vendor/open/one'})

    def test_required_key_overlap_fails_even_with_fixture_evidence(self):
        self.model['classes']['PatternTarget'] = {'title': 'Pattern', 'rank': 25, 'attributes': {
            'key': {'range': 'string', 'pattern': 'margo.org/resource/cpu'}}}
        self.add_root('PatternTarget')
        self.require('PatternTarget')
        self.save()
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        cpu = self.examples / 'CpuCharacteristic-001.json'
        data = parse_data(cpu.read_text(), cpu.suffix)
        with self.assertRaisesRegex(ValueError, 'matches 2'):
            profile_target(view, data)
        shutil.copy2(cpu, self.examples / 'PatternTarget-same.json')
        with self.assertRaisesRegex(ValueError, 'matches 2'):
            profile_target(view, data)

    def test_optional_pattern_fixture_cannot_override_exact_key(self):
        self.model['classes']['PatternTarget'] = {'title': 'Pattern', 'rank': 25, 'attributes': {
            'key': {'range': 'string', 'pattern': 'margo.org/resource/.*'}}}
        self.add_root('PatternTarget')
        self.save()
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        cpu = self.examples / 'CpuCharacteristic-001.json'
        data = parse_data(cpu.read_text(), cpu.suffix)
        cpu.rename(self.examples / 'PatternTarget-same.json')
        self.assertEqual(profile_target(view, data), 'CpuCharacteristic')

    def test_fixture_paths_do_not_drive_sections_or_targets(self):
        view = SchemaView(str(self.root / 'model/profile.yaml'))
        path = self.examples / 'Unknown-test.json'
        path.write_text('{}\n')
        self.assertEqual(len(profile_sections(view)), 6)
        with self.assertRaisesRegex(ValueError, 'matches 0'):
            profile_target(view, {'key': 'unknown'})

    def test_required_pattern_needs_display_and_example(self):
        classes = self.model['classes']
        classes['RequiredPattern'] = {'attributes': {'key': {'range': 'string', 'pattern': 'vendor/.*'}}}
        self.add_root('RequiredPattern')
        classes['HelmProfile']['all_of'].append({'slot_conditions': {
            'characteristics': {'has_member': {'range': 'RequiredPattern'}}}})
        with self.assertRaisesRegex(ValueError, 'Missing display.*RequiredPattern'):
            self.sections()
        classes['RequiredPattern'].update(title='Required pattern', rank=25)
        self.assertIn('RequiredPattern', [s.target for s in self.sections()])
        (self.examples / 'RequiredPattern-valid.json').write_text('{"key":"vendor/example"}\n')
        self.assertIn('RequiredPattern', [s.target for s in self.sections()])
        classes['HelmProfile']['all_of'][-1]['slot_conditions']['characteristics']['has_member']['pattern'] = 'x'
        with self.assertRaisesRegex(ValueError, 'Unsupported has_member range'):
            self.sections()


if __name__ == '__main__':
    unittest.main()
