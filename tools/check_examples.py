"""Check documented examples, valid fixtures, and class-specific counterexamples."""
import re
import sys
from pathlib import Path

from linkml_runtime.utils.schemaview import SchemaView

from common import ROOT, markdown_fences, parse_data, semantic_equal
from schema_validation import ValidationEngine
from profile_structure import profile_target as native_profile_target

GROUPS = ("application", "profile")
DOCUMENTS = (
    ("applications/application-description.md", {"yaml", "yml"}, "application", ".yaml", "ApplicationDescription"),
    ("margo-management-interface/helm-profile-characteristics.md", {"json"}, "profile", ".json", None),
)


def profile_target(data, schema):
    schema = Path(schema)
    return native_profile_target(SchemaView(str(schema)), data)


def validate(schema, target, fixture, negative=False, engine=None):
    data = parse_data(fixture.read_text(encoding="utf-8"), fixture.suffix)
    errors, notes = engine.validate(schema, target, data)
    if negative:
        if not errors:
            raise ValueError(f"Expected validation failure for {fixture}")
    elif errors:
        raise ValueError(f"Validation failed for {fixture}: {'; '.join(errors)}")
    elif notes:
        print(f"{fixture}: {'; '.join(notes)}")


def check(root=ROOT):
    engine = ValidationEngine()
    try:
        return _check(root, engine)
    finally:
        engine.close()


def _check(root, engine):
    valid_data = {}
    for group in GROUPS:
        schema = root / "model" / f"helm-{group}.linkml.yaml"
        if schema.parent.is_symlink() or schema.is_symlink() or not schema.is_file():
            raise ValueError(f"Missing or unsafe schema: {schema}")
        view = SchemaView(str(schema))
        targets = {}
        valid_data[group] = {}
        for kind in ("valid", "invalid"):
            directory = root / "model/examples" / group / kind
            if any(p.is_symlink() for p in (root / "model", root / "model/examples", directory.parent, directory)) or not directory.is_dir():
                raise ValueError(f"Missing or unsafe fixture directory: {directory}")
            fixtures = sorted(directory.iterdir())
            if not fixtures:
                raise ValueError(f"No {kind} examples in {directory}")
            for fixture in fixtures:
                match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)-([A-Za-z0-9][A-Za-z0-9_-]*)\.(json|yaml|yml)", fixture.name)
                if not match or fixture.is_symlink() or not fixture.is_file():
                    raise ValueError(f"Invalid fixture path: {fixture}")
                target = match.group(1)
                if not view.get_class(target):
                    raise ValueError(f"Unknown fixture target: {group}/{target}")
                data = parse_data(fixture.read_text(encoding="utf-8"), fixture.suffix)
                validate(schema, target, fixture, negative=kind == "invalid", engine=engine)
                targets.setdefault(target, set()).add(kind)
                if kind == "valid":
                    valid_data[group].setdefault(target, []).append(data)
        for target, kinds in targets.items():
            if kinds != {"valid", "invalid"}:
                raise ValueError(f"Both positive and negative examples required for {group}/{target}")
        print(f"Checked {group}")
    for path, languages, group, kind, target in DOCUMENTS:
        document = root / "specification" / path
        if any(p.is_symlink() for p in (root / "specification", document.parent, document)) or not document.is_file():
            raise ValueError(f"Missing or unsafe document: {document}")
        fences = markdown_fences(document.read_text(encoding="utf-8"), languages)
        if not fences:
            raise ValueError(f"No {languages} examples in {document}")
        for fence in fences:
            data = parse_data(fence, kind)
            intended = target or profile_target(data, root / "model/helm-profile.linkml.yaml")
            if not any(semantic_equal(data, fixture) for fixture in valid_data[group].get(intended, [])):
                raise ValueError(f"Example drift in {document} ({group}/{intended})")
            errors, notes = engine.validate(root / "model" / f"helm-{group}.linkml.yaml", intended, data)
            if errors:
                raise ValueError(f"Documented example invalid: {'; '.join(errors)}")
            for note in notes:
                print(f"{document}: {note}")


if __name__ == "__main__":
    try:
        check()
    except (ValueError, OSError, TypeError) as error:
        print(f"Example check failed: {error}", file=sys.stderr)
        sys.exit(1)
