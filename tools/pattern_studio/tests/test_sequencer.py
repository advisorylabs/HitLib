from hitlib_sim.sequencer import Phase, Sequencer
from hitlib_sim.strand import Strand


def test_sequencer_walks_and_loops_phases():
    calls = []

    def phase0(strand):
        calls.append(("phase0", strand.now_ms))

    def phase1(strand):
        calls.append(("phase1", strand.now_ms))

    seq = Sequencer([Phase(40, phase0), Phase(20, phase1)])
    s = Strand(adi_port=1, length=3, refresh_ms=20)

    seq.start(s)
    assert calls == [("phase0", 0)]
    assert seq.is_running

    s.tick()  # now_ms=20, phase0 duration (40) not yet elapsed
    seq.update(s)
    assert calls == [("phase0", 0)]

    s.tick()  # now_ms=40, phase0 elapses -> phase1 starts
    seq.update(s)
    assert calls == [("phase0", 0), ("phase1", 40)]

    s.tick()  # now_ms=60, phase1 (duration 20) elapses -> wraps to phase0
    seq.update(s)
    assert calls == [("phase0", 0), ("phase1", 40), ("phase0", 60)]


def test_stop_halts_updates():
    calls = []
    seq = Sequencer([Phase(10, lambda strand: calls.append(strand.now_ms))])
    s = Strand(adi_port=1, length=1, refresh_ms=20)

    seq.start(s)
    seq.stop()
    assert not seq.is_running

    s.tick()
    seq.update(s)
    assert calls == [0]  # only the initial start() call fired
