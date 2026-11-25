# Helm Deployment Technology Specification

This repository contains the supplemental requirements for application and workload target suppliers to support the Helm workload deployment technology. This documentation supplements the requirements documented in Margo's [core specification](https://github.com/margo/specification) and does not stand on its own.

**Deployment Technology Key:** `margo.org/deployment/helm`

**Status:** Candidate

**Specification Version:** `1.0.0-rc.3`

> **Note:** This specification follows the same [Versioning Management Strategy](https://docs.margo.org/specification/specification-version-management) outlined in Margo's core specification. This specification is versioned independently of Margo's core specification.

## Supplemental Requirements

The following pages define the additional requirements on supporting the Helm deployment technology:

- [Helm Profile Characteristics](./specification/margo-management-interface/helm-profile-characteristics.md)
- [Application Description Supplemental Material](./specification/applications/application-description.md)
- [Workload Target Requirements Supplemental Material](./specification/margo-workload-targets/workload-target-requirements.md)
- [Collecting Workload Observability Data Supplemental Material](./specification/observability/collecting-workload-observability-data.md)

## Data models and generated documentation

The LinkML schemas in [`model/`](./model/) describe Application Descriptions
and Helm profile characteristics. The Application Description model uses the
shared specification as a reference, with Helm-specific rules applied here.
See [Compatibility issues and inconsistencies](./COMPATIBILITY-ISSUES.md) for
the differences and remaining questions.

Common definitions are imported from the core specification's
`application-description.linkml.yaml`, optional OCI component properties from
`oci-component-properties.linkml.yaml`, and workload-target characteristics
from `workload-target-characteristics.linkml.yaml`, via raw GitHub URLs on the
`externalize-component-specs` branch. Generation and validation require those
URLs to be reachable. The imports follow a moving branch and are therefore not
version-pinned. Generation and validation fetch the remote schemas
independently, and they can change between checks. Numeric-source
verification therefore cannot guarantee consistency with the schema consumed
by LinkML until the dependency is immutable or snapshotted.

Helm-specific constraints apply to profiles with the Helm deployment key.
Other keys are checked against the shared model only and reported as
generic-only unless their specializations are loaded.
To register a loaded component/deployment type, subclass `DeploymentProfile`
and set a unique `slot_usage.key.equals_string`; unknown keys remain generic-only.
Bundle root schemas must declare `margo-common` as
`https://raw.githubusercontent.com/Silvanoc/margo-specification/externalize-component-specs/model/`;
otherwise LinkML import resolution can fail with `Unknown CURIE prefix: margo-common`.

With [uv](https://docs.astral.sh/uv/) installed, validate the examples and
generate and check the Markdown and JSON Schemas with:

```bash
uv sync --locked
uv run --locked python tools/check_examples.py
uv run --locked python tools/generate.py --check
```

Run the workflow tests with `uv run --locked python -m unittest discover -s tests -v`.
An optional [`justfile`](./justfile) provides shortcuts for these commands.

Generated pages are written to `build/site/specification/` and checked against
the corresponding source pages; generated JSON Schemas are in
`build/artifacts/json-schemas/`. The `build/` directory contains disposable
generated output. The Markdown sources in `content/` use Jinja templates in
`tools/templates/` to render model structures. The profile template iterates
over the model's characteristic classes, and the JSON and YAML examples are
inserted from validated fixture files.

Fixtures are discovered under `model/examples/<model>/valid/` and
`model/examples/<model>/invalid/`. Name each file `<ClassName>-<name>.yaml`
or `<ClassName>-<name>.json` so the checker can select its LinkML class.

To validate another document, select its schema and target class:

```bash
uv run --locked python tools/validate.py --schema model/helm-application.linkml.yaml --target-class ApplicationDescription path/to/application.yaml
```

The validation script preserves parsed empty objects and numeric values and
checks additional rules carried in `x-margo-*` metadata. Ordinary JSON Schema
validators may ignore that metadata. Optional typed Application Description
fields may be omitted, but an explicitly supplied `null` is rejected.
Use this repository's `tools/generate.py` and `tools/validate.py` (or `just
check`) for key-based subtype dispatch: bare LinkML JSON Schema generation can
accept a permissive base branch without enforcing known deployment-key
constraints. Native class-level `has_member` required-characteristic constraints
also require this project's generator workaround. Generated JSON Schema enforces structural constraints; the
validation tool additionally enforces `x-margo-*` semantic checks.
Structural validation does not replace the normative prose or verify Helm
chart contents, Kubernetes deployment behavior or registry availability.

## Special Constraints

The Helm deployment technology specification supports workload deployments as Helm charts using either Helm [version 3](https://helm.sh/docs/v3/topics/charts) or [version 4](https://helm.sh/docs/topics/charts) using Chart APIVersion v2 only.

In order to facilitate the required deployment options for Helm charts, the specification does not support Helm functions requiring direct communication with the Kubernetes API.

Your Helm-based workloads MUST NOT rely on the following Helm functions:

- [Lookup](https://helm.sh/docs/chart_template_guide/functions_and_pipelines#using-the-lookup-function): The `lookup` template function queries live resources in the target Kubernetes cluster at render time, for example, to fetch ConfigMaps, Secrets, or other objects. This requires direct API access and cluster credentials during rendering.
- [Hooks](https://helm.sh/docs/topics/charts_hooks/): Hooks (pre/post install, upgrade, delete, rollback, test, etc.) execute additional Kubernetes resources or jobs at specific points in the release lifecycle. Helm waits for these resources to reach a ready state by monitoring them via the Kubernetes API, and the hooks themselves may perform privileged or arbitrary operations in the cluster.
- [CRD management via Helm](https://helm.sh/docs/chart_best_practices/custom_resource_definitions/): Installing or managing Custom Resource Definitions (CRDs) with Helm requires checking for the presence of CRDs and registering new APIs with the cluster, which involves API calls and may require elevated permissions. **Note**: This does not necessarily indicate that CRDs will not be supported, but the way they will be supported has not been defined yet due to the additional requirements for handling them securely.

**Note:** This function list is based on the current Helm v3 and v4 implementations. Additional functions may be added later if Helm introduces new functions requiring communication with the Kubernetes API to work.

## Local Registries

This section describes options for configuring local registries to avoid reliance on public Internet-accessible registries for situations where the Application Package and workload artifacts are not accessible via the internet.

### Option - Container Image Registry Mirror on Kubernetes Level

Kubernetes supports the configuration of registry mirrors. How this is configured depends on the distribution and the underlying container runtime. Distributions that utilize **containerd** as runtime (e.g., k3s or microk8s) allow the definition of mirrors in a configuration file. For example, in k3s, the file `/etc/rancher/k3s/registries.yaml` can be used to set up a mirror for each device's Kubernetes environment:

```yaml
mirrors:
  "docker.io":
    endpoint:
      - "http://<local-registry-ip>:5000"
configs:
  "docker.io":
    auth:
      username: "<username>"
      password: "<password>"
```

### Option - Helm Chart Component Registry as Pull-through Cache

Setting up a pull-through cache for Helm charts in combination with Kubernetes involves configuring a local Helm chart repository, e.g., ChartMuseum, that can be installed with the `PROXY_CACHE` variable set to `true`:

```bash
helm repo add chartmuseum https://chartmuseum.github.io/charts
helm repo update
helm install my-chartmuseum chartmuseum/chartmuseum --set env.open.DISABLE_API=false --set env.open.PROXY_CACHE=true
```

Then, this Helm Chart repository can be added to Helm, and chart releases can be installed there:

```bash
helm repo add my-cached-repo http://<chartmuseum-ip>:8080
helm repo update
helm install my-release my-cached-repo/<chart-name>
```

Now, when deploying applications in Kubernetes using Helm, the cached repository is used to serve charts rather than the remote repository.

## Licenses

Margo contributors contributing to this draft specification have made those contributions subject to the Open Web Foundation Contributor License Agreement Contribution Copyright Grant, available at [Open Web Foundation - CLA Copyright Grant 0.9](https://www.openwebfoundation.org/the-agreements/the-owf-0-9-agreements-necessary-claims/cla-copyright-grant-0-9).

Copyright ©️ 2025 Margo.
