"""Self-contained HTML report for one autoresearch branch: progress chart, runs table, artifacts."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from autoresearcher.cli import Record

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
REFRESH_SECONDS = 30
WINDOW_RUNS = 10  # recursive mode reviews its research harness after each window of runs
# Chart geometry in viewBox units; the SVG scales to its container.
WIDTH, HEIGHT = 760, 300
LEFT, RIGHT, TOP, BOTTOM = 64, 16, 16, 64
LOG_SCALE_RATIO = 10  # switch to a log y axis when kept values span more than this
MIN_LOG_TICKS = 3
RUG_OFFSET = 30  # crashes have no metric, so they sit in a band under the x axis
STATUS_LABELS = {
    "keep": ("✓", "kept"),
    "discard": ("·", "discarded"),
    "crash": ("✕", "crashed"),
    "timeout": ("✕", "timed out"),
}

STYLE = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --best: #2a78d6; --good: #0ca30c; --neutral: #898781; --critical: #d03b3b; --up: #006300;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --best: #3987e5; --up: #0ca30c;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 0 0 8px; }
.sub { color: var(--ink-2); margin: 0 0 20px; }
.skip { position: absolute; left: -9999px; }
.skip:focus { left: 16px; top: 8px; background: var(--surface); padding: 4px 8px; }
.stats { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.stat { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px 16px; min-width: 160px; }
.stat .label { color: var(--ink-2); font-size: 13px; }
.stat .value { font-size: 24px; font-weight: 600; }
.stat .value.up { color: var(--up); }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px; margin-bottom: 16px; }
.legend { display: flex; flex-wrap: wrap; gap: 16px; color: var(--ink-2); font-size: 13px;
  margin: 0 0 8px; padding: 0; list-style: none; }
.legend svg { vertical-align: middle; margin-right: 6px; }
svg.chart { width: 100%; height: auto; display: block; }
.grid line { stroke: var(--grid); stroke-width: 1; }
.axis { stroke: var(--axis); stroke-width: 1; }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.axis-title { fill: var(--ink-2); font-size: 12px; }
.best { fill: none; stroke: var(--best); stroke-width: 2;
  stroke-linejoin: round; stroke-linecap: round; }
.pt { cursor: default; outline: none; }
.pt .hit { fill: transparent; }
.pt .mark { transform-box: fill-box; transform-origin: center; transition: transform 80ms; }
.pt:hover .mark, .pt:focus-visible .mark { transform: scale(1.4); }
.keep { fill: var(--good); stroke: var(--surface); stroke-width: 2; }
.discard { fill: var(--neutral); stroke: var(--surface); stroke-width: 2; }
.fail { stroke: var(--critical); stroke-width: 2; stroke-linecap: round; }
#tip { position: fixed; pointer-events: none; background: var(--surface); color: var(--ink);
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 12px;
  max-width: 360px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--grid);
  vertical-align: top; }
th { color: var(--ink-2); font-weight: 600; }
td.num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
td.status { white-space: nowrap; }
.icon-keep { color: var(--good); } .icon-discard { color: var(--neutral); }
.icon-crash, .icon-timeout { color: var(--critical); }
.reason { color: var(--ink-2); display: block; }
code { font-size: 12px; }
.thumbs a { display: inline-block; margin: 0 4px 4px 0; }
.thumbs img { height: 56px; border-radius: 4px; border: 1px solid var(--border); }
.empty { color: var(--ink-2); }
"""

SCRIPT = """
const tip = document.getElementById('tip');
function show(event) {
  const box = event.currentTarget.getBoundingClientRect();
  tip.textContent = event.currentTarget.dataset.tip;
  tip.hidden = false;
  const left = Math.min(box.left + box.width / 2 + 12, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = Math.max(8, left) + 'px';
  tip.style.top = Math.max(8, box.top - tip.offsetHeight - 8) + 'px';
}
function hide() { tip.hidden = true; }
for (const point of document.querySelectorAll('.pt')) {
  point.addEventListener('pointerenter', show);
  point.addEventListener('pointerleave', hide);
  point.addEventListener('focus', show);
  point.addEventListener('blur', hide);
}
"""


def write_report(
    state_dir: Path, title: str, metric: str, goal: str, records: list[Record]
) -> Path:
    """Render the report for one branch's records and write it atomically."""
    path = state_dir / "report.html"
    partial = path.with_name("report.html.tmp")
    partial.write_text(render(state_dir, title, metric, goal, records), encoding="utf-8")
    partial.replace(path)
    return path


