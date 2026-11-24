import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment, TemplateError, nodes

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

    def test_model_and_fixture_changes_are_rendered(self):
        model = self.root / "model/helm-application.linkml.yaml"
        text = model.read_text()
        old = "The `component.name` property MUST be used as the chart name when installing Helm charts."
        annotation = 'margo_helm_description: "' + old + '"'
        self.assertIn(annotation, text)
        model.write_text(text.replace(annotation, 'margo_helm_description: "Changed model description."'))
        fixture = self.root / "model/examples/application/valid/ApplicationDescription-001.yaml"
        fixture.write_text(fixture.read_text() + "# changed fixture\n")
        rendered = self.render("application")
        self.assertIn("- Changed model description.", rendered)
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

    def test_profile_new_annotated_class_is_rendered(self):
        model = self.root / "model/helm-profile.linkml.yaml"
        text = model.read_text()
        addition = """classes:
  Novel:
    title: Novel Display
    rank: 25
    annotations:
      margo_profile_kind: characteristic
      margo_profile_characteristic: Novel
      margo_profile_example: profile/Novel-001.json
    attributes:
      key:
        range: string
        equals_string: example/novel
      value:
        range: string
        description: A novel value.
"""
        model.write_text(text.replace("classes:\n", addition, 1))
        fixture = self.root / "model/examples/profile/valid/Novel-001.json"
        fixture.write_text('{"value": "new"}\n')
        page = self.render("profile")
        self.assertIn("### Novel Display\n\nKey: `example/novel`", page)
        self.assertLess(page.index("### CPUs"), page.index("### Novel Display"))
        self.assertLess(page.index("### Novel Display"), page.index("### Memory"))
        self.assertIn("| value | string | N | A novel value. |", page)
        self.assertIn(fixture.read_text(), page)

    def test_profile_metadata_and_slot_mutations(self):
        model = self.root / "model/helm-profile.linkml.yaml"
        text = model.read_text()
        replacements = {
            "title: CPUs\n    rank: 20": "title: Processor\n    rank: 45",
            "equals_string: margo.org/resource/cpu": "equals_string: example/processor",
            "Defines how many cores the CPU has available.": "Changed core description.",
            "Indicates the peripherals available on the workload target.": "Changed peripheral description.",
        }
        for before, after in replacements.items():
            self.assertIn(before, text)
            text = text.replace(before, after)
        model.write_text(text)
        page = self.render("profile")
        self.assertIn("### Processor\n\nKey: `example/processor`", page)
        self.assertLess(page.index("### Storage"), page.index("### Processor"))
        self.assertIn("Changed core description.", page)
        self.assertIn("Changed peripheral description.", page)

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
        for extra in ("{{ schemaview.induced_slot('wait', 'ComponentProperties').annotations['margo_helm_description'].value }}",
                      "{{ schemaview.induced_slot('missing', 'ComponentProperties').description }}",
                      "{{ schemaview.induced_slot('wait', 'MissingClass').description }}"):
            with self.subTest(reference=extra):
                source.write_text(original + "\n" + extra)
                with self.assertRaises(ValueError):
                    self.render("application")
        source.write_text(original.replace("schemaview.induced_slot", "missing_model.induced_slot"))
        with self.assertRaises(TemplateError):
            self.render("application")

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
