from pathlib import Path

import pytest

from veriload.config import ConfigError, LoadProfileConfig, VeriLoadConfig, load_config


def test_load_config_merges_file_environment_and_cli_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "veriload.yaml"
    config_path.write_text(
        """
run:
  base_url: "https://api.example.test"
  users: 10
  spawn_rate: 2
  max_duration_seconds: 30
data:
  pool_size: 20
  seed: 123
  locales:
    - locale: en_US
      weight: 1.0
profile:
  type: ramp
  duration_seconds: 5
safety:
  allowed_hosts:
    - api.example.test
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("VERILOAD_RUN__USERS", "12")

    config = load_config(config_path, cli_overrides={"data.seed": 999})

    assert config.run.base_url == "https://api.example.test"
    assert config.run.users == 12
    assert config.profile.target_users == 12
    assert config.data.seed == 999
    assert config.profile.type == "ramp"


def test_profile_target_users_defaults_to_run_users() -> None:
    config = VeriLoadConfig(
        run={
            "base_url": "https://api.example.test",
            "users": 3,
            "spawn_rate": 2,
            "max_duration_seconds": 10,
        },
        data={"pool_size": 3, "seed": 1},
        profile={"type": "soak", "duration_seconds": 5},
        safety={"allowed_hosts": ["api.example.test"]},
    )

    assert config.profile.target_users == 3


def test_config_accepts_database_only_run_target() -> None:
    config = VeriLoadConfig(
        run={
            "database": {"driver": "sqlite", "dsn": ":memory:"},
            "users": 3,
            "spawn_rate": 2,
            "max_duration_seconds": 10,
        },
        data={"pool_size": 3, "seed": 1},
        profile={"type": "soak", "duration_seconds": 5},
        safety={"allowed_hosts": []},
    )

    assert config.run.base_url is None
    assert config.run.database is not None
    assert config.run.database.driver == "sqlite"
    assert config.profile.target_users == 3


def test_config_accepts_cleanup_targets_and_database_strategy() -> None:
    config = VeriLoadConfig(
        run={
            "database": {"driver": "sqlite", "dsn": ":memory:"},
            "users": 3,
            "spawn_rate": 2,
            "max_duration_seconds": 10,
        },
        data={"pool_size": 3, "seed": 1},
        profile={"type": "soak", "duration_seconds": 5},
        cleanup={
            "enabled": True,
            "http": {
                "targets": [
                    {
                        "method": "POST",
                        "path": "/objects",
                        "delete_path": "/objects/{id}",
                        "id_fields": ["id", "data.id"],
                    }
                ]
            },
            "database": {"strategy": "rollback", "tables": ["synthetic_users"]},
        },
        safety={"allowed_hosts": []},
    )

    assert config.cleanup is not None
    assert config.cleanup.enabled is True
    assert config.cleanup.http.targets[0].delete_path == "/objects/{id}"
    assert config.cleanup.database.strategy == "rollback"
    assert config.cleanup.database.tables == ("synthetic_users",)


def test_config_rejects_invalid_cleanup_strategy() -> None:
    with pytest.raises(ValueError, match="delete|rollback"):
        VeriLoadConfig(
            run={
                "database": {"driver": "sqlite", "dsn": ":memory:"},
                "users": 3,
                "spawn_rate": 2,
                "max_duration_seconds": 10,
            },
            data={"pool_size": 3, "seed": 1},
            profile={"type": "soak", "duration_seconds": 5},
            cleanup={"enabled": True, "database": {"strategy": "truncate", "tables": []}},
            safety={"allowed_hosts": []},
        )


def test_config_requires_at_least_one_run_target() -> None:
    with pytest.raises(ConfigError, match="run.base_url or run.database is required"):
        VeriLoadConfig(
            run={
                "users": 3,
                "spawn_rate": 2,
                "max_duration_seconds": 10,
            },
            data={"pool_size": 3, "seed": 1},
            profile={"type": "soak", "duration_seconds": 5},
            safety={"allowed_hosts": []},
        )


def test_config_rejects_profile_target_user_mismatch() -> None:
    with pytest.raises(ConfigError, match="profile.target_users must match run.users"):
        VeriLoadConfig(
            run={
                "base_url": "https://api.example.test",
                "users": 3,
                "spawn_rate": 2,
                "max_duration_seconds": 10,
            },
            data={"pool_size": 3, "seed": 1},
            profile={"type": "soak", "target_users": 4, "duration_seconds": 5},
            safety={"allowed_hosts": ["api.example.test"]},
        )


def test_config_rejects_profile_duration_above_runtime_cap() -> None:
    with pytest.raises(ConfigError, match="profile.duration_seconds exceeds run.max_duration_seconds"):
        VeriLoadConfig(
            run={
                "base_url": "https://api.example.test",
                "users": 3,
                "spawn_rate": 2,
                "max_duration_seconds": 5,
            },
            data={"pool_size": 3, "seed": 1},
            profile={"type": "soak", "target_users": 3, "duration_seconds": 10},
            safety={"allowed_hosts": ["api.example.test"]},
        )


def test_config_rejects_spike_hold_above_runtime_cap() -> None:
    with pytest.raises(ConfigError, match="profile.hold_seconds exceeds run.max_duration_seconds"):
        VeriLoadConfig(
            run={
                "base_url": "https://api.example.test",
                "users": 3,
                "spawn_rate": 2,
                "max_duration_seconds": 5,
            },
            data={"pool_size": 3, "seed": 1},
            profile={
                "type": "spike",
                "target_users": 3,
                "duration_seconds": 1,
                "hold_seconds": 10,
            },
            safety={"allowed_hosts": ["api.example.test"]},
        )


def test_config_rejects_allowed_host_mismatch() -> None:
    with pytest.raises(ConfigError, match="not present in safety.allowed_hosts"):
        VeriLoadConfig(
            run={
                "base_url": "https://prod.example.com",
                "users": 1,
                "spawn_rate": 1,
                "max_duration_seconds": 1,
            },
            data={"pool_size": 1, "seed": 1},
            profile={"type": "soak", "target_users": 1, "duration_seconds": 1},
            safety={"allowed_hosts": ["staging.example.com"]},
        )


def test_load_profile_config_requires_positive_duration() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        LoadProfileConfig(type="soak", duration_seconds=0)


def test_load_config_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path / "missing.yaml")


def test_load_config_reports_non_mapping_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "veriload.yaml"
    config_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigError, match="must contain a YAML mapping"):
        load_config(config_path)


def test_config_rejects_user_count_above_safety_cap() -> None:
    with pytest.raises(ConfigError, match="exceeds safety.max_users"):
        VeriLoadConfig(
            run={
                "base_url": "https://api.example.test",
                "users": 10,
                "spawn_rate": 1,
                "max_duration_seconds": 1,
            },
            data={"pool_size": 10, "seed": 1},
            profile={"type": "soak", "target_users": 10, "duration_seconds": 1},
            safety={"allowed_hosts": ["api.example.test"], "max_users": 5},
        )


def test_config_rejects_spawn_rate_above_safety_rps_cap() -> None:
    with pytest.raises(ConfigError, match="exceeds safety.max_rps"):
        VeriLoadConfig(
            run={
                "base_url": "https://api.example.test",
                "users": 10,
                "spawn_rate": 10,
                "max_duration_seconds": 1,
            },
            data={"pool_size": 10, "seed": 1},
            profile={"type": "soak", "target_users": 10, "duration_seconds": 1},
            safety={"allowed_hosts": ["api.example.test"], "max_rps": 5},
        )
