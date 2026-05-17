# Authenticated Object Lifecycle Example

This is a complete template for load testing an API that creates authenticated
users and objects, then deletes the test data later during teardown.

Each virtual user:

1. Registers a synthetic user from its VeriLoad persona.
2. Logs in and stores a bearer token in scenario state.
3. Creates one or more owned objects with a `delete_after` timestamp.
4. Reads the most recently created object with a stable metric name.
5. Deletes all created objects in `on_stop`.
6. Deletes the synthetic user in `on_stop`.

The example assumes these JSON endpoints:

| Endpoint | Expected response fields |
| --- | --- |
| `POST /auth/register` | `id`, `user.id`, or `data.user.id` |
| `POST /auth/login` | `access_token`, `token`, `session.access_token`, or `data.access_token` |
| `POST /objects` | `id`, `object.id`, or `data.id` |
| `GET /objects/{id}` | Any successful JSON or empty body |
| `DELETE /objects/{id}` | Any successful status |
| `DELETE /users/{id}` | Any successful status |

Before running it against your service, edit `veriload.yaml`:

- Set `run.base_url` to your staging or test API.
- Set `safety.allowed_hosts` to the same hostname.
- Keep `safety.max_users`, `safety.max_rps`, and SLOs conservative until the
  endpoint behavior is verified.
- Adjust endpoint paths or response-field helpers in `scenario.py` if your API
  uses different names.

Run from the repository root:

```bash
uv run veriload validate --config examples/auth_object_lifecycle/veriload.yaml --show-personas 3
uv run veriload run --config examples/auth_object_lifecycle/veriload.yaml
```

The scenario does not hardcode shared credentials. Passwords are deterministic,
per-persona load-test credentials generated from the synthetic persona ID and
virtual-user index.
