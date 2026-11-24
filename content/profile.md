# Helm Profile Characteristics

The following [Profile](https://docs.margo.org/specification/margo-management-interface/) characteristics apply to workload targets supporting the Helm deployment technology. Workload targets supporting the Helm deployment technology MUST report the applicable characteristics as defined below in the profile.

{% from 'model/profile/index.md.jinja2' import profile_group with context %}{{ profile_group('deployment') }}

## Additional Required Characteristics

The following characteristics MUST be reported for workload targets supporting the Helm deployment technology.

{{ profile_group('characteristic') }}

## Example Profile Payload

```json
{{ example('profile/HelmProfile-001.json') }}
```
