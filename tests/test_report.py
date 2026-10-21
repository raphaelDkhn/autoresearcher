"""Tests for the HTML report."""

from pathlib import Path

import pytest

from infinit_research import report
from infinit_research.cli import Manifest, Probe, Record

MANIFEST = Manifest("loss", "minimize", 0.0, "true", 1.0, ("x.txt",))


def record(
    run_id: str, status: str, metric: float | None, description: str = "idea", lab: str = "aaa"
) -> Record:
    return Record(
        id=run_id,
        time="2026-10-19T12:00:00+02:00",
        branch="infinit-research/oct19",
        commit="abcdef1234567890",
        status=status,
        metric=metric,
        metrics={},
        seconds=1.5,
        description=description,
        reason="exit code 1" if status == "crash" else None,
        lab=lab,
    )


RUNS = [
    record("0001", "keep", 9.0, "baseline"),
    record("0002", "discard", 12.0, "<script>alert(1)</script>"),
    record("0003", "crash", None, "boom"),
    record("0004", "keep", 4.0, "better"),
]


def test_report_has_a_row_and_a_point_per_run(tmp_path: Path) -> None:
    html = report.write_report(tmp_path, "proj · main", MANIFEST, RUNS, []).read_text()
    assert html.split('<table id="runs">')[1].count("<tr><td>") == 4
    assert html.count('class="pt"') == 4
    assert "Change vs baseline" in html and "-55.56%" in html
    assert "exit code 1" in html


def test_report_escapes_agent_text(tmp_path: Path) -> None:
    html = report.write_report(tmp_path, "proj", MANIFEST, RUNS, []).read_text()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_report_links_run_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "0001"
    run_dir.mkdir(parents=True)
    (run_dir / "pred image.png").write_bytes(b"")
    (run_dir / "run.log").write_text("loss: 9\n")
    html = report.write_report(tmp_path, "proj", MANIFEST, RUNS, []).read_text()
    assert 'src="runs/0001/pred%20image.png"' in html
    assert 'href="runs/0001/run.log"' in html


def test_report_without_runs(tmp_path: Path) -> None:
    html = report.write_report(tmp_path, "proj", MANIFEST, [], []).read_text()
    assert "No runs on this branch yet." in html


def test_report_uses_a_log_axis_for_wide_ranges(tmp_path: Path) -> None:
    runs = [record("0001", "keep", 100.0), record("0002", "keep", 1.0)]
    html = report.write_report(tmp_path, "proj", MANIFEST, runs, []).read_text()
    assert "(log scale)" in html


def test_nice_ticks_are_round_numbers() -> None:
    assert report.nice_ticks(0, 10) == [0, 2, 4, 6, 8, 10]
    assert report.nice_ticks(0.95, 1.01) == [0.96, 0.98, 1.0]


def probe(
    probe_id: str,
    runs: int,
    description: str = "quick check",
    branch: str = "infinit-research/oct19",
) -> Probe:
    return Probe(
        id=probe_id,
        time="2026-10-19T12:00:00+02:00",
        branch=branch,
        head="abcdef1234567890",
        lab="aaa",
        runs=runs,
        status="ok",
        metrics={"quick": 1.5},
        seconds=30.0,
        description=description,
        reason=None,
    )


def test_windows_measure_how_far_the_best_moved() -> None:
    runs = [
        record("0001", "keep", 10.0, lab="aaa"),
        record("0002", "discard", 11.0, lab="aaa"),
        record("0003", "keep", 8.0, lab="aaa"),
        record("0004", "crash", None, lab="bbb"),
        record("0005", "keep", 4.0, lab="bbb"),
    ]
    probes = [probe("0001", 1), probe("0002", 2), probe("0003", 3)]
    first, second, third = report.windows(runs, probes, "minimize", 2)
    assert (first.first, first.last, first.runs) == ("0001", "0002", 2)
    assert (first.kept, first.gain, first.probes) == (1, 0, 1)
    assert second.gain == pytest.approx(0.2)
    assert (second.failed, second.labs, second.probes) == (1, ("aaa", "bbb"), 2)
    assert second.probe_seconds == 60.0
    assert third.gain == pytest.approx(0.5)
    assert (third.seconds, third.probes) == (1.5, 0)


def test_windows_measure_gain_in_the_goal_direction() -> None:
    rising = [record("1", "keep", 2.0), record("2", "keep", 3.0)]
    assert report.windows(rising, [], "maximize", 10)[0].gain == pytest.approx(0.5)
    from_zero = [record("1", "keep", 0.0), record("2", "keep", 1.0)]
    assert report.windows(from_zero, [], "maximize", 10)[0].gain == 0


def test_report_shows_the_research_harness_and_any_probes(tmp_path: Path) -> None:
    without_probes = report.write_report(tmp_path, "proj", MANIFEST, RUNS, []).read_text()
    assert "Research harness" in without_probes and "<h2>Probes</h2>" not in without_probes
    runs = [
        record("0001", "keep", 9.0, lab="3fa2c1d4e5f6"),
        record("0002", "keep", 4.0, lab="3fa2c1d4e5f6"),
    ]
    probes = [probe("0001", 1, "<b>quick</b>"), probe("0002", 1, branch="other")]
    html = report.write_report(tmp_path, "proj", MANIFEST, runs, probes).read_text()
    assert "Research harness" in html
    assert 'href="lab-versions/3fa2c1d4e5f6/strategy.md"' in html
    assert "+55.56%" in html
    assert "&lt;b&gt;quick&lt;/b&gt;" in html and "<b>quick</b>" not in html
    probes_table = html.split("<h2>Probes</h2>")[1].split("</table>")[0]
    assert 'href="probes/0001/run.log"' in probes_table and "#0002" not in probes_table
