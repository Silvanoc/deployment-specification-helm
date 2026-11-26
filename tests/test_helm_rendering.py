import shutil
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen
from linkml_runtime.utils.schemaview import SchemaView
from linkml_runtime.dumpers import yaml_dumper

from jinja2 import Environment, TemplateError, nodes
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import generate


class HelmRenderingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in ("model", "content", "tools/templates", "specification"):
            shutil.copytree(ROOT / directory, self.root / directory)
        for name in ("README.md", "COMPATIBILITY-ISSUES.md"):
            shutil.copy2(ROOT / name, self.root / name)
        model = self.root / "model/helm-profile.linkml.yaml"
        view = SchemaView(str(model))
        alias = 'margo-common:workload-target-characteristics.linkml'
        view.imports_closure()
        shared = copy.deepcopy(view.schema_map[alias])
        for cls in shared.classes.values():
            for key in ('margo_profile_example', 'margo_profile_kind', 'margo_profile_characteristic'):
                cls.annotations.pop(key, None)
        local = self.root / 'model/workload-target-local.yaml'
        yaml_dumper.dump(shared, to_file=str(local))
        model.write_text(model.read_text().replace(alias, str(local.with_suffix(''))))

    def render(self, name):
        return generate.render_page(name, self.root, self.root / "model" / f"helm-{name}.linkml.yaml")

    def test_authored_pages_match_original_bytes(self):
        for name, relative in generate.PAGES.items():
            with self.subTest(page=name):
                self.assertEqual(self.render(name).encode(), (ROOT / "specification" / relative).read_bytes())
        self.assertFalse(list(self.root.rglob("*-examples.json")))

    def test_rendered_page_is_linkml_index(self):
        original = generate.PageDocGenerator.serialize

        def marked_index(generator, directory=None):
            original(generator, directory=directory)
            index = Path(directory) / "index.md"
            index.write_bytes(index.read_bytes() + b"\nINDEX-MARKER\n")

        with patch.object(generate.PageDocGenerator, "serialize", marked_index):
            self.assertTrue(self.render("profile").endswith("\nINDEX-MARKER\n"))
            generate.generate(self.root)
            page = self.root / "build/site/specification" / generate.PAGES["profile"]
            self.assertTrue(page.read_text().endswith("\nINDEX-MARKER\n"))

    def test_integration_four_page_byte_parity(self):
        generate.generate(self.root, check=True)
        source = self.root / "specification"
        output = self.root / "build/site/specification"
        expected = {p.relative_to(source) for p in source.rglob("*.md")}
        actual = {p.relative_to(output) for p in output.rglob("*.md")}
        self.assertEqual(len(expected), 4)
        self.assertEqual(expected, actual)
        for path in expected:
            self.assertEqual((source / path).read_bytes(), (output / path).read_bytes())

    def test_authored_prose_and_fixture_changes_are_rendered(self):
        model = self.root / "model/helm-application.linkml.yaml"
        schema = yaml.safe_load(model.read_text())
        schema['classes']['HelmComponent']['slot_usage']['name'] = {'description': 'Changed model description.'}
        model.write_text(yaml.safe_dump(schema, sort_keys=False))
        source = self.root / 'content/application.md'
        old = "The `component.name` property MUST be used as the chart name when installing Helm charts."
        self.assertIn(old, source.read_text())
        self.assertIn(old, self.render('application'))
        self.assertNotIn('Changed model description.', self.render('application'))
        source.write_text(source.read_text().replace(old, 'Changed authored description.'))
        fixture = self.root / "model/examples/application/valid/ApplicationDescription-001.yaml"
        fixture.write_text(fixture.read_text() + "# changed fixture\n")
        rendered = self.render("application")
        self.assertIn("- Changed authored description.", rendered)
        self.assertIn("# changed fixture\n```", rendered)

    def test_templates_are_model_driven(self):
        for path in (self.root / "tools/templates").rglob("*.jinja2"):
            text = path.read_text()
            tree = Environment().parse(text)
            self.assertIn("schemaview.class_induced_slots(", text)
            self.assertIn("slot.description", text)
            for literal in tree.find_all(nodes.TemplateData):
                self.assertRegex(literal.data, r"^[\s|`-]*$")
        profile = (self.root / "content/profile.md").read_text()
        self.assertTrue(list(Environment().parse(profile).find_all(nodes.FromImport)))

    def test_profile_new_required_class_is_rendered(self):
        from common import parse_data
        from schema_validation import generated_schema, precise_validator
        model = self.root / "model/helm-profile.linkml.yaml"
        text = model.read_text()
        addition = """classes:
  Novel:
    title: Novel Display
    rank: 25
    attributes:
      key:
        range: string
        equals_string: example/novel
      value:
        range: string
        description: A novel value.
"""
        model.write_text(text.replace("classes:\n", addition, 1))
        schema = yaml.safe_load(model.read_text())
        profile = schema['classes']['HelmProfile']
        profile['slot_usage']['characteristics']['any_of'].append({'range': 'Novel'})
        profile['all_of'].append({'slot_conditions': {'characteristics': {'has_member': {'range': 'Novel'}}}})
        model.write_text(yaml.safe_dump(schema, sort_keys=False))
        fixture = self.root / "model/examples/profile/valid/Novel-custom.json"
        fixture.write_text('{"key": "example/novel", "value": "new"}\n')
        full = parse_data((fixture.parent / 'HelmProfile-001.json').read_text(), '.json')
        validator = precise_validator(generated_schema(model))
        self.assertFalse(validator.is_valid(full))
        full['characteristics'].append(parse_data(fixture.read_text(), '.json'))
        self.assertTrue(validator.is_valid(full))
        page = self.render("profile")
        self.assertIn("### Novel Display\n\nKey: `example/novel`", page)
        self.assertLess(page.index("### CPUs"), page.index("### Novel Display"))
        self.assertLess(page.index("### Novel Display"), page.index("### Memory"))
        self.assertIn("| value | string | N | A novel value. |", page)
        self.assertIn(fixture.read_text(), page)

    def test_optional_undocumented_characteristic_does_not_break_rendering(self):
        model = self.root / 'model/helm-profile.linkml.yaml'
        schema = yaml.safe_load(model.read_text())
        schema['classes']['OptionalTarget'] = {'attributes': {
            'key': {'range': 'string', 'equals_string': 'example/optional'}}}
        schema['classes']['HelmProfile']['slot_usage']['characteristics']['any_of'].append({'range': 'OptionalTarget'})
        model.write_text(yaml.safe_dump(schema, sort_keys=False))
        self.assertEqual(self.render('profile').encode(),
                         (ROOT / 'specification' / generate.PAGES['profile']).read_bytes())

    def test_subclass_fixture_does_not_change_profile_documentation(self):
        from check_examples import profile_target
        from common import parse_data
        model = self.root / 'model/helm-profile.linkml.yaml'
        schema = yaml.safe_load(model.read_text())
        schema['classes']['DerivedCpu'] = {'is_a': 'CpuCharacteristic'}
        model.write_text(yaml.safe_dump(schema, sort_keys=False))
        directory = self.root / 'model/examples/profile/valid'
        fixture = directory / 'CpuCharacteristic-001.json'
        shutil.copy2(fixture, directory / 'DerivedCpu-001.json')
        self.assertEqual(self.render('profile').encode(),
                         (ROOT / 'specification' / generate.PAGES['profile']).read_bytes())
        self.assertEqual(profile_target(parse_data(fixture.read_text(), '.json'), model), 'CpuCharacteristic')

    def test_required_pattern_key_is_rendered(self):
        model = self.root / 'model/helm-profile.linkml.yaml'
        schema = yaml.safe_load(model.read_text())
        schema['classes']['PatternTarget'] = {'title': 'Pattern Target', 'rank': 25, 'attributes': {
            'key': {'range': 'string', 'pattern': 'vendor/.*'}}}
        profile = schema['classes']['HelmProfile']
        profile['slot_usage']['characteristics']['any_of'].append({'range': 'PatternTarget'})
        profile['all_of'].append({'slot_conditions': {'characteristics': {'has_member': {'range': 'PatternTarget'}}}})
        model.write_text(yaml.safe_dump(schema, sort_keys=False))
        (self.root / 'model/examples/profile/valid/PatternTarget-example.json').write_text('{"key":"vendor/one"}\n')
        self.assertIn('### Pattern Target\n\nKey pattern: `vendor/.*`', self.render('profile'))

    def test_profile_discovery_without_example_annotations(self):
        model = self.root / 'model/helm-profile.linkml.yaml'
        view = SchemaView(str(model))
        self.assertTrue(view.get_class('Cpu'))
        for cls in view.all_classes().values():
            for key in ('margo_profile_example', 'margo_profile_kind', 'margo_profile_characteristic'):
                self.assertNotIn(key, cls.annotations)
        directory = self.root / 'model/examples/profile/valid'
        old = directory / 'CpuCharacteristic-001.json'
        renamed = old.with_name('CpuCharacteristic-z.json')
        old.rename(renamed)
        first = old.with_name('CpuCharacteristic-a.json')
        first.write_text(renamed.read_text().replace('"key":', '"key" :', 1))
        original = generate.ExampleRunner.example_source_inputs
        calls = []
        def spy(runner, cls=None):
            calls.append((runner.input_directory, runner.input_formats, cls))
            return original(runner, cls)
        with patch.object(generate.ExampleRunner, 'example_source_inputs', spy):
            page = self.render('profile')
        self.assertIn((directory, ['json'], 'CpuCharacteristic'), calls)
        self.assertIn(first.read_text(), page)
        self.assertNotIn(renamed.read_text(), page)
        first.unlink()
        self.assertIn(renamed.read_text(), self.render('profile'))
        renamed.unlink()
        with self.assertRaisesRegex(ValueError, 'No valid JSON profile example for CpuCharacteristic'):
            self.render('profile')
        first.symlink_to(self.root / 'README.md')
        with self.assertRaisesRegex(ValueError, 'unsafe example'):
            self.render('profile')

    def test_profile_discovery_rejects_symlink_before_glob(self):
        directory = self.root / 'model/examples/profile/valid'
        moved = directory.with_name('moved')
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        with patch.object(generate.ExampleRunner, 'example_source_inputs') as discovery:
            with self.assertRaisesRegex(ValueError, 'unsafe profile example directory'):
                self.render('profile')
            discovery.assert_not_called()

    def test_profile_discovery_in_workspace_with_glob_metacharacters(self):
        workspace = self.root / 'repo[1]'
        workspace.mkdir()
        for directory in ('model', 'content', 'tools/templates'):
            shutil.copytree(self.root / directory, workspace / directory)
        model = workspace / 'model/helm-profile.linkml.yaml'
        page = generate.render_page('profile', workspace, model)
        self.assertEqual(page.encode(), (ROOT / 'specification' / generate.PAGES['profile']).read_bytes())

    def test_profile_metadata_and_slot_mutations(self):
        model = self.root / "model/helm-profile.linkml.yaml"
        view = SchemaView(str(model))
        view.imports_closure()
        shared = next(s for s in view.schema_map.values() if 'Cpu' in s.classes)
        source = shared.source_file
        text = urlopen(source).read().decode('utf-8') if source.startswith(('https:', 'http:')) else Path(source).read_text()
        replacements = {
            "title: CPUs": "title: Processor",
            "rank: 20": "rank: 45",
            "equals_string: margo.org/resource/cpu": "equals_string: example/processor",
            "Defines how many cores the CPU has available,": "Changed core description,",
            "Indicates the peripherals available on the workload target.": "Changed peripheral description.",
        }
        for before, after in replacements.items():
            self.assertIn(before, text)
            text = text.replace(before, after)
        local = self.root / 'shared-profile.yaml'
        local.write_text(text)
        imported = next(i for i in view.schema.imports if 'workload-target-local' in i)
        model.write_text(model.read_text().replace(imported, str(local.with_suffix(''))))
        page = self.render("profile")
        self.assertIn("### Processor\n\nKey: `example/processor`", page)
        self.assertLess(page.index("### Storage"), page.index("### Processor"))
        self.assertIn("Changed core description,", page)
        self.assertIn("Changed peripheral description.", page)

    def test_deployment_properties_range_drives_rendering(self):
        model = self.root / 'model/helm-profile.linkml.yaml'
        schema = yaml.safe_load(model.read_text())
        schema['classes']['AlternateProperties'] = {'title': 'Alternate Label', 'description': 'Alternate note.'}
        schema['classes']['HelmDeploymentCharacteristic']['attributes']['properties']['range'] = 'AlternateProperties'
        model.write_text(yaml.safe_dump(schema, sort_keys=False))
        generate.validate_profile_metadata(model)
        page = self.render('profile')
        self.assertIn('### Alternate Label Properties Attributes', page)
        self.assertIn('> **Note:** Alternate note.', page)
        self.assertIn('### Alternate Label Example', page)

    def test_validate_profile_metadata_accepts_string_path(self):
        model = self.root / 'model/helm-profile.linkml.yaml'
        self.assertEqual(generate.validate_profile_metadata(str(model)),
                         generate.validate_profile_metadata(model))

    def test_invalid_deployment_properties_metadata_fails_closed(self):
        model = self.root / 'model/helm-profile.linkml.yaml'
        original = yaml.safe_load(model.read_text())
        for case in ('missing_slot', 'missing_range', 'multivalued', 'nonclass_range', 'missing_title', 'missing_description', 'blank_title'):
            with self.subTest(case=case):
                schema = yaml.safe_load(yaml.safe_dump(original))
                attrs = schema['classes']['HelmDeploymentCharacteristic']['attributes']
                props = schema['classes']['HelmDeploymentProperties']
                if case == 'missing_slot':
                    del attrs['properties']
                elif case == 'missing_range':
                    del attrs['properties']['range']
                elif case == 'multivalued':
                    attrs['properties']['multivalued'] = True
                elif case == 'nonclass_range':
                    attrs['properties']['range'] = 'string'
                elif case == 'missing_title':
                    del props['title']
                elif case == 'missing_description':
                    del props['description']
                else:
                    props['title'] = '   '
                model.write_text(yaml.safe_dump(schema, sort_keys=False))
                with self.assertRaises(ValueError):
                    generate.validate_profile_metadata(model)

    def test_standard_jinja_include_and_undefined_context(self):
        source = self.root / "content/application.md"
        original = source.read_text()
        extra = self.root / "content/extra.md"
        extra.write_text("Included authored text.")
        source.write_text(original + "\n{% include 'content/extra.md' %}\n")
        self.assertTrue(list(Environment().parse(source.read_text()).find_all(nodes.Include)))
        self.assertIn("Included authored text.", self.render("application"))
        source.write_text(original + "\n{{ missing_context }}\n")
        with self.assertRaises(TemplateError):
            self.render("application")

    def test_fixture_literals_are_not_evaluated(self):
        for name, relative, marker in (
            ("application", "application/valid/ApplicationDescription-001.yaml", "# {{ missing_context }}\n"),
            ("profile", "profile/valid/HelmProfile-001.json", '"{{ missing_context }}"'),
        ):
            with self.subTest(page=name):
                fixture = self.root / "model/examples" / relative
                original = fixture.read_text()
                fixture.write_text(original + marker if name == "application" else original.replace('"characteristics"', marker, 1))
                self.assertIn(marker.rstrip(), self.render(name))

    def test_bad_example_references_fail_closed(self):
        source = self.root / "content/application.md"
        original = source.read_text()
        ref = "application/ApplicationDescription-001.yaml"
        for bad in ("application/../bad.yaml", "unknown/ApplicationDescription-001.yaml",
                    "application/Unknown-001.yaml", "application/ApplicationDescription-999.yaml"):
            with self.subTest(ref=bad):
                source.write_text(original.replace(ref, bad))
                with self.assertRaises(ValueError):
                    self.render("application")
        for text in (original.replace("{{ example('" + ref + "') }}", ""),
                     original + "\n{{ example('" + ref + "') }}\n"):
            source.write_text(text)
            with self.assertRaises(ValueError):
                self.render("application")
        source.write_text(original)
        fixture = self.root / "model/examples/application/valid/ApplicationDescription-001.yaml"
        fixture.unlink()
        with self.assertRaises(ValueError):
            self.render("application")
        fixture.symlink_to(self.root / "README.md")
        with self.assertRaises(ValueError):
            self.render("application")

    def test_bad_model_references_fail_closed(self):
        source = self.root / "content/application.md"
        original = source.read_text()
        valid = "{{ schemaview.induced_slot('wait', 'HelmComponentProperties').description }}"
        source.write_text(original + "\n" + valid)
        self.assertIn('Whether to wait', self.render('application'))
        for extra in (valid + "\n" + valid,
                      "{{ schemaview.induced_slot('missing', 'HelmComponentProperties').description }}",
                      "{{ schemaview.induced_slot('wait', 'MissingClass').description }}"):
            with self.subTest(reference=extra):
                source.write_text(original + "\n" + extra)
                with self.assertRaises(ValueError):
                    self.render("application")
        source.write_text(original + "\n{{ missing_model.induced_slot('wait', 'HelmComponentProperties') }}")
        with self.assertRaises(TemplateError):
            self.render("application")

    def test_application_prose_requires_no_model_reference(self):
        source = (self.root / 'content/application.md').read_text()
        self.assertNotIn('schemaview.', source)
        self.assertNotIn('margo_helm_description', (self.root / 'model/helm-application.linkml.yaml').read_text())
        page = self.render('application')
        expected = (ROOT / 'specification/applications/application-description.md').read_text()
        for line in expected.splitlines():
            if line.startswith('- The `'):
                self.assertIn(line, source)
                self.assertIn(line, page)

    def test_unsafe_template_paths_and_fences_rejected(self):
        source = self.root / "content/application.md"
        original = source.read_text()
        for include in ("model/../README.md", "content/../README.md", "model/missing.jinja2"):
            source.write_text(original + "\n{% include '" + include + "' %}\n")
            with self.assertRaises(TemplateError):
                self.render("application")
        extra = self.root / "content/extra.md"
        extra.symlink_to(self.root / "README.md")
        source.write_text(original + "\n{% include 'content/extra.md' %}\n")
        with self.assertRaises(TemplateError):
            self.render("application")
        source.write_text(original + "\n~~~text\nunclosed\n")
        with self.assertRaises(ValueError):
            self.render("application")
        source.write_text(original)
        fixture = self.root / "model/examples/application/valid/ApplicationDescription-001.yaml"
        fixture.write_text(fixture.read_text() + "```\n")
        with self.assertRaises(ValueError):
            self.render("application")


if __name__ == "__main__":
    unittest.main()
