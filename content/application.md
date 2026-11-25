# Application Description Supplemental Material

The following content supplements the [Application Description](https://docs.margo.org/specification/applications/application-description) documentation in the core specification with requirements clarifications specific to Helm workloads.

## Application Description Property Handling

The following defines how certain properties in the Application Description file are handled specifically for Helm workloads. Workload target implementation MUST apply these rules when installing Helm workloads.

- The `component.name` property MUST be used as the chart name when installing Helm charts.
- The `component.properties.wait` indicates the workload target MUST wait until the component is deployed before moving to the next component.
- The `parameter.targets[].pointer` MUST use the dot notation for matching elements in the Helm chart's `values.yaml` file. This follows the same naming convention used with the --set command-line argument with the Helm install command.

## Example Application Description

```yaml
{{ example('application/ApplicationDescription-001.yaml') }}
```
