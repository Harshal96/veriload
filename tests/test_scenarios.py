from pathlib import Path

import pytest

from veriload.scenarios import ScenarioLoadError, load_user_class
from veriload.users import VeriUser


def test_load_user_class_imports_veriuser_subclass(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.py"
    scenario.write_text(
        """
from veriload import VeriUser, task

class SmokeUser(VeriUser):
    @task(weight=1)
    async def ping(self):
        self.stop()
""",
        encoding="utf-8",
    )

    user_class = load_user_class(scenario, "SmokeUser")

    assert issubclass(user_class, VeriUser)
    assert user_class.__name__ == "SmokeUser"


def test_load_user_class_rejects_missing_class(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.py"
    scenario.write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ScenarioLoadError, match="MissingUser"):
        load_user_class(scenario, "MissingUser")


def test_load_user_class_requires_veriuser_subclass(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.py"
    scenario.write_text("class NotAUser: pass\n", encoding="utf-8")

    with pytest.raises(ScenarioLoadError, match="VeriUser"):
        load_user_class(scenario, "NotAUser")
