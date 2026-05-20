# Auto-Cleanup DB Example

This example shows raw `self.db.execute("INSERT ...")` traffic with no manual
`on_stop()` cleanup. VeriLoad tracks inserts into configured tables and cleans
them after the user stops.

```bash
uv run veriload run --config examples/auto_cleanup_db/veriload-delete.yaml
uv run veriload run --config examples/auto_cleanup_db/veriload-rollback.yaml
```

`veriload-delete.yaml` commits the insert and removes it with a generated
`DELETE`. `veriload-rollback.yaml` keeps the tracked insert uncommitted on the
same per-user database connection and rolls it back during cleanup.
