from hitlib_sim.profile import Profile, ProfileMode
from hitlib_sim.strand import Strand


def _make_profile(calls):
    def idle_activate(s):
        calls.append(("idle", "activate"))
        s.set_color(0x000011)

    def red_activate(s):
        calls.append(("red", "activate"))
        s.set_color(0xFF0000)

    def red_tick(s):
        calls.append(("red", "tick"))

    return Profile(
        name="Test",
        modes=[
            ProfileMode(name="Idle", priority=10, on_activate=idle_activate, on_tick=None),
            ProfileMode(name="Red", priority=50, on_activate=red_activate, on_tick=red_tick),
        ],
    )


def test_highest_priority_mode_wins_and_on_activate_fires_once():
    calls = []
    s = Strand(adi_port=1, length=1, refresh_ms=20)
    s.attach_profile(_make_profile(calls))

    s.activate_mode(0)  # Idle
    s.tick()
    assert s.pixels == [0x000011]
    assert calls == [("idle", "activate")]

    s.activate_mode(1)  # Red, higher priority -> should win and fire on_activate once
    s.tick()
    assert s.pixels == [0xFF0000]
    assert calls == [("idle", "activate"), ("red", "activate"), ("red", "tick")]

    s.tick()  # still Red -> on_activate must NOT fire again, on_tick should
    assert calls == [
        ("idle", "activate"), ("red", "activate"), ("red", "tick"), ("red", "tick"),
    ]


def test_deactivating_higher_priority_falls_back_to_remaining_mode():
    calls = []
    s = Strand(adi_port=1, length=1, refresh_ms=20)
    s.attach_profile(_make_profile(calls))

    s.activate_mode(0)
    s.activate_mode(1)
    s.tick()
    assert s.pixels == [0xFF0000]

    s.deactivate_mode(1)
    s.tick()
    assert s.pixels == [0x000011]
    assert calls[-1] == ("idle", "activate")  # switching back re-fires on_activate


def test_activate_mode_timed_expires_and_reverts():
    calls = []
    s = Strand(adi_port=1, length=1, refresh_ms=20)
    s.attach_profile(_make_profile(calls))

    s.activate_mode(0)
    s.activate_mode_timed(1, duration_ms=40)
    s.tick()  # now_ms=20
    assert s.pixels == [0xFF0000]

    s.tick()  # now_ms=40 -- timed entry expires exactly here (>= check), Idle should win
    assert s.pixels == [0x000011]


def test_detach_profile_turns_strand_off():
    s = Strand(adi_port=1, length=2, refresh_ms=20)
    s.attach_profile(_make_profile([]))
    s.activate_mode(1)
    s.tick()
    assert s.pixels == [0xFF0000, 0xFF0000]

    s.detach_profile()
    s.tick()
    assert s.pixels == [0x000000, 0x000000]
