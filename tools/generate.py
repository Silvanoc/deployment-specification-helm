"""Render the supplemental specification from LinkML and fixed templates."""
import argparse
import glob
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from common import ROOT
from schema_validation import generated_schema
from profile_structure import profile_sections
import simplejson
from linkml_runtime.utils.schemaview import SchemaView
from linkml.generators.docgen import DocGenerator
from linkml.workspaces.example_runner import ExampleRunner
from jinja2 import Environment, FileSystemLoader, PrefixLoader, StrictUndefined, TemplateError, TemplateNotFound

PAGES = {
    "application": "applications/application-description.md",
    "profile": "margo-management-interface/helm-profile-characteristics.md",
}
PROSE_PAGES = (
    "observability/collecting-workload-observability-data.md",
    "margo-workload-targets/workload-target-requirements.md",
)
class SafeLoader(FileSystemLoader):
    """A Jinja loader confined to one non-symlinked source directory."""

    def get_source(self, environment, template):
        parts = template.split("/")
        if any(part in ("", ".", "..") or "\\" in part for part in parts):
            raise TemplateNotFound(template)
        base = Path(self.searchpath[0])
        path = base
        if path.is_symlink():
            raise TemplateNotFound(template)
        for part in parts:
            path = path / part
            if path.is_symlink():
                raise TemplateNotFound(template)
        return super().get_source(environment, template)


def check_fences(text):
    """Reject unclosed Markdown fences without interpreting fixture contents as Jinja."""
    fence = None
    for line in text.splitlines():
        if fence is None:
            match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if match:
                fence = match.group(1)
        elif re.fullmatch(r" {0,3}" + re.escape(fence[0]) + r"{" + str(len(fence)) + r",}[ \t]*", line):
            fence = None
    if fence:
        raise ValueError("Unclosed Markdown code fence")


