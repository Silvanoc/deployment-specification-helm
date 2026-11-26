"""Discover profile documentation sections from native LinkML structure."""

import re
from copy import copy
from dataclasses import dataclass

from linkml_runtime.dumpers import json_dumper

from schema_validation import required_memberships, root_class


DEPLOYMENT_SUBSET = "DeploymentTechnologyCharacteristics"


@dataclass(frozen=True)
class ProfileSection:
    target: str
    display: str
    kind: str
    rank: int


def _check_subset(view):
    # Force the import closure to load before inspecting every schema definition.
    view.imports_closure()
    definitions = []
    for schema in view.schema_map.values():
        subset = schema.subsets.get(DEPLOYMENT_SUBSET)
        if subset is not None:
            definition = json_dumper.to_dict(subset)
            for provenance in ("from_schema", "source_file"):
                definition.pop(provenance, None)
            definitions.append(definition)
    if not definitions:
        raise ValueError(f"Missing profile subset: {DEPLOYMENT_SUBSET}")
    if any(item != definitions[0] for item in definitions[1:]):
        raise ValueError(f"Conflicting profile subset definitions: {DEPLOYMENT_SUBSET}")


def profile_display(view, target):
    """Return a target's unique native display class name, or None."""
    cls = view.get_class(target)
    if not cls:
        raise ValueError(f"Unknown profile characteristic class: {target}")
    if cls.title and cls.rank is not None:
        return target
    slots = {slot.name: slot for slot in view.class_induced_slots(target)}
    properties = slots.get("properties")
    if not properties:
        return None
    ranges = [name for name in view.slot_range_as_union(properties) if name]
    if any(not view.get_class(name) and not view.get_type(name) and not view.get_enum(name) for name in ranges):
        raise ValueError(f"Unknown properties class on {target}: {ranges}")
    visited = set()
    found = set()
    pending = [name for name in ranges if view.get_class(name)]
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        cls = view.get_class(name)
        if not cls:
            raise ValueError(f"Unknown inlined class {name} on {target}")
        if cls.title and cls.rank is not None:
            found.add(name)
        for slot in view.class_induced_slots(name):
            for range_name in view.slot_range_as_union(slot):
                if not range_name:
                    continue
                if view.get_class(range_name):
                    # is_inlined examines a single range; project each union branch
                    # while retaining the induced slot's native metadata.
                    branch = copy(slot)
                    branch.range = range_name
                    if view.is_inlined(branch):
                        pending.append(range_name)
                elif not view.get_type(range_name) and not view.get_enum(range_name):
                    raise ValueError(f"Unknown range {range_name} on {name}.{slot.name}")
    if len(found) > 1:
        raise ValueError(f"Ambiguous display classes for {target}: {sorted(found)}")
    return next(iter(found), None)


def _required_targets(view, root):
    slots = {slot.name: slot for slot in view.class_induced_slots(root)}
    characteristics = slots.get("characteristics")
    if characteristics and characteristics.has_member is not None:
        raise ValueError(f"Slot-level has_member on {root}.characteristics is unsupported for profile documentation")
    return sorted({member for slot, member in required_memberships(view, root, view.all_classes())
                   if slot == "characteristics"})


def profile_sections(view):
    """Return ordered documentation sections for required characteristics only."""
    _check_subset(view)
    root = root_class(view)
    sections = []
    displays = set()
    ranks = set()
    for target in _required_targets(view, root):
        if target == root or not view.get_class(target):
            raise ValueError(f"Invalid required profile characteristic: {target}")
        slots = {slot.name: slot for slot in view.class_induced_slots(target)}
        key = slots.get("key")
        if not key or (key.equals_string is None and key.pattern is None):
            raise ValueError(f"Missing key constraint for required characteristic {target}")
        display = profile_display(view, target)
        if display is None:
            raise ValueError(f"Missing display class for modeled characteristic {target}")
        cls = view.get_class(display)
        if not isinstance(cls.rank, int) or isinstance(cls.rank, bool):
            raise ValueError(f"Invalid display rank for {display}")
        if display in displays or cls.rank in ranks:
            raise ValueError(f"Duplicate profile display or rank: {display} ({cls.rank})")
        displays.add(display)
        ranks.add(cls.rank)
        ancestors = view.class_ancestors(target, mixins=True, detect_cycles=True)
        deployment = any(DEPLOYMENT_SUBSET in (view.get_class(name).in_subset or []) for name in ancestors)
        sections.append(ProfileSection(target, display, "deployment" if deployment else "characteristic", cls.rank))
    return tuple(sorted(sections, key=lambda section: section.rank))


def profile_target(view, data):
    """Identify a documented profile example using required native key constraints."""
    if not isinstance(data, dict):
        raise ValueError("Profile example must be an object")
    root = root_class(view)
    if "characteristics" in data:
        return root
    key = data.get("key")
    if not isinstance(key, str):
        raise ValueError("Profile characteristic must have a string key")
    matches = []
    for name in _required_targets(view, root):
        slots = {slot.name: slot for slot in view.class_induced_slots(name)}
        slot = slots.get("key")
        if not slot or (slot.equals_string is None and slot.pattern is None):
            raise ValueError(f"Missing key constraint for required characteristic {name}")
        if ((slot.equals_string is None or slot.equals_string == key)
                and (slot.pattern is None or re.fullmatch(slot.pattern, key))):
            matches.append(name)
    if len(matches) != 1:
        raise ValueError(f"Profile key {key!r} matches {len(matches)} characteristic classes")
    return matches[0]
