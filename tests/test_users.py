import pytest

from veriload.users import FlowFailed, VeriUser, flow, retryable, task


class ExampleUser(VeriUser):
    @task(weight=3)
    async def browse(self) -> None:
        self.state["task"] = "browse"

    @flow("signup")
    async def signup(self) -> None:
        self.state["flow"] = self.payload(
            {
                "email": "contact.email",
                "company_id": "company.id",
                "name": "person.name",
            }
        )


def test_task_metadata_is_discovered() -> None:
    tasks = ExampleUser.discover_tasks()

    assert [(item.name, item.weight) for item in tasks] == [("browse", 3)]


def test_task_rejects_non_positive_weight() -> None:
    with pytest.raises(ValueError, match="weight"):
        task(weight=0)


def test_flow_metadata_is_discovered() -> None:
    flows = ExampleUser.discover_flows()

    assert [item.name for item in flows] == ["signup"]


@pytest.mark.asyncio
async def test_payload_resolves_dotted_persona_fields(sample_persona) -> None:
    user = ExampleUser(persona=sample_persona, user_index=0, run_seed=10)

    await user.signup()

    assert user.state["flow"] == {
        "email": "ada@example.invalid",
        "company_id": "company-1",
        "name": "Ada Lovelace",
    }


def test_payload_raises_attribute_error_for_missing_path(sample_persona) -> None:
    user = ExampleUser(persona=sample_persona, user_index=0, run_seed=10)

    with pytest.raises(AttributeError, match="contact.missing"):
        user.payload({"bad": "contact.missing"})


def test_user_has_model_fixtures_facade_by_default(sample_persona) -> None:
    user = ExampleUser(persona=sample_persona, user_index=2, run_seed=10)

    assert user.fixtures.persona is sample_persona
    assert user.fixtures.run_id == "seed-10"
    assert user.fixtures.worker_index == 2


@pytest.mark.asyncio
async def test_retryable_retries_until_success(sample_persona) -> None:
    attempts = 0

    class RetryUser(VeriUser):
        @retryable(max_attempts=3, backoff_seconds=0)
        async def flaky(self) -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("try again")
            return "ok"

    user = RetryUser(persona=sample_persona, user_index=0, run_seed=10)

    assert await user.flaky() == "ok"
    assert attempts == 3


def test_retryable_rejects_invalid_options() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        retryable(max_attempts=0, backoff_seconds=0)

    with pytest.raises(ValueError, match="backoff_seconds"):
        retryable(max_attempts=1, backoff_seconds=-1)


@pytest.mark.asyncio
async def test_retryable_raises_last_error_after_exhaustion(sample_persona) -> None:
    class RetryUser(VeriUser):
        @retryable(max_attempts=2, backoff_seconds=0)
        async def always_fails(self) -> None:
            raise RuntimeError("still bad")

    user = RetryUser(persona=sample_persona, user_index=0, run_seed=10)

    with pytest.raises(RuntimeError, match="still bad"):
        await user.always_fails()


@pytest.mark.asyncio
async def test_run_once_requires_at_least_one_task(sample_persona) -> None:
    user = VeriUser(persona=sample_persona, user_index=0, run_seed=10)

    with pytest.raises(RuntimeError, match="defines no @task"):
        await user.run_once()


@pytest.mark.asyncio
async def test_flow_failure_is_wrapped_with_flow_name(sample_persona) -> None:
    class BrokenFlowUser(VeriUser):
        @flow("broken")
        async def broken(self) -> None:
            raise RuntimeError("boom")

    user = BrokenFlowUser(persona=sample_persona, user_index=0, run_seed=10)

    with pytest.raises(FlowFailed, match="broken"):
        await user.broken()