def index_template(name, root, view, generator):
    """Provide LinkML's index renderer with confined Markdown and model access."""
    seen_examples = set()
    seen_models = set()
    groups = set()

    class ModelView:
        def __getattr__(self, attr):
            return getattr(view, attr)

        def induced_slot(self, slot, cls):
            key = ("slot", cls, slot)
            if key in seen_models or not view.get_class(cls):
                raise ValueError(f"Invalid or duplicate model reference: {cls}.{slot}")
            slots = {item.name: item for item in view.class_induced_slots(cls)}
            if slot not in slots:
                raise ValueError(f"Unknown induced slot: {cls}.{slot}")
            seen_models.add(key)
            return slots[slot]

        def class_induced_slots(self, cls):
            key = ("class", cls)
            if key in seen_models or not view.get_class(cls):
                raise ValueError(f"Invalid or duplicate model reference: {cls}")
            seen_models.add(key)
            return view.class_induced_slots(cls)

    def example(ref):
        if not isinstance(ref, str):
            raise ValueError("Invalid example reference")
        match = re.fullmatch(r"([a-z]+)/([A-Za-z_][A-Za-z0-9_]*)-[A-Za-z0-9][A-Za-z0-9_-]*\.(json|yaml|yml)", ref)
        if not match or match.group(1) not in PAGES or ref in seen_examples:
            raise ValueError(f"Invalid or duplicate example reference: {ref}")
        group, cls, _ = match.groups()
        fixture_schema = root / "model" / f"helm-{group}.linkml.yaml"
        if fixture_schema.parent.is_symlink() or fixture_schema.is_symlink() or not fixture_schema.is_file():
            raise ValueError(f"Missing or unsafe schema: {fixture_schema}")
        if not SchemaView(str(fixture_schema)).get_class(cls):
            raise ValueError(f"Unknown example class: {ref}")
        path = root / "model" / "examples" / group / "valid" / ref.split("/")[1]
        if any(part.is_symlink() for part in (root / "model", root / "model/examples", path.parent.parent, path.parent, path)) or not path.is_file():
            raise ValueError(f"Missing or unsafe example: {path}")
        block = path.read_bytes().decode("utf-8")
        if not block.endswith("\n") or any(re.match(r"^ {0,3}(?:`{3,}|~{3,})[ \t]*$", line) for line in block.splitlines()):
            raise ValueError(f"Unsafe example contents: {path}")
        seen_examples.add(ref)
        return block[:-1]

    def require_group(group):
        if name != "profile" or group not in ("deployment", "characteristic") or group in groups:
            raise ValueError(f"Invalid or duplicate profile group: {group}")
        groups.add(group)
        return ""

    def profile_example(cls):
        if not isinstance(cls, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cls) or not view.get_class(cls):
            raise ValueError(f"Invalid or unknown profile example class: {cls}")
        directory = root / "model/examples/profile/valid"
        for component in (root, root / "model", root / "model/examples", directory.parent, directory):
            if component.is_symlink() or not component.is_dir():
                raise ValueError(f"Missing or unsafe profile example directory: {component}")
        runner = ExampleRunner(input_directory=Path(glob.escape(str(directory))), input_formats=["json"])
        matches = sorted(runner.example_source_inputs(cls))
        if not matches:
            raise ValueError(f"No valid JSON profile example for {cls} in {directory}; add {cls}-<name>.json")
        return example(f"profile/{Path(matches[0]).name}")

    loader = PrefixLoader({"content": SafeLoader(str(root / "content")),
                           "model": SafeLoader(str(root / "tools/templates"))})
    env = Environment(loader=loader, undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
    generator.customize_environment(env)
    env.globals.update(example=example, profile_example=profile_example, require_group=require_group)
    if name == "profile":
        env.globals["profile_sections"] = validate_profile_metadata(root / "model/helm-profile.linkml.yaml", view=view)
    template = env.get_template(f"content/{name}.md")

    class CheckedIndex:
        def render(self, **context):
            context["schemaview"] = ModelView()
            result = template.render(**context)
            if not seen_examples or (name == "profile" and groups != {"deployment", "characteristic"}):
                raise ValueError(f"Missing example references or profile groups in {name}")
            check_fences(result)
            return result

    return CheckedIndex()


class PageDocGenerator(DocGenerator):
    """Use the authored Markdown as LinkML's index template."""

    def _get_template(self, element_type):
        if element_type == "index":
            return index_template(self.page_name, self.page_root, self.schemaview, self)
        return super()._get_template(element_type)


def render_page(name, root, schema, directory=None):
    """Return the actual index emitted by LinkML's documentation generator."""
    if directory is None:
        with tempfile.TemporaryDirectory(prefix="linkml-page-") as temp:
            return render_page(name, root, schema, Path(temp))
    generator = PageDocGenerator(str(schema), preserve_names=True,
                                 template_directory=str(root / "tools/templates" / name))
    generator.page_name = name
    generator.page_root = root
    generator.serialize(directory=str(directory))
    index = directory / "index.md"
    if index.is_symlink() or not index.is_file():
        raise ValueError(f"Missing or unsafe LinkML index: {index}")
    return index.read_bytes().decode("utf-8")


def validate_profile_metadata(schema, view=None):
    """Reject incomplete display metadata before rendering the profile index."""
    schema = Path(schema)
    view = view or SchemaView(str(schema))
    sections = profile_sections(view)
    kinds = set()
    for section in sections:
        target = section.target
        kinds.add(section.kind)
        slots = {slot.name: slot for slot in view.class_induced_slots(target)}
        if "key" not in slots or not (slots["key"].equals_string or slots["key"].pattern):
            raise ValueError(f"Missing characteristic key: {target}")
        if section.kind == "deployment":
            cls = view.get_class(target)
            if not cls.title or cls.rank is None or section.display != target:
                raise ValueError(f"Invalid profile display metadata: {target}")
            properties = slots.get('properties')
            range_name = properties.range if properties else None
            if not properties or properties.multivalued or not isinstance(range_name, str) or not range_name.strip():
                raise ValueError(f"Missing properties range: {target}")
            properties_class = view.get_class(range_name)
            if not properties_class:
                raise ValueError(f"Unknown properties class: {range_name}")
            if not all(isinstance(value, str) and value.strip() for value in (properties_class.title, properties_class.description)):
                raise ValueError(f"Missing properties display metadata: {range_name}")
    if kinds != {"deployment", "characteristic"}:
        raise ValueError("Missing profile display groups")
    return sections


def generate(root=ROOT, check=False):
    schemas = {name: root / "model" / f"helm-{name}.linkml.yaml" for name in PAGES}
    for schema in schemas.values():
        if schema.parent.is_symlink() or schema.is_symlink() or not schema.is_file():
            raise ValueError(f"Missing or unsafe schema: {schema}")
    build = root / "build"
    if build.is_symlink() or (build.exists() and any(p.is_symlink() for p in build.rglob("*"))):
        raise ValueError("Symlinks in build/ are not safe generation targets")
    source = root / "specification"
    if source.is_symlink() or any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("Symlinks in specification/ cannot be copied safely")
    report = root / "COMPATIBILITY-ISSUES.md"
    if report.is_symlink() or not report.is_file():
        raise ValueError("COMPATIBILITY-ISSUES.md must be a regular file, not a symlink")
    readme = root / "README.md"
    if readme.is_symlink() or not readme.is_file():
        raise ValueError("README.md must be a regular file, not a symlink")
    site = build / "site"
    generated = site / "specification"
    artifacts = build / "artifacts/json-schemas"
    # data-model was owned by an earlier generator; remove its old class pages.
    for owned in (generated, artifacts, site / "data-model"):
        if owned.exists():
            if not owned.is_dir():
                raise ValueError(f"Expected a generated directory: {owned}")
            shutil.rmtree(owned)
    generated.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(readme, site / "README.md")
    shutil.copy2(report, site / report.name)
    # Only prose-only pages are copied. Generated pages always come from models.
    for relative in PROSE_PAGES:
        src = source / relative
        dst = generated / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    with tempfile.TemporaryDirectory(prefix="linkml-", dir=build) as temp:
        for name, relative in PAGES.items():
            schema = schemas[name]
            intermediate = Path(temp) / name
            intermediate.mkdir()
            content = root / "content" / f"{name}.md"
            template_dir = root / "tools" / "templates" / name
            template = template_dir / "class.md.jinja2"
            if (content.parent.is_symlink() or content.is_symlink() or not content.is_file()
                    or (root / "tools").is_symlink() or template_dir.parent.is_symlink() or template_dir.is_symlink()
                    or not template_dir.is_dir() or template.is_symlink() or not template.is_file()
                    or set(template_dir.iterdir()) != ({template, template_dir / "index.md.jinja2"} if name == "profile" else {template})
                    or (name == "profile" and ((template_dir / "index.md.jinja2").is_symlink() or not (template_dir / "index.md.jinja2").is_file()))):
                raise ValueError(f"Missing or unsafe content/template inputs for {name}")
            if name == "profile":
                validate_profile_metadata(schema)
            dest = generated / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(render_page(name, root, schema, intermediate), encoding="utf-8")
            artifact = generated_schema(schema)
            (artifacts / f"{name}.schema.json").write_text(simplejson.dumps(artifact, indent=2, ensure_ascii=False, use_decimal=True) + "\n", encoding="utf-8")
    if check:
        expected = {p.relative_to(source) for p in source.rglob("*.md")}
        actual = {p.relative_to(generated) for p in generated.rglob("*.md")}
        if expected != actual:
            raise ValueError(f"Markdown page set differs: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
        different = sorted(str(p) for p in expected if (source / p).read_bytes() != (generated / p).read_bytes())
        if different:
            raise ValueError(f"Generated Markdown differs: {', '.join(different)}")
    print("Generated supplemental specification" + (" (byte check passed)" if check else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="compare generated Markdown to checked-in pages")
    args = parser.parse_args()
    try:
        generate(check=args.check)
    except (ValueError, OSError, subprocess.CalledProcessError, TemplateError) as error:
        print(f"Generation failed: {error}", file=sys.stderr)
        sys.exit(1)