def render(state_dir: Path, title: str, metric: str, goal: str, records: list[Record]) -> str:
    body = [
        f"<h1>{escape(title)}</h1>",
        f'<p class="sub">{escape(goal)} <code>{escape(metric)}</code> · '
        f"refreshes every {REFRESH_SECONDS} s</p>",
        '<a class="skip" href="#runs">Skip to the runs table</a>',
    ]
    if records:
        body += [
            stats(metric, records),
            chart_card(metric, records),
            harness_card(records, goal),
            table(state_dir, metric, records),
        ]
    else:
        body.append('<p class="empty">No runs on this branch yet.</p>')
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
        f"<body><main>{''.join(body)}</main>"
        f'<div id="tip" role="tooltip" hidden></div><script>{SCRIPT}</script></body></html>\n'
    )


def fmt(value: float) -> str:
    return f"{value:.6g}"


def kept_values(records: Sequence[Record]) -> list[float]:
    return [r["metric"] for r in records if r["status"] == "keep" and r["metric"] is not None]


def stats(metric: str, records: list[Record]) -> str:
    kept = kept_values(records)
    counts = {status: sum(r["status"] == status for r in records) for status in STATUS_LABELS}
    tally = ", ".join(f"{n} {STATUS_LABELS[s][1]}" for s, n in counts.items() if n)
    tiles = [tile(f"Runs ({tally})", str(len(records)))]
    if kept:
        tiles.insert(0, tile(f"Best {metric}", fmt(kept[-1])))
        if kept[0] != 0 and len(kept) > 1:
            change = (kept[-1] - kept[0]) / abs(kept[0]) * 100
            tiles.insert(1, tile("Change vs baseline", f"{change:+.2f}%", "up"))
    return f'<div class="stats">{"".join(tiles)}</div>'


def tile(label: str, value: str, tone: str = "") -> str:
    return (
        f'<div class="stat"><div class="label">{escape(label)}</div>'
        f'<div class="value {tone}">{escape(value)}</div></div>'
    )


# Chart --------------------------------------------------------------------------------


def chart_card(metric: str, records: list[Record]) -> str:
    kept = kept_values(records)
    if not kept:
        return '<div class="card"><p class="empty">No baseline yet, so nothing to plot.</p></div>'
    return f'<div class="card">{legend()}{chart(metric, records, kept)}</div>'


def legend() -> str:
    def key(shape: str, label: str) -> str:
        return f'<li><svg width="18" height="12" aria-hidden="true">{shape}</svg>{label}</li>'

    return (
        '<ul class="legend">'
        + key('<line class="best" x1="1" y1="6" x2="17" y2="6"/>', "Running best")
        + key('<circle class="keep" cx="9" cy="6" r="5"/>', "Kept")
        + key('<circle class="discard" cx="9" cy="6" r="4"/>', "Discarded")
        + key(cross(9, 6), "Crashed or timed out (below the axis)")
        + "</ul>"
    )


def cross(x: float, y: float, size: float = 4) -> str:
    return (
        f'<path class="fail" d="M{x - size:.1f},{y - size:.1f}L{x + size:.1f},{y + size:.1f}'
        f'M{x - size:.1f},{y + size:.1f}L{x + size:.1f},{y - size:.1f}"/>'
    )


def domain(values: list[float]) -> tuple[float, float, bool]:
    """Y range around the kept runs (karpathy-style zoom), and whether to use a log scale."""
    lo, hi = min(values), max(values)
    log = lo > 0 and hi / lo > LOG_SCALE_RATIO
    if log:
        return lo / 1.3, hi * 1.3, True
    pad = (hi - lo) * 0.15 or abs(hi) * 0.05 or 1.0
    return lo - pad, hi + pad, False


def y_scale(lo: float, hi: float, log: bool) -> Callable[[float], float]:
    height = HEIGHT - TOP - BOTTOM

    def scale(value: float) -> float:
        value = min(max(value, lo), hi)
        if log:
            share = (math.log10(value) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
        else:
            share = (value - lo) / (hi - lo)
        return TOP + (1 - share) * height

    return scale


def x_scale(count: int) -> Callable[[int], float]:
    width = WIDTH - LEFT - RIGHT
    return lambda index: (
        LEFT + (index - 1) / max(count - 1, 1) * width + (width / 2 if count == 1 else 0)
    )


def nice_ticks(
    lo: float, hi: float, count: int = 5, steps: tuple[float, ...] = (1, 2, 2.5, 5, 10)
) -> list[float]:
    raw = (hi - lo) / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in steps if m * magnitude >= raw)
    first = math.ceil(lo / step)
    return [round(i * step, 12) for i in range(first, math.floor(hi / step) + 1)]


