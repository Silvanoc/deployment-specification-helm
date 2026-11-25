"""Generate and validate the metadata-augmented LinkML JSON Schemas.

The x-margo keywords document constraints that JSON Schema cannot express;
semantic_errors enforces them on parsed values without a file loader.
"""
import json
import re
import subprocess
import tempfile
import math
import ipaddress
import unicodedata
from urllib.parse import urlsplit
from urllib.request import urlopen
from decimal import Decimal, InvalidOperation
from pathlib import Path

import jsonschema
import simplejson
from linkml.validator import Validator
from linkml.validator.plugins import JsonschemaValidationPlugin
from linkml.validator.report import Severity
from linkml_runtime.utils.schemaview import SchemaView
from common import parse_data, semantic_equal


def valid_oci_repository(value):
    """Check distribution-compatible repository URI syntax, without I/O."""
    if not isinstance(value, str) or any(c.isspace() or unicodedata.category(c) == 'Cc' for c in value):
        return False
    if not value.startswith('oci://') or '?' in value or '#' in value:
        return False
    try:
        parsed = urlsplit(value)
        authority = parsed.netloc
        if not authority or '@' in authority or not parsed.path.startswith('/'):
            return False
        if authority.startswith('['):
            end = authority.find(']')
            if end < 0 or '%' in authority[1:end]:
                return False
            ipaddress.IPv6Address(authority[1:end])
            suffix = authority[end + 1:]
            if suffix and not re.fullmatch(r':[0-9]+', suffix, flags=re.ASCII):
                return False
        else:
            host, separator, port = authority.partition(':')
            if separator and not re.fullmatch(r'[0-9]+', port, flags=re.ASCII):
                return False
            component = r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?'
            if not re.fullmatch(component + r'(?:\.' + component + r')*', host, flags=re.ASCII):
                return False
        segment = r'[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*'
        return bool(re.fullmatch(r'/' + segment + r'(?:/' + segment + r')*', parsed.path, flags=re.ASCII))
    except ValueError:
        return False


def disallow_null(prop, location):
    """Remove the generator's explicit optional null forms, or fail closed."""
    typ = prop.get('type')
    if isinstance(typ, list):
        if not typ or not all(isinstance(t, str) for t in typ):
            raise ValueError(f"Invalid nullable type at {location}")
        remaining = [t for t in typ if t != 'null']
        if not remaining:
            raise ValueError(f"No non-null type at {location}")
        prop['type'] = remaining[0] if len(remaining) == 1 else remaining
    elif isinstance(typ, str):
        if typ == 'null':
            raise ValueError(f"No non-null type at {location}")
    elif 'anyOf' in prop:
        branches = prop['anyOf']
        if not isinstance(branches, list) or not branches or not all(isinstance(b, dict) for b in branches):
            raise ValueError(f"Invalid nullable anyOf at {location}")
        kept = []
        for branch in branches:
            if branch.get('type') == 'null':
                continue
            disallow_null(branch, location)
            kept.append(branch)
        if not kept:
            raise ValueError(f"No non-null branch at {location}")
        prop['anyOf'] = kept
    elif '$ref' in prop and isinstance(prop['$ref'], str):
        # Restrict a reference even if the referenced schema allows null.
        prop.setdefault('allOf', []).append({'not': {'type': 'null'}})
    else:
        raise ValueError(f"Unsupported nullable property shape at {location}")


