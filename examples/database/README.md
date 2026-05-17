# Database Example

This example runs a local SQLite smoke test without any network dependency. It
uses the built-in `run.database` SQLite target, inserts one deterministic
persona-backed row, verifies it with a query, and deletes it during cleanup.

Run from the repository root:

```bash
uv run veriload validate --config examples/database/veriload.yaml --show-personas 1
uv run veriload run --config examples/database/veriload.yaml
```

The config uses `dsn: ":memory:"`, so it does not create a database file. To run
against a file-backed SQLite database, change the DSN to a path such as
`./examples/database/load-test.sqlite`.

Use stable SQL metric names like `DB select synthetic user` so SLOs and report
comparisons stay low-cardinality and do not include literal query values.