def log_ticks(lo: float, hi: float) -> list[float]:
    ticks = [10.0**e for e in range(math.ceil(math.log10(lo)), math.floor(math.log10(hi)) + 1)]
    if len(ticks) < MIN_LOG_TICKS:
        ticks = sorted(
            m * 10.0**e
            for e in range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1)
            for m in (1, 2, 5)
            if lo <= m * 10.0**e <= hi
        )
    return ticks


def axes(metric: str, count: int, lo: float, hi: float, log: bool) -> str:
    y = y_scale(lo, hi, log)
    x = x_scale(count)
    bottom = HEIGHT - BOTTOM
    parts = ['<g class="grid">']
    for value in log_ticks(lo, hi) if log else nice_ticks(lo, hi):
        parts.append(
            f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y(value):.1f}" y2="{y(value):.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{LEFT - 8}" y="{y(value) + 4:.1f}" text-anchor="end">'
            f"{fmt(value)}</text>"
        )
    parts.append("</g>")
    # Run numbers are integers, so only use steps of 1, 2, or 5 times a power of ten.
    for run in sorted({int(t) for t in nice_ticks(0, max(count, 2), 5, (1, 2, 5, 10)) if t >= 1}):
        parts.append(
            f'<text class="tick" x="{x(run):.1f}" y="{bottom + 16}" text-anchor="middle">'
            f"{run}</text>"
        )
    parts.append(
        f'<line class="axis" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{bottom}" y2="{bottom}"/>'
    )
    parts.append(
        f'<text class="axis-title" x="{(LEFT + WIDTH - RIGHT) / 2}" y="{HEIGHT - 6}" '
        f'text-anchor="middle">run</text>'
        f'<text class="axis-title" x="14" y="{TOP + (HEIGHT - TOP - BOTTOM) / 2}" '
        f'text-anchor="middle" transform="rotate(-90 14 {TOP + (HEIGHT - TOP - BOTTOM) / 2})">'
        f"{escape(metric)}{' (log scale)' if log else ''}</text>"
    )
    return "".join(parts)


def chart(metric: str, records: list[Record], kept: list[float]) -> str:
    lo, hi, log = domain(kept)
    x, y = x_scale(len(records)), y_scale(lo, hi, log)
    points = [point(index, r, metric, x, y) for index, r in enumerate(records, start=1)]
    return (
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="group" '
        f'aria-label="Progress of {escape(metric)} over {len(records)} runs">'
        f"{axes(metric, len(records), lo, hi, log)}{best_svg(records, x, y)}{''.join(points)}</svg>"
    )


def best_svg(records: list[Record], x: Callable[[int], float], y: Callable[[float], float]) -> str:
    """The running-best step line: flat until the next keep, then a step to its value."""
    d = ""
    for index, record in enumerate(records, start=1):
        if record["status"] != "keep" or record["metric"] is None:
            continue
        d += (
            f"M{x(index):.1f} {y(record['metric']):.1f}"
            if not d
            else (f"H{x(index):.1f}V{y(record['metric']):.1f}")
        )
    return f'<path class="best" d="{d}H{x(len(records)):.1f}"/>'


def point(
    index: int,
    record: Record,
    metric: str,
    x: Callable[[int], float],
    y: Callable[[float], float],
) -> str:
    label = STATUS_LABELS.get(record["status"], ("?", record["status"]))[1]
    value = record["metric"]
    tip = f"#{record['id']} {label} · "
    tip += f"{metric} {fmt(value)}" if value is not None else (record["reason"] or "no metric")
    tip += f" · {record['description']}"
    cx = x(index)
    if value is None:
        cy = HEIGHT - BOTTOM + RUG_OFFSET
        mark = cross(cx, cy)
    else:
        cy = y(value)
        css = "keep" if record["status"] == "keep" else "discard"
        mark = (
            f'<circle class="{css}" cx="{cx:.1f}" cy="{cy:.1f}" r="{5 if css == "keep" else 4}"/>'
        )
    return (
        f'<g class="pt" tabindex="0" role="img" aria-label="{escape(tip)}" '
        f'data-tip="{escape(tip)}">'
        f'<circle class="hit" cx="{cx:.1f}" cy="{cy:.1f}" r="12"/><g class="mark">{mark}</g></g>'
    )


# Recursive mode -----------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """How one window of runs went, as the evidence for a review of the research harness."""

    first: str
    last: str
    runs: int
    kept: int
    failed: int
    gain: float  # how much the best improved over the window, as a fraction of where it began
    seconds: float
    labs: tuple[str, ...]  # versions of lab/ that chose the runs, in order


