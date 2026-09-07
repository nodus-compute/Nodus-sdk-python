# Policy and data regions

| Argument | Type | Omitted | CLI | HTTP field |
|---|---|---|---|---|
| `data_regions` | `list[str]` | No explicit region restriction from shortcut | Repeat `--data-region` | `policy.data_regions` |
| `policy` | Dictionary | Absent unless populated | Python only | `policy` |

Use region identifiers supported by your deployment's catalog. Restricting
regions narrows eligible routes and can make a workload infeasible. The SDK
forwards these identifiers. It does not translate cloud-specific region names.

Inside a `with nodus.Client() as client:` block:

```python
# allowed_regions is your account's supported region list.
workload = client.run(
    command=["python", "-c", "print('regional workload')"],
    data_regions=allowed_regions,
    budget=5,
)
```

`policy["data_regions"]` wins over the flat `data_regions` argument. Regions
belong under policy, not requirements. Omission does not promise execution in any
particular geography. Deployment and account policies may still constrain it.
Other dictionary fields require confirmation against the deployed API schema.
