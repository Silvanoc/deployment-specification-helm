# Application Description Supplemental Material

The following content supplements the [Application Description](https://docs.margo.org/specification/applications/application-description) documentation in the core specification with requirements clarifications specific to Helm workloads.

## Application Description Property Handling

The following defines how certain properties in the Application Description file are handled specifically for Helm workloads. Workload target implementation MUST apply these rules when installing Helm workloads.

- {{ schemaview.induced_slot('name', 'Component').annotations['margo_helm_description'].value }}
- {{ schemaview.induced_slot('wait', 'ComponentProperties').annotations['margo_helm_description'].value }}
- {{ schemaview.induced_slot('pointer', 'ParameterTarget').annotations['margo_helm_description'].value }}

## Example Application Description

```yaml
{{ example('application/ApplicationDescription-001.yaml') }}
```
