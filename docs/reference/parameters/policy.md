# Policy and data regions

| Argument | Type | Omitted | Workload file | HTTP field |
|---|---|---|---|---|
| `data_regions` | `list[str]` | No explicit region restriction from shortcut | `data_regions` | `policy.data_regions` |
| `policy` | Dictionary | Absent unless populated | Same key or table | `policy` |

`data_regions` is a list of exact region identifiers accepted by your deployment.
An empty list adds no location restriction. There is no universal region list
or region-discovery method in this SDK. For the hosted service,
[contact Nodus](mailto:nodus.infrastructure@gmail.com) for the enabled identifiers
before setting a location restriction. For a private deployment, obtain them
from its administrator. Do not assume a cloud provider's region codes are valid.

Restricting regions narrows eligible routes and can make a workload infeasible.
The SDK forwards the identifiers without translating them.

Inside a `with nodus.Client() as client:` block:

```python
workload = client.run(
    image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
    command=["python", "-c", "print('regional workload')"],
    data_regions=[],
    budget=5,
)
```

This example adds no region restriction. If your workload requires a particular
geography, replace the empty list with the exact approved identifiers before
submitting. Do not use unrestricted execution for a location-sensitive workload.

`policy["data_regions"]` wins over the flat `data_regions` argument. Regions
belong under policy, not requirements. Omission does not promise execution in any
particular geography. Deployment and account policies may still constrain it.
`data_regions` is the only policy field documented for this SDK interface.
