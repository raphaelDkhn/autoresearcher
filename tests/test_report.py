"""Tests for the HTML report."""

from pathlib import Path

from autoresearcher import report
from autoresearcher.cli import Record


def record(run_id: str, status: str, metric: float | None, description: str = "idea") -> Record:
    return Record(
        id=run_id,
        time="2026-10-19T12:00:00+02:00",
        branch="autoresearch/oct19",
        commit="abcdef1234567890",
        status=status,
        metric=metric,
        metrics={},
        seconds=1.5,
        description=description,
        reason="exit code 1" if status == "crash" else None,
    )


RUNS = [
    record("0001", "keep", 9.0, "baseline"),
    record("0002", "discard", 12.0, "<script>alert(1)</script>"),
    record("0003", "crash", None, "boom"),
    record("0004", "keep", 4.0, "better"),
]


def test_report_has_a_row_and_a_point_per_run(tmp_path: Path) -> None:
    html = report.write_report(tmp_path, "proj · main", "loss", "minimize", RUNS).read_text()
    assert html.count("<tr><td>") == 4
    assert html.count('class="pt"') == 4
    assert "Change vs baseline" in html and "-55.56%" in html
    assert "exit code 1" in html


def test_report_escapes_agent_text(tmp_path: Path) -> None:
    html = report.write_report(tmp_path, "proj", "loss", "minimize", RUNS).read_text()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_report_links_run_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "0001"
    run_dir.mkdir(parents=True)
    (run_dir / "pred image.png").write_bytes(b"")
    (run_dir / "run.log").write_text("loss: 9\n")
    html = report.write_report(tmp_path, "proj", "loss", "minimize", RUNS).read_text()
    assert 'src="runs/0001/pred%20image.png"' in html
    assert 'href="runs/0001/run.log"' in html


def test_report_without_runs(tmp_path: Path) -> None:
    html = report.write_report(tmp_path, "proj", "loss", "minimize", []).read_text()
    assert "No runs on this branch yet." in html


def test_report_uses_a_log_axis_for_wide_ranges(tmp_path: Path) -> None:
    runs = [record("0001", "keep", 100.0), record("0002", "keep", 1.0)]
    html = report.write_report(tmp_path, "proj", "loss", "minimize", runs).read_text()
    assert "(log scale)" in html


def test_nice_ticks_are_round_numbers() -> None:
    assert report.nice_ticks(0, 10) == [0, 2, 4, 6, 8, 10]
    assert report.nice_ticks(0.95, 1.01) == [0.96, 0.98, 1.0]
