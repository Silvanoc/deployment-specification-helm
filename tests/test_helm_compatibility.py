"""Focused checks for the shared application model and Compose overrides."""

import json
import copy
import subprocess
import sys
import unittest
from pathlib import Path

import jsonschema
import yaml
from linkml_runtime import SchemaView
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from schema_validation import ValidationEngine, precise_validator
from common import parse_data

MODEL = ROOT / "model/helm-application.linkml.yaml"
EXAMPLE = ROOT / "model/examples/application/valid/ApplicationDescription-001.yaml"


class ApplicationEngineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = ValidationEngine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        self.application = parse_data(EXAMPLE.read_text(), ".yaml")

    def assert_engine_valid(self, application):
        before = copy.deepcopy(application)
        errors, _ = self.engine.validate(MODEL, "ApplicationDescription", application)
        self.assertEqual(errors, [], errors)
        self.assertEqual(application, before, "validation must preserve raw input")

    def test_mixed_helm_and_compose_profiles(self):
        helm = copy.deepcopy(self.application["deploymentProfiles"][0])
        helm["key"] = "margo.org/deployment/compose"
        helm["id"] = "example-helm"
        self.application["deploymentProfiles"].append(helm)
        self.assert_engine_valid(self.application)

    def test_documented_profile_conforms_to_helm_specific_class(self):
        profiles = self.application["deploymentProfiles"]
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        errors, _ = self.engine.validate(MODEL, "HelmDeploymentProfile", profile)
        self.assertEqual(errors, [], errors)
        wrong_key = {**profile, "key": "margo.org/deployment/compose"}
        errors, _ = self.engine.validate(MODEL, "HelmDeploymentProfile", wrong_key)
        self.assertTrue(errors)

    def test_object_extensions_at_all_levels(self):
        profile = self.application["deploymentProfiles"][0]
        for node in (self.application, profile, profile["components"][0]):
            node["x-acme-extensions"] = {"enabled": True, "nested": {"value": None}}
        self.assert_engine_valid(self.application)
        schema, _ = self.engine.cache[(str(MODEL.resolve()), "ApplicationDescription")]
        errors = list(precise_validator(schema).iter_errors(self.application))
        self.assertEqual(errors, [], [error.message for error in errors])

    def test_invalid_extensions_rejected(self):
        for location in ("application", "profile", "component"):
            for name, value in (("x-acme-extensions", "not an object"),
                                ("x-Acme-extensions", {}),
                                ("x_placeholder_extensions", {})):
                with self.subTest(location=location, name=name):
                    application = copy.deepcopy(self.application)
                    profile = application["deploymentProfiles"][0]
                    node = {"application": application, "profile": profile,
                            "component": profile["components"][0]}[location]
                    node[name] = value
                    errors, _ = self.engine.validate(MODEL, "ApplicationDescription", application)
                    self.assertTrue(errors, (location, name, value))

    def test_complete_application_values(self):
        profile = self.application["deploymentProfiles"][0]
        profile["placementConstraints"][0]["matchExpressions"].append(
            {"key": "feature", "property": "/enabled", "operator": "In", "values": [True]})
        self.application["parameters"]["idpName"]["value"] = ["first", "second"]
        profile["components"][0]["properties"]["timeout"] = "5m0s"
        self.assert_engine_valid(self.application)

    def test_select_option_types_in_engine(self):
        for data_type, options in (("string", ["a", "b"]), ("integer", [30, 60]),
                                   ("double", [1.5, 2.5]), ("boolean", [True, False])):
            with self.subTest(data_type=data_type):
                errors, _ = self.engine.validate(MODEL, "SelectValidationSchema",
                                                 {"name": "choice", "dataType": data_type, "options": options})
                self.assertEqual(errors, [], errors)

    def test_oci_revision_build_metadata_and_tag_length(self):
        base = {"repository": "oci://registry.example.com/example"}
        for revision, valid in (("1.2.3_build.1", True),
                                ("1.2.3+build.1", False),
                                ("1.2.3_" + "a" * 122, True),
                                ("1.2.3_" + "a" * 123, False)):
            with self.subTest(revision=revision):
                errors, _ = self.engine.validate(MODEL, "ComponentProperties", {**base, "revision": revision})
                self.assertEqual(not errors, valid, errors)

    def test_absolute_end_patterns_in_engine_and_patched_schema(self):
        self.assert_engine_valid(self.application)
        schema, _ = self.engine.cache[(str(MODEL.resolve()), "ApplicationDescription")]
        validator = precise_validator(schema)
        for ending in ("", "\n", "\r\n"):
            for field in ("id", "revision", "timeout", "extension"):
                with self.subTest(field=field, ending=repr(ending)):
                    application = copy.deepcopy(self.application)
                    properties = application["deploymentProfiles"][0]["components"][0]["properties"]
                    if field == "id":
                        application["id"] += ending
                    elif field == "revision":
                        properties["revision"] = "1.0.0_build.1" + ending
                    elif field == "timeout":
                        properties["timeout"] = "5m0s" + ending
                    else:
                        application["x-acme-extensions" + ending] = {}
                    errors, _ = self.engine.validate(MODEL, "ApplicationDescription", application)
                    schema_errors = list(validator.iter_errors(application))
                    self.assertEqual(bool(errors), bool(ending), errors)
                    self.assertEqual(bool(schema_errors), bool(ending), [e.message for e in schema_errors])



class HelmMetadataTests(unittest.TestCase):
    def test_helm_descriptions_and_display_metadata(self):
        view = SchemaView(str(MODEL))
        cases = (
            ('Component', 'name', 'The `component.name` property MUST be used as the chart name when installing Helm charts.', ('identify the component package', 'chart name')),
            ('ComponentProperties', 'wait', 'The `component.properties.wait` indicates the workload target MUST wait until the component is deployed before moving to the next component.', ('For Helm:', 'For Compose:', 'running state')),
            ('ParameterTarget', 'pointer', "The `parameter.targets[].pointer` MUST use the dot notation for matching elements in the Helm chart's `values.yaml` file. This follows the same naming convention used with the --set command-line argument with the Helm install command.", ('For Helm deployments', 'For compose deployments', 'environment variable')),
        )
        for cls, field, bullet, shared_terms in cases:
            with self.subTest(cls=cls, field=field):
                slot = view.induced_slot(field, cls)
                self.assertEqual(slot.annotations['margo_helm_description'].value, bullet)
                for term in shared_terms:
                    self.assertIn(term, slot.description)
        profile = SchemaView(str(ROOT / 'model/helm-profile.linkml.yaml'))
        required = json.loads(profile.induced_slot('characteristics', 'HelmProfile').annotations['margo_required_characteristics'].value)
        displayed = []
        ranks = set()
        for cls in profile.all_classes().values():
            if 'margo_profile_kind' not in cls.annotations:
                continue
            self.assertTrue(cls.title and cls.rank is not None)
            self.assertNotIn(cls.rank, ranks)
            ranks.add(cls.rank)
            target = cls.annotations['margo_profile_characteristic'].value
            fixture = ROOT / 'model/examples' / cls.annotations['margo_profile_example'].value.replace('profile/', 'profile/valid/', 1)
            key = profile.induced_slot('key', target).equals_string
            self.assertEqual(json.loads(fixture.read_text())['key'], key)
            displayed.append(key)
        self.assertEqual(set(required), set(displayed))
