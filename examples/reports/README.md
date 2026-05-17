# Report Comparison Demo

These JSON reports are CLI-first demo fixtures for regression review:

```bash
uv run veriload compare examples/reports/baseline.json examples/reports/current-regression.json \
  --max-p95-regression-ms 25 \
  --max-error-rate-regression 0.01
```

The current report intentionally regresses `POST /checkout` so the command shows
the comparison table and summary text.
