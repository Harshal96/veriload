import pytest

from veriload.engine import LoadTarget, RampProfile, SoakProfile, SpikeProfile, StepProfile


@pytest.mark.asyncio
async def test_soak_profile_emits_constant_target() -> None:
    profile = SoakProfile(target_users=5, duration_seconds=3, tick_seconds=1)

    targets = [target async for target in profile.ticks()]

    assert targets == [
        LoadTarget(elapsed_seconds=0, active_users=5, arrival_rate=None),
        LoadTarget(elapsed_seconds=1, active_users=5, arrival_rate=None),
        LoadTarget(elapsed_seconds=2, active_users=5, arrival_rate=None),
        LoadTarget(elapsed_seconds=3, active_users=0, arrival_rate=None),
    ]


@pytest.mark.asyncio
async def test_ramp_profile_increases_linearly() -> None:
    profile = RampProfile(target_users=4, duration_seconds=4, tick_seconds=1)

    targets = [target.active_users async for target in profile.ticks()]

    assert targets == [0, 1, 2, 3, 4, 0]


@pytest.mark.asyncio
async def test_spike_profile_jumps_then_drops() -> None:
    profile = SpikeProfile(target_users=7, hold_seconds=2, tick_seconds=1)

    targets = [target.active_users async for target in profile.ticks()]

    assert targets == [7, 7, 0]


@pytest.mark.asyncio
async def test_step_profile_emits_each_step() -> None:
    profile = StepProfile(steps=[(2, 1), (5, 2)], tick_seconds=1)

    targets = [target async for target in profile.ticks()]

    assert targets == [
        LoadTarget(elapsed_seconds=0, active_users=2, arrival_rate=None),
        LoadTarget(elapsed_seconds=1, active_users=5, arrival_rate=None),
        LoadTarget(elapsed_seconds=2, active_users=5, arrival_rate=None),
        LoadTarget(elapsed_seconds=3, active_users=0, arrival_rate=None),
    ]
