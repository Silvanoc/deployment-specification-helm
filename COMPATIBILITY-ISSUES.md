# Compatibility issues and inconsistencies

This document records differences between this Helm supplement and the shared
Margo specification, inconsistencies found during the LinkML migration, and
remaining validation questions. It is not normative. The shared specification
is a reference; Helm-specific requirements and examples in this repository take
precedence where they differ. References are pinned where possible because the
specifications are evolving.

## Open specification questions

### Application Description versions

The [Helm example](./specification/applications/application-description.md)
has a top-level `id` and no `kind`. The requested
[`create-data-model` reference](https://github.com/Silvanoc/margo-specification/blob/e82cf368781abc61aec6ce2fe548a1c2bb2540b9/model/application-description.linkml.yaml#L27-L45)
instead requires `kind` and puts `id` under `metadata`. A later
[shared model](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/src/specification/applications/application-description.linkml.yaml#L20-L40)
uses a top-level `id` like this repository's example. These references should
not be treated as interchangeable. The Helm example is preserved.

### Placement selector property paths

The [Application Description example](./specification/applications/application-description.md)
uses `/memory` and `/storage`, while the corresponding [profile characteristics](./specification/margo-management-interface/helm-profile-characteristics.md)
define an `available` property for each. The historical [shared model](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/src/specification/applications/application-description.linkml.yaml#L392-L403)
evaluates `property` relative to the selected characteristic's `properties`.
Under that rule, these two paths would not resolve; `/available` would.
Whether that rule applies unchanged to this evolving supplement needs
confirmation. The example is preserved until that is resolved.

Placement expressions are defined by Margo, with RFC 6901 JSON Pointers for
property paths. They resemble Kubernetes selectors but are not interchangeable
with them. The reference does not explicitly say whether RFC 6901
URI-fragment representations are permitted, and its numeric comparison rules
do not specify a lexical grammar for numeric strings.

### Local Helm chart registry guidance

The [local registry example](./README.md#option---helm-chart-component-registry-as-pull-through-cache)
uses `helm repo add` with an HTTP ChartMuseum repository. That is a classic
Helm chart repository, whereas the shared Application Description model and
this repository's example use `oci://` component repositories. The example
does not demonstrate an OCI-compatible component registry. Official
[Helm OCI guidance](https://helm.sh/docs/v3/topics/registries/) and
[classic chart repository guidance](https://helm.sh/docs/v3/topics/chart_repository/)
describe these as separate workflows. The example also uses `PROXY_CACHE`,
whose support in the referenced ChartMuseum configuration was not established;
its behavior needs verification rather than assumption. The original guidance
is preserved.

### Shared schema and prose

The [shared model at 4137e6a](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/src/specification/applications/application-description.linkml.yaml#L571-L581)
describes scalar and array parameter values, but its LinkML `any_of` lists
only scalar types and marks array support as still to do. Its select-option
description says values match the parameter type while declaring options as
strings. The adapted model supports scalar and homogeneous array parameter
values and typed string, numeric and boolean options. The relationship between
`array[...]` parameter types and individual options remains unclear.

The [shared extension rules](https://github.com/margo/specification/blob/4137e6a6cd0b34f0eb4e7bb3382c1248cf71b3d5/system-design/specification/extensibility/specification-extensions.md#L15-L40)
permit object-valued `x-<name>-extensions`, while a local LinkML 1.12.1
validation of the shared model rejected a valid extension as an additional
property. The Helm validation path preserves the extension mechanism rather
than adopting that rejection.

The shared model describes `Component.name` as lowercase letters, digits and
optional dashes, but does not encode that restriction as a LinkML pattern. The
Helm supplement says the name is the chart name without specifying its full
grammar. The model preserves the reference's string type; the precise naming
constraint needs confirmation before imposing an additional restriction.

The shared model describes optional Application Description fields using
specific types, but its generated JSON Schema also permits explicit `null`.
For this migration, the earlier compatibility decision is retained: optional
typed fields may be omitted, but an explicitly supplied value must have a
documented type and cannot be `null`.

### YAML numeric exponents

The shared specification does not identify a YAML scalar-resolution profile.
LinkML's PyYAML-based loader can interpret `1e1` as a string in YAML, although
the same token is a number in JSON. The Helm validation path recognizes
JSON-compatible exponent notation as numeric while retaining the existing
PyYAML interpretation of other YAML scalar forms. The earlier Compose
validation workflow still follows PyYAML's default behavior for this case; it
has not been changed as part of this Helm migration.

### Helm-specific open notes

The [workload target requirements](./specification/margo-workload-targets/workload-target-requirements.md)
describe `.Capabilities.APIVersions.Has` support as probationary. The
[observability page](./specification/observability/collecting-workload-observability-data.md)
also leaves namespace scope and Kubernetes Objects Receiver configuration as
open actions. These statements remain unchanged; they are not modeled as
settled validation rules.

## Corrected inconsistency

The peripherals and interfaces examples in the original Helm profile page had
trailing commas in JSON objects. The commas were removed with approval; the
rest of the examples and pages are preserved.

LinkML's default JSON loader also drops empty objects during normalization,
although the Helm deployment characteristic example explicitly contains an
empty `properties` object. The supplied validation path preserves the parsed
object so that an empty property bag remains distinct from a missing one.

## Validation boundaries

The validation workflow checks modeled data and examples, but it does not
render Helm charts, query Kubernetes or a registry, or establish that a chart
avoids `lookup`, hooks or Helm-managed CRDs. Standard JSON Schema validators
may ignore `x-margo-*` semantic metadata; use the supplied validation script.
LinkML 1.12.1 can round some numeric schema bounds, so unverified numeric
constraints are rejected rather than silently altered. Decimal implementation
limits apply, and the generic `multipleOf` check fails explicitly above 4,000
coefficient digits. YAML scalar-resolution and selector numeric-string details
remain dependent on the shared specification and underlying parser behavior.

In a local LinkML 1.12.1 check, `linkml examples` failed on the valid CPU
characteristic JSON fixture with a `'string'` error. The validation script
therefore discovers fixture files by directory and validates parsed data
through LinkML's Validator API and the generated schemas. The cause of the
`linkml examples` failure has not been established.