def windows(records: Sequence[Record], goal: str, size: int) -> list[Window]:
    result = []
    best: float | None = None
    for start in range(0, len(records), size):
        chunk = records[start : start + size]
        began = best
        labs: dict[str, None] = {}
        for record in chunk:
            value = record["metric"]
            if record["status"] == "keep" and value is not None:
                began = value if began is None else began  # the baseline starts the first window
                best = value
            if "lab" in record:
                labs[record["lab"]] = None
        gain = 0.0
        if began and best is not None:  # a best of zero has no relative improvement
            gain = (began - best if goal == "minimize" else best - began) / abs(began)
        result.append(
            Window(
                first=chunk[0]["id"],
                last=chunk[-1]["id"],
                runs=len(chunk),
                kept=sum(r["status"] == "keep" for r in chunk),
                failed=sum(r["status"] in ("crash", "timeout") for r in chunk),
                gain=gain,
                seconds=sum(r["seconds"] for r in chunk),
                labs=tuple(labs),
            )
        )
    return result


def harness_card(records: list[Record], goal: str) -> str:
    if not any("lab" in record for record in records):
        return ""
    rows = []
    for window in windows(records, goal, WINDOW_RUNS):
        per_hour = f"{window.gain / (window.seconds / 3600):+.2%}" if window.seconds else ""
        labs = " ".join(
            f'<a href="lab-versions/{quote(lab)}/strategy.md"><code>{escape(lab[:7])}</code></a>'
            for lab in window.labs
        )
        rows.append(
            f"<tr><td>#{escape(window.first)} to #{escape(window.last)}</td>"
            f'<td class="num">{window.kept}/{window.runs}</td>'
            f'<td class="num">{window.failed}</td><td class="num">{window.gain:+.2%}</td>'
            f'<td class="num">{per_hour}</td><td>{labs}</td></tr>'
        )
    return (
        '<div class="card"><h2>Research harness</h2><div class="table-wrap"><table>'
        '<caption class="skip">Each window of runs and the research harness behind it</caption>'
        '<thead><tr><th>Runs</th><th class="num">Kept</th><th class="num">Failed</th>'
        '<th class="num">Gain</th><th class="num">Gain per compute-hour</th>'
        f"<th>Lab versions</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div>"
    )


# Table --------------------------------------------------------------------------------


def table(state_dir: Path, metric: str, records: list[Record]) -> str:
    kept = kept_values(records)
    baseline = kept[0] if kept else None
    rows = [row(state_dir, r, baseline) for r in reversed(records)]
    return (
        '<div class="card"><div class="table-wrap"><table id="runs">'
        f'<caption class="skip">All runs, newest first</caption><thead><tr><th>Run</th>'
        f'<th>Status</th><th class="num">{escape(metric)}</th><th class="num">vs baseline</th>'
        '<th class="num">Seconds</th><th>What was tried</th><th>Commit</th><th>Artifacts</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></div>"
    )


def row(state_dir: Path, record: Record, baseline: float | None) -> str:
    icon, label = STATUS_LABELS.get(record["status"], ("?", record["status"]))
    value = record["metric"]
    change = ""
    if value is not None and baseline:
        change = f"{(value - baseline) / abs(baseline) * 100:+.2f}%"
    reason = f'<span class="reason">{escape(record["reason"])}</span>' if record["reason"] else ""
    return (
        f"<tr><td>#{escape(record['id'])}</td>"
        f'<td class="status"><span class="icon-{escape(record["status"])}" aria-hidden="true">'
        f"{icon}</span> "
        f"{escape(label)}</td>"
        f'<td class="num">{fmt(value) if value is not None else "&ndash;"}</td>'
        f'<td class="num">{change}</td><td class="num">{record["seconds"]:g}</td>'
        f"<td>{escape(record['description'])}{reason}</td>"
        f"<td><code>{escape(record['commit'][:7])}</code></td>"
        f"<td>{artifacts(state_dir, record['id'])}</td></tr>"
    )


def artifacts(state_dir: Path, run_id: str) -> str:
    run_dir = state_dir / "runs" / run_id
    if not run_dir.is_dir():
        return ""
    base = f"runs/{quote(run_id)}/"
    images = sorted(p.name for p in run_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    thumbs = "".join(
        f'<a href="{escape(base + quote(name))}"><img src="{escape(base + quote(name))}" '
        f'alt="{escape(name)}" loading="lazy"></a>'
        for name in images
    )
    log = f'<a href="{escape(base)}run.log">log</a>' if (run_dir / "run.log").exists() else ""
    return f'<span class="thumbs">{thumbs}</span>{log}'
