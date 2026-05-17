"""Built-in scenario templates for CLI-first VeriLoad projects."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def render_auth_lifecycle_template(
    *,
    base_url: str,
    user_class: str = "AuthenticatedObjectUser",
) -> dict[str, str]:
    """Render the authenticated object lifecycle template files."""

    host = urlparse(base_url).hostname
    if host is None:
        raise ValueError("base_url must include a hostname")
    return {
        "README.md": _auth_readme(base_url=base_url),
        "scenario.py": _AUTH_SCENARIO.replace("AuthenticatedObjectUser", user_class),
        "veriload.yaml": _auth_config(base_url=base_url, host=host, user_class=user_class),
    }


def write_auth_lifecycle_template(
    output_dir: str | Path,
    *,
    base_url: str,
    user_class: str = "AuthenticatedObjectUser",
    overwrite: bool = False,
) -> tuple[Path, ...]:
    """Write the authenticated lifecycle template into a directory."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in render_auth_lifecycle_template(
        base_url=base_url,
        user_class=user_class,
    ).items():
        path = destination / name
        if path.exists() and not overwrite:
            continue
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return tuple(written)


def _auth_config(*, base_url: str, host: str, user_class: str) -> str:
    return f"""scenario:
  path: scenario.py
  user_class: {user_class}
run:
  base_url: "{base_url}"
  users: 5
  spawn_rate: 2
  max_duration_seconds: 60
data:
  pool_size: 10
  seed: 20260514
  source: memory
  locales:
    - locale: en_US
      weight: 1
profile:
  type: soak
  duration_seconds: 30
  tick_seconds: 1
safety:
  allowed_hosts:
    - {host}
  max_users: 10
  max_rps: 5
  require_non_routable_contacts: true
slo:
  global:
    max_p95_ms: 750
    max_error_rate: 0.01
reports:
  json: reports/auth-object-lifecycle.json
  junit: reports/auth-object-lifecycle.junit.xml
  trace: reports/auth-object-lifecycle.trace.jsonl
  replay: reports/auth-object-lifecycle.replay.json
"""


def _auth_readme(*, base_url: str) -> str:
    return f"""# Authenticated Object Lifecycle

This CLI-first VeriLoad template creates synthetic users, logs them in, exercises
owned objects, and cleans up generated data in `on_stop`.

Review `scenario.py` before running. The default API target is `{base_url}`.

```bash
uv run veriload validate --config veriload.yaml --show-personas 3
uv run veriload run --config veriload.yaml
```
"""


_AUTH_SCENARIO = '''"""Authenticated object lifecycle scenario."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from veriload import VeriUser, flow, retryable, task


class AuthenticatedObjectUser(VeriUser):
    """Authenticate a synthetic user, exercise objects, then clean up."""

    async def on_start(self) -> None:
        await self.register_user()
        await self.login_user()

    async def on_stop(self) -> None:
        await self.delete_created_objects()
        await self.delete_user()

    @flow("register synthetic user")
    async def register_user(self) -> None:
        profile = self.payload(
            {
                "email": "contact.email",
                "name": "person.name",
                "username": "person.username",
            }
        )
        payload = {
            **profile,
            "external_id": self.persona.persona_id,
            "password": self._password(),
            "company_id": self.persona.company.id,
        }
        response = await self.http.post(
            "/auth/register",
            name="POST /auth/register",
            json=payload,
        )
        response.raise_for_status()
        body = _json_body(response, operation="register synthetic user")
        self._remember(
            email=payload["email"],
            user_id=_required_string(body, ("id", "user.id", "data.user.id")),
        )

    @flow("login synthetic user")
    @retryable(max_attempts=3, backoff_seconds=0.25)
    async def login_user(self) -> None:
        response = await self.http.post(
            "/auth/login",
            name="POST /auth/login",
            json={"email": self.state["email"], "password": self._password()},
        )
        response.raise_for_status()
        body = _json_body(response, operation="login synthetic user")
        self._remember(
            access_token=_required_string(
                body,
                ("access_token", "token", "session.access_token", "data.access_token"),
            )
        )

    @task(weight=3)
    async def create_object_for_later_cleanup(self) -> None:
        response = await self.http.post(
            "/objects",
            name="POST /objects",
            headers=self._auth_headers(),
            json={
                "owner_id": self.state["user_id"],
                "title": f"Load object {self.persona.person.username}",
                "delete_after": self._delete_after_timestamp(),
            },
        )
        response.raise_for_status()
        body = _json_body(response, operation="create object")
        object_id = _required_string(body, ("id", "object.id", "data.id"))
        self._remember(object_ids=(*self._object_ids(), object_id), last_object_id=object_id)

    @task(weight=2)
    async def read_created_object(self) -> None:
        object_id = self.state.get("last_object_id")
        if not isinstance(object_id, str):
            await self.create_object_for_later_cleanup()
            object_id = _required_state_string(self.state, "last_object_id")
        response = await self.http.get(
            f"/objects/{object_id}",
            name="GET /objects/{id}",
            headers=self._auth_headers(),
        )
        response.raise_for_status()

    @flow("delete created objects later")
    @retryable(max_attempts=3, backoff_seconds=0.25)
    async def delete_created_objects(self) -> None:
        for object_id in reversed(self._object_ids()):
            response = await self.http.request(
                "DELETE",
                f"/objects/{object_id}",
                name="DELETE /objects/{id}",
                headers=self._auth_headers(),
            )
            response.raise_for_status()
        self._remember(object_ids=(), last_object_id=None)

    @flow("delete synthetic user")
    @retryable(max_attempts=3, backoff_seconds=0.25)
    async def delete_user(self) -> None:
        user_id = self.state.get("user_id")
        if not isinstance(user_id, str):
            return
        response = await self.http.request(
            "DELETE",
            f"/users/{user_id}",
            name="DELETE /users/{id}",
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        self._remember(user_id=None, access_token=None)

    def _auth_headers(self) -> dict[str, str]:
        token = self.state.get("access_token")
        return {"Authorization": f"Bearer {token}"} if isinstance(token, str) else {}

    def _delete_after_timestamp(self) -> str:
        return (datetime.now(UTC) + timedelta(hours=24)).isoformat()

    def _object_ids(self) -> tuple[str, ...]:
        value = self.state.get("object_ids", ())
        return tuple(item for item in value if isinstance(item, str)) if isinstance(value, tuple) else ()

    def _password(self) -> str:
        return f"VeriLoad-{self.persona.persona_id}-{self.user_index}!"

    def _remember(self, **values: Any) -> None:
        self.state = {**self.state, **values}


def _json_body(response: Any, *, operation: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{operation} response must be JSON") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"{operation} response must be a JSON object")
    return body


def _required_string(body: dict[str, Any], paths: tuple[str, ...]) -> str:
    for path in paths:
        value = _read_path(body, path)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, int):
            return str(value)
    raise RuntimeError(f"Response did not include any of: {', '.join(paths)}")


def _required_state_string(state: dict[str, Any], key: str) -> str:
    value = state.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Scenario state is missing {key!r}")
    return value


def _read_path(body: dict[str, Any], path: str) -> Any:
    current: Any = body
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
'''