def _integer_type(checker, value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            return False
        _, digits, exponent = value.as_tuple()
        if value.is_zero() or exponent >= 0:
            return True
        return -exponent <= len(digits) and all(d == 0 for d in digits[exponent:])
    return jsonschema.Draft202012Validator.TYPE_CHECKER.is_type(value, "integer")


def _number_type(checker, value):
    if isinstance(value, Decimal):
        return value.is_finite()
    return jsonschema.Draft202012Validator.TYPE_CHECKER.is_type(value, "number")


def _decimal(value):
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal.from_float(value)
    return Decimal(value)


def _coefficient(value):
    _, digits, exponent = _decimal(value).as_tuple()
    if len(digits) > 4000:
        raise ValueError("Number exceeds safe multipleOf precision limit")
    coefficient = int(''.join(map(str, digits)))
    return coefficient, exponent


def _multiple_of(validator, divisor, instance, schema):
    if not validator.is_type(instance, "number"):
        return
    numerator, nexp = _coefficient(instance)
    denominator, dexp = _coefficient(divisor)
    if denominator == 0:
        raise ValueError("Invalid zero multipleOf")
    delta = nexp - dexp
    if not numerator:
        return
    if delta >= 0:
        valid = (numerator % denominator) * pow(10, delta, denominator) % denominator == 0
    elif -delta >= len(str(numerator)):
        valid = False
    else:
        valid = numerator % (denominator * 10 ** (-delta)) == 0
    if not valid:
        yield jsonschema.ValidationError(f"{instance!r} is not a multiple of {divisor!r}")


def precise_validator(schema):
    # Schema decimals also need exact arithmetic for keywords such as multipleOf.
    exact = json.loads(simplejson.dumps(schema, use_decimal=True), parse_float=Decimal)
    def reject_nested_dialects(node, root=True):
        if not isinstance(node, dict):
            return
        if not root and '$schema' in node:
            raise ValueError("Nested $schema dialect declarations are unsupported by the precise validator")
        for keyword in ('$defs', 'definitions', 'properties', 'patternProperties', 'dependentSchemas', 'dependencies'):
            mapping = node.get(keyword)
            if isinstance(mapping, dict):
                for child in mapping.values():
                    reject_nested_dialects(child, root=False)
        for keyword in ('allOf', 'anyOf', 'oneOf', 'prefixItems'):
            children = node.get(keyword)
            if isinstance(children, list):
                for child in children:
                    reject_nested_dialects(child, root=False)
        for keyword in ('additionalProperties', 'unevaluatedProperties', 'items', 'additionalItems', 'unevaluatedItems', 'not', 'if', 'then', 'else', 'propertyNames', 'contains', 'contentSchema'):
            child = node.get(keyword)
            if keyword == 'items' and isinstance(child, list):
                for item in child:
                    reject_nested_dialects(item, root=False)
            else:
                reject_nested_dialects(child, root=False)
    reject_nested_dialects(exact)
    base = jsonschema.validators.validator_for(exact)
    checker = base.TYPE_CHECKER.redefine("integer", _integer_type).redefine("number", _number_type)
    cls = jsonschema.validators.extend(base, validators={"multipleOf": _multiple_of}, type_checker=checker)
    return cls(exact, format_checker=cls.FORMAT_CHECKER)


class _PreciseContext:
    def __init__(self, context, validator):
        self._context = context
        self._validator = validator

    def __getattr__(self, name):
        return getattr(self._context, name)

    def json_schema_validator(self, **kwargs):
        return self._validator


class PreciseJsonschemaValidationPlugin(JsonschemaValidationPlugin):
    """Use LinkML's plugin/reporting with a locally extended type checker."""
    def __init__(self, schema, schema_path):
        super().__init__(json_schema_path=str(schema_path))
        self._precise_validator = precise_validator(schema)

    def process(self, instance, context):
        return super().process(instance, _PreciseContext(context, self._precise_validator))


def annotation(obj, name):
    annotations = getattr(obj, "annotations", None)
    if not annotations:
        return None
    try:
        entry = annotations[name]
    except (KeyError, TypeError, AttributeError):
        return None
    return None if entry is None else entry.value


def root_class(view):
    roots = [c.name for c in view.all_classes(imports=False).values() if c.tree_root]
    if not roots:
        roots = [c.name for c in view.all_classes().values() if c.tree_root]
    if len(roots) != 1:
        raise ValueError(f"Expected exactly one tree_root, got {roots}")
    return roots[0]


def reject_unverified_numeric_constraints(view):
    # LinkML 1.12.1 rounds some model values before emitting JSON Schema.
    # Until structural source-to-output verification is available, refuse
    # numeric constraints rather than silently publishing altered bounds.
    keys = {'minimum_value', 'maximum_value', 'equals_number', 'equals_number_in', 'multiple_of', 'minimum_cardinality', 'maximum_cardinality', 'exact_cardinality', 'minimum_number_dimensions', 'maximum_number_dimensions', 'exact_number_dimensions'}
    def scan(value, location):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in keys and child is not None:
                    raise ValueError(f"Cannot verify LinkML numeric constraint {key} at {location}; generator may round it")
                scan(child, f"{location}/{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                scan(child, f"{location}/{index}")
    for name in view.imports_closure():
        source = view.schema_map[name].source_file
        if not source:
            raise ValueError(f"Cannot verify numeric constraints in imported schema {name}: source unavailable")
        if urlsplit(source).scheme in ('http', 'https'):
            try:
                with urlopen(source, timeout=30) as response:
                    raw = response.read(10_000_001)
                if len(raw) > 10_000_000:
                    raise ValueError("schema source exceeds size limit")
                text = raw.decode('utf-8')
            except Exception as exc:
                raise ValueError(f"Cannot verify numeric constraints in imported schema {name}: {source}") from exc
        else:
            path = Path(source)
            if not path.is_file():
                raise ValueError(f"Cannot verify numeric constraints in imported schema {name}: source unavailable")
            text = path.read_text(encoding='utf-8')
        scan(parse_data(text, '.yaml'), str(source))


def generated_schema(schema_path, target=None):
    view = SchemaView(str(schema_path))
    reject_unverified_numeric_constraints(view)
    target = target or root_class(view)
    if view.get_class(target) is None:
        raise ValueError(f"Unknown target class: {target}")
    nonnull = annotation(view.schema, 'margo_optional_typed_nonnull')
    if nonnull is None:
        seen = set()
        for name in view.class_ancestors(target):
            owner_id = view.get_class(name).from_schema
            if owner_id in seen:
                continue
            seen.add(owner_id)
            owners = [s for s in view.schema_map.values() if s.id == owner_id]
            if not owners:
                raise ValueError(f"Cannot identify schema defining class {name}")
            policies = {annotation(s, 'margo_optional_typed_nonnull') for s in owners} - {None}
            if len(policies) > 1:
                raise ValueError(f"Conflicting margo_optional_typed_nonnull policies for {owner_id}")
            if policies:
                nonnull = next(iter(policies))
                break
    if nonnull is not None and nonnull not in ('true', 'false'):
        raise ValueError("Invalid margo_optional_typed_nonnull annotation: expected 'true' or 'false'")
    command = ["linkml", "generate", "json-schema", "--top-class", target, "--include-range-class-descendants", "--preserve-names"]
    if nonnull == 'true':
        command.append('--no-include-null')
    command.append(str(schema_path))
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    schema = json.loads(result.stdout, parse_float=Decimal)
    defs = schema.get("$defs")
    if not isinstance(defs, dict) or target not in defs:
        raise ValueError(f"Generated schema lacks definition for {target}")
    local_classes = view.all_classes(imports=False)
    for name, cls in view.all_classes().items():
        if name not in defs:
            if name in local_classes:
                raise ValueError(f"Generated schema lacks definition for {name}")
            continue
        nodes = [defs[name]] + ([schema] if name == target else [])
        patterns = {annotation(view.get_class(parent), "margo_extensions_pattern") for parent in view.class_ancestors(name)} - {None}
        if len(patterns) > 1:
            raise ValueError(f"Conflicting extension patterns for {name}")
        slots = view.class_induced_slots(name)
        extra = next((view.get_class(parent).extra_slots for parent in view.class_ancestors(name)
                      if view.get_class(parent).extra_slots is not None), None)
        typed = annotation(cls, "margo_typed_options")
        if typed is not None:
            typed = json.loads(typed)
            if not isinstance(typed, dict) or set(typed) != {'type_slot', 'options_slot', 'types'}:
                raise ValueError(f"Invalid typed options metadata on {name}")
            if not all(isinstance(typed[k], str) and typed[k] for k in ('type_slot', 'options_slot')) or typed['type_slot'] == typed['options_slot']:
                raise ValueError(f"Invalid typed options slot names on {name}")
            types = typed['types']
            if not isinstance(types, dict) or not types or not all(isinstance(k, str) and k and v in ('string', 'integer', 'number', 'boolean') for k, v in types.items()):
                raise ValueError(f"Invalid typed option types on {name}")
        selector = annotation(cls, "margo_selector_rules")
        if selector is not None:
            selector = json.loads(selector)
            groups = {"membership", "existence", "numeric", "object_array"}
            if not isinstance(selector, dict) or not selector or set(selector) - groups:
                raise ValueError(f"Invalid selector rule groups on {name}")
            operators = []
            for group, values in selector.items():
                if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
                    raise ValueError(f"Invalid selector operators for {name}.{group}")
                operators.extend(values)
            if len(operators) != len(set(operators)):
                raise ValueError(f"Duplicate selector operator in {name}")
        for node in nodes:
            props = node.get("properties", {})
            if not isinstance(props, dict):
                raise ValueError(f"Generated schema has no properties for {name}")
            if selector is not None:
                if not {"operator", "property", "values"} <= set(props):
                    raise ValueError(f"Missing selector properties for {name}")
                if "object_array" in selector and "itemSelector" not in props:
                    raise ValueError(f"Missing itemSelector for {name}")
                node["x-margo-selector-rules"] = selector
            if typed is not None:
                if typed['type_slot'] not in props or typed['options_slot'] not in props:
                    raise ValueError(f"Missing typed options properties for {name}")
                type_prop, options_prop = props[typed['type_slot']], props[typed['options_slot']]
                if not isinstance(type_prop, dict) or type_prop.get('type') != 'string' or not isinstance(options_prop, dict) or options_prop.get('type') != 'array':
                    raise ValueError(f"Unsupported typed options schema shape for {name}")
                node['x-margo-typed-options'] = typed
            if patterns:
                pattern = next(iter(patterns))
                re.compile(pattern)
                node.setdefault("patternProperties", {})[pattern] = {"type": "object"}
                props.pop("x_placeholder_extensions", None)
                if "required" in node:
                    node["required"] = [p for p in node["required"] if p != "x_placeholder_extensions"]
                if extra and extra.allowed:
                    node["additionalProperties"] = True
                    node.setdefault('propertyNames', {}).setdefault('allOf', []).append({
                        'if': {'pattern': r'^x[-_].*[-_]extensions(?![\s\S])'},
                        'then': {'pattern': pattern}})
                else:
                    node["additionalProperties"] = False
            if extra is not None:
                if extra.allowed and extra.range_expression and extra.range_expression.range:
                    range_name = extra.range_expression.range
                    if range_name != "string":
                        raise ValueError(f"Unsupported extra_slots range: {range_name}")
                    node["additionalProperties"] = {"type": "string"}
                else:
                    node["additionalProperties"] = bool(extra.allowed)
            for slot in slots:
                required = annotation(slot, "margo_required_characteristics")
                unique = annotation(slot, "margo_unique_by")
                equal = annotation(slot, "margo_equal_to")
                encoding = annotation(slot, "margo_oci_tag_encoding")
                nonnull = annotation(slot, "margo_disallow_null")
                oci = annotation(slot, "margo_oci_repository")
                if not any(x is not None for x in (required, unique, equal, encoding, nonnull, oci)):
                    continue
                if slot.name not in props:
                    raise ValueError(f"Missing generated property {name}.{slot.name}")
                prop = props[slot.name]
                if nonnull is not None:
                    if nonnull != 'true':
                        raise ValueError(f"Invalid disallow-null metadata on {name}.{slot.name}")
                    disallow_null(prop, f"{name}.{slot.name}")
                if oci is not None:
                    if oci != 'true':
                        raise ValueError(f"Invalid OCI repository metadata on {name}.{slot.name}")
                    if prop.get('type') != 'string':
                        raise ValueError(f"Unsupported OCI repository schema shape on {name}.{slot.name}")
                    prop['x-margo-oci-repository'] = True
                if required is not None:
                    keys = json.loads(required)
                    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys) or not unique:
                        raise ValueError(f"Invalid required characteristics metadata on {name}.{slot.name}")
                    prop.setdefault("allOf", []).extend({"contains": {"type": "object", "properties": {unique: {"const": key}}, "required": [unique]}} for key in keys)
                if unique is not None:
                    if not isinstance(unique, str) or not unique:
                        raise ValueError(f"Invalid unique-by metadata on {name}.{slot.name}")
                    prop["x-margo-unique-by"] = unique
                if equal is not None:
                    if not isinstance(equal, str) or equal not in props:
                        raise ValueError(f"Invalid equality metadata on {name}.{slot.name}")
                    prop["x-margo-equal-to"] = equal
                if encoding is not None:
                    if encoding != "semver_plus_as_underscore":
                        raise ValueError(f"Unsupported OCI tag encoding: {encoding}")
                    prop["x-margo-oci-tag-encoding"] = encoding
    # Generic profiles and components must not inherit constraints merely
    # because a specialized descendant happens to match their shape.
    if 'DeploymentProfile' in defs and 'Component' in defs and 'ComponentProperties' in defs:
        for node in [defs['DeploymentProfile']] + ([schema] if target == 'DeploymentProfile' else []):
            item = node['properties']['components']['items']
            item.pop('anyOf', None)
            item['$ref'] = '#/$defs/Component'
        for node in [defs['Component']] + ([schema] if target == 'Component' else []):
            prop = node['properties']['properties']
            prop.pop('anyOf', None)
            prop['$ref'] = '#/$defs/ComponentProperties'
    if view.get_class('DeploymentProfile') and view.get_class('ApplicationDescription'):
        registrations = {}
        for name, cls in view.all_classes().items():
            if name == 'DeploymentProfile' or 'DeploymentProfile' not in view.class_ancestors(name):
                continue
            key = cls.slot_usage.get('key') if cls.slot_usage else None
            key = key.equals_string if key else None
            if key is None:
                continue
            if key in registrations:
                raise ValueError(f"Duplicate deployment profile key {key}: {registrations[key]}, {name}")
            if name not in defs:
                raise ValueError(f"Generated schema lacks deployment profile definition for {name}")
            registrations[key] = name
        for name in view.class_descendants('ApplicationDescription'):
            if name not in defs:
                continue
            slots = {slot.name: slot for slot in view.class_induced_slots(name)}
            if 'deploymentProfiles' not in slots or slots['deploymentProfiles'].range != 'DeploymentProfile':
                continue
            for node in [defs[name]] + ([schema] if target == name else []):
                try:
                    item = node['properties']['deploymentProfiles']['items']
                except KeyError as exc:
                    raise ValueError(f"Generated schema lacks deploymentProfiles items for {name}") from exc
                item.clear()
                item['$ref'] = '#/$defs/DeploymentProfile'
                item['allOf'] = [
                    {'if': {'properties': {'key': {'const': key}}, 'required': ['key']},
                     'then': {'$ref': f'#/$defs/{subtype}'}}
                    for key, subtype in registrations.items()]
                item['x-margo-known-deployment-keys'] = list(registrations)
    jsonschema.validators.validator_for(schema).check_schema(schema)
    return schema


def semantic_errors(data, schema, tag=None, revision=None):
    errors = []
    notes = []
    saw_encoding = False
    validator = precise_validator(schema)

    def numeric(value, allow_string=False):
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        if isinstance(value, Decimal):
            return value.is_finite()
        if isinstance(value, float):
            return math.isfinite(value)
        if allow_string and isinstance(value, str) and re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value, flags=re.ASCII):
            try:
                return Decimal(value).is_finite()
            except InvalidOperation:
                return False
        return False

    def check_selector(value, rules, path):
        operator = value.get('operator')
        group = next((name for name, operators in rules.items() if operator in operators), None)
        if group is None:
            return
        if group in ('membership', 'numeric', 'object_array') and not isinstance(value.get('property'), str):
            errors.append(f"{path}: {operator} requires a property")
        values = value.get('values')
        if group == 'membership':
            if not isinstance(values, list) or not values:
                errors.append(f"{path}: {operator} requires nonempty values")
            elif not (all(isinstance(v, str) for v in values) or all(numeric(v) for v in values) or (len(values) == 1 and isinstance(values[0], bool))):
                errors.append(f"{path}: {operator} values must be homogeneous strings or numbers, or one boolean")
        elif group == 'numeric':
            if not isinstance(values, list) or len(values) != 1 or not numeric(values[0], allow_string=True):
                errors.append(f"{path}: {operator} requires exactly one finite number or numeric string")
        elif group == 'existence':
            if 'values' in value:
                errors.append(f"{path}: {operator} forbids values")
        elif group == 'object_array':
            if 'values' in value:
                errors.append(f"{path}: {operator} forbids values")
            selector = value.get('itemSelector')
            expressions = selector.get('matchExpressions') if isinstance(selector, dict) else None
            if not isinstance(expressions, list) or not expressions:
                errors.append(f"{path}: {operator} requires nonempty itemSelector.matchExpressions")

    def walk(value, node, path):
        nonlocal saw_encoding
        if not isinstance(node, dict):
            return
        if '$ref' in node:
            ref = node['$ref']
            if not ref.startswith('#/$defs/'):
                raise ValueError(f"Unsupported schema reference: {ref}")
            walk(value, schema['$defs'][ref[len('#/$defs/'):]], path)
        for branch in node.get('allOf', []):
            walk(value, branch, path)
        if 'if' in node:
            selected = 'then' if validator.evolve(schema=node['if']).is_valid(value) else 'else'
            if selected in node:
                walk(value, node[selected], path)
        for keyword in ('anyOf', 'oneOf'):
            for branch in node.get(keyword, []):
                if validator.evolve(schema=branch).is_valid(value):
                    walk(value, branch, path)
        if isinstance(value, dict):
            known = node.get('x-margo-known-deployment-keys')
            if known is not None and isinstance(value.get('key'), str) and value['key'] not in known:
                note = f"{path}: deployment key {value['key']!r} validated generically only"
                if note not in notes:
                    notes.append(note)
            if 'x-margo-selector-rules' in node:
                check_selector(value, node['x-margo-selector-rules'], path)
            typed = node.get('x-margo-typed-options')
            if typed:
                datatype = value.get(typed['type_slot'])
                options = value.get(typed['options_slot'])
                if isinstance(datatype, str) and datatype not in typed['types'] and isinstance(options, list):
                    note = f"{path}: option type {datatype!r} is not verified"
                    if note not in notes:
                        notes.append(note)
                elif isinstance(datatype, str) and datatype in typed['types'] and isinstance(options, list):
                    scalar_type = typed['types'][datatype]
                    for index, option in enumerate(options):
                        if not validator.is_type(option, scalar_type):
                            errors.append(f"{path}.{typed['options_slot']}[{index}] must be {scalar_type} for {datatype}")
            for name, prop in node.get('properties', {}).items():
                if name not in value:
                    continue
                if prop.get('x-margo-oci-repository') and not valid_oci_repository(value[name]):
                    errors.append(f"{path}.{name} must be a valid OCI repository URI without tag or digest")
                equal = prop.get('x-margo-equal-to')
                if equal and equal in value and value[name] != value[equal]:
                    errors.append(f"{path}.{name} must equal {equal}")
                encoding = prop.get('x-margo-oci-tag-encoding')
                if encoding:
                    saw_encoding = True
                    if encoding != 'semver_plus_as_underscore':
                        raise ValueError(f"Unsupported OCI tag encoding: {encoding}")
                    if isinstance(value[name], str):
                        expected = value[name].replace('+', '_')
                        for label, supplied in (('tag', tag), ('revision', revision)):
                            if supplied is not None and supplied != expected:
                                errors.append(f"{path}.{name}: {label} must be {expected!r}, got {supplied!r}")
                walk(value[name], prop, f"{path}.{name}")
            properties = node.get('properties', {})
            patterns = node.get('patternProperties', {})
            for name, child in value.items():
                matched = False
                for pattern, prop in patterns.items():
                    if re.search(pattern, name):
                        matched = True
                        walk(child, prop, f"{path}.{name}")
                if name not in properties and not matched and isinstance(node.get('additionalProperties'), dict):
                    walk(child, node['additionalProperties'], f"{path}.{name}")
        elif isinstance(value, list):
            unique = node.get('x-margo-unique-by')
            if unique:
                strings = set()
                other = []
                for item in value:
                    if isinstance(item, dict) and unique in item:
                        key = item[unique]
                        if isinstance(key, str):
                            duplicate = key in strings
                            strings.add(key)
                        else:
                            duplicate = any(semantic_equal(key, previous) for previous in other)
                            other.append(key)
                        if duplicate:
                            errors.append(f"{path}: duplicate {unique} {item[unique]!r}")
            item_schema = node.get('items')
            if isinstance(item_schema, dict):
                for i, item in enumerate(value):
                    walk(item, item_schema, f"{path}[{i}]")
    walk(data, schema, '$')
    if saw_encoding and (tag is None or revision is None):
        missing = ', '.join(name for name, value in (('tag', tag), ('revision', revision)) if value is None)
        notes.append(f"External OCI comparison not performed for: {missing}")
    elif not saw_encoding and (tag is not None or revision is not None):
        notes.append("External OCI comparison not performed: no encoded version was present")
    return errors, notes


class ValidationEngine:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='margo-validation-')
        self.cache = {}

    def close(self):
        self.temp.cleanup()

    def validate(self, schema_path, target, data, tag=None, revision=None):
        key = (str(Path(schema_path).resolve()), target)
        if key not in self.cache:
            schema = generated_schema(schema_path, target)
            path = Path(self.temp.name) / f"schema-{len(self.cache)}.json"
            path.write_text(simplejson.dumps(schema, use_decimal=True), encoding='utf-8')
            validator = Validator(str(schema_path), validation_plugins=[PreciseJsonschemaValidationPlugin(schema, path)])
            self.cache[key] = (schema, validator)
        schema, validator = self.cache[key]
        report = validator.validate(data, target_class=target)
        errors = [str(r.message) for r in report.results if r.severity in (Severity.ERROR, Severity.FATAL)]
        semantic, notes = semantic_errors(data, schema, tag, revision)
        return errors + semantic, notes
