# Model Workloads Example

This example shows `self.model_workload(...)`, which generates insert/select
database traffic from model metadata. The scenario uses a tiny SQLAlchemy-like
model so it runs without optional SQLModel or Django dependencies.

```bash
uv run veriload run --config examples/model_workloads/veriload.yaml
uv run veriload run --config examples/model_workloads/veriload-rollback.yaml
```

`mode="sql"` executes generated SQL through `self.db`, so the insert and select
appear in normal DB metrics. Cleanup is separate: `veriload.yaml` deletes the
generated row, while `veriload-rollback.yaml` rolls back the tracked insert.
