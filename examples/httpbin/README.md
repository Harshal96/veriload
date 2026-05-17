# httpbin Example

This example runs a tiny VeriLoad smoke test against `https://httpbin.org`.
It uses real Verisim-generated synthetic personas and sends persona fields as
query parameters so the response can echo the payload back for inspection.

Run from the repository root:

```bash
uv run veriload validate --config examples/httpbin/veriload.yaml --show-personas 3
uv run veriload run --config examples/httpbin/veriload.yaml
```
