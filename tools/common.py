"""Shared parsing of fixtures and Markdown fences."""
import json
import re
import math
from decimal import Decimal, localcontext
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


class StrictLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self._checked_mappings = set()

    def flatten_mapping(self, node):
        # Check keys before PyYAML expands merge aliases; merged keys are
        # defaults; delegate anchor precedence and explicit overrides to PyYAML.
        if id(node) not in self._checked_mappings:
            self._checked_mappings.add(id(node))
            keys = set()
            merge_seen = False
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    if merge_seen:
                        raise ValueError("Duplicate YAML merge key")
                    merge_seen = True
                    continue
                key = self.construct_object(key_node, deep=True)
                if key in keys:
                    raise ValueError(f"Duplicate YAML key: {key!r}")
                keys.add(key)
        super().flatten_mapping(node)

    def construct_yaml_float(self, node):
        value = self.construct_scalar(node).replace('_', '').lower()
        if value.lstrip('+-') in ('.inf', '.nan'):
            raise ValueError("Non-finite numbers are not supported")
        if ':' not in value:
            return Decimal(value)
        negative = value.startswith('-')
        parts = value.lstrip('+-').split(':')
        # Decimal's normal arithmetic context is bounded; widen it enough to
        # compute YAML 1.1 sexagesimal values without rounding.
        with localcontext() as context:
            context.prec = len(value) + 2 * len(parts) + 4
            result = Decimal(0)
            for part in parts:
                result = result * 60 + Decimal(part)
        return result.copy_negate() if negative else result

    def construct_yaml_int(self, node):
        value = self.construct_scalar(node).replace('_', '')
        negative = value.startswith('-')
        unsigned = value.lstrip('+-')
        if unsigned.startswith(('0b', '0x')):
            result = Decimal(int(unsigned[2:], 2 if unsigned.startswith('0b') else 16))
        elif len(unsigned) > 1 and unsigned.startswith('0'):
            result = Decimal(int(unsigned, 8))
        elif ':' in unsigned:
            parts = unsigned.split(':')
            with localcontext() as context:
                context.prec = len(value) + 2 * len(parts) + 4
                result = Decimal(0)
                for part in parts:
                    result = result * 60 + Decimal(part)
        else:
            result = Decimal(unsigned)
        return result.copy_negate() if negative else result


StrictLoader.add_constructor('tag:yaml.org,2002:float', StrictLoader.construct_yaml_float)
StrictLoader.add_constructor('tag:yaml.org,2002:int', StrictLoader.construct_yaml_int)
# PyYAML's YAML 1.1 float resolver misses JSON exponent forms such as 1e1.
# Extend only this loader, after its existing resolvers, without changing
# implicit typing for other YAML scalars or for SafeLoader users.
StrictLoader.add_implicit_resolver(
    'tag:yaml.org,2002:float',
    re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?[eE][+-]?[0-9]+\Z'),
    list('-0123456789'),
)


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite numbers are not supported")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def parse_data(text, suffix):
    if suffix == ".json":
        value = json.loads(text, parse_int=Decimal, parse_float=Decimal, object_pairs_hook=_json_pairs, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"Invalid JSON constant: {v}")))
    elif suffix in (".yaml", ".yml"):
        value = yaml.load(text, Loader=StrictLoader)
    else:
        raise ValueError(f"Unsupported example extension: {suffix}")
    _finite(value)
    return value


def semantic_equal(left, right):
    """Compare JSON values without conflating booleans and numbers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is bool and type(right) is bool and left == right
    if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(semantic_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(semantic_equal(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def markdown_fences(text, languages):
    fences = []
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        match = re.match(r'^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)[^\n]*$', lines[i].rstrip('\r\n'))
        if not match:
            i += 1
            continue
        marker, language = match.groups()
        content = []
        i += 1
        while i < len(lines) and not re.match(r'^ {0,3}' + re.escape(marker[0]) + '{' + str(len(marker)) + r',}\s*$', lines[i].rstrip('\r\n')):
            content.append(lines[i])
            i += 1
        if i == len(lines):
            raise ValueError("Unclosed Markdown code fence")
        if language.lower() in languages:
            fences.append(''.join(content))
        i += 1
    return fences


def markdown_fence(text, languages, index):
    if type(index) is not int or index < 0:
        raise ValueError(f"Invalid fence index: {index!r}")
    fences = markdown_fences(text, languages)
    if index >= len(fences):
        raise ValueError(f"Missing {languages} fence {index}")
    return fences[index]


def markdown_example(text, suffix, index):
    languages = {"json"} if suffix == ".json" else {"yaml", "yml"}
    return parse_data(markdown_fence(text, languages, index), suffix)
