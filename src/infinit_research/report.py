"""Self-contained HTML report for one branch: progress chart, runs table, artifacts."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from infinit_research.cli import Manifest, Probe, Record

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
REFRESH_SECONDS = 30
WINDOW_RUNS = 10  # the agent reviews its research harness after each window of runs
# Chart geometry in viewBox units; the SVG scales to its card, about 1:1 on a wide window.
WIDTH, HEIGHT = 1100, 300
LEFT, RIGHT, TOP, BOTTOM = 56, 16, 16, 48
LOG_SCALE_RATIO = 10  # switch to a log y axis when kept values span more than this
MIN_LOG_TICKS = 3
RUG_OFFSET = 34  # crashes have no metric, so they sit in a band under the x axis
STATUS_LABELS = {"keep": "kept", "discard": "discarded", "crash": "crashed", "timeout": "timed out"}

# shadcn/ui preset b2VmBIkQS: the Lyra style, zinc colors, fuchsia charts, and Geist.
STYLE = """
:root {
  color-scheme: light;
  --background: oklch(1 0 0); --foreground: oklch(0.141 0.005 285.823); --card: oklch(1 0 0);
  --muted: oklch(0.967 0.001 286.375); --muted-foreground: oklch(0.552 0.016 285.938);
  --primary: oklch(0.21 0.006 285.885); --primary-foreground: oklch(0.985 0 0);
  --destructive: oklch(0.577 0.245 27.325); --border: oklch(0.92 0.004 286.32);
  --chart: oklch(0.591 0.293 322.896); --neutral: oklch(0.65 0.012 286);
  --frame: color-mix(in oklab, var(--foreground) 10%, transparent);
  --destructive-tint: color-mix(in oklab, var(--destructive) 10%, transparent);
  --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --background: oklch(0.141 0.005 285.823); --foreground: oklch(0.985 0 0);
    --card: oklch(0.21 0.006 285.885); --muted: oklch(0.274 0.006 286.033);
    --muted-foreground: oklch(0.705 0.015 286.067);
    --primary: oklch(0.92 0.004 286.32); --primary-foreground: oklch(0.21 0.006 285.885);
    --destructive: oklch(0.704 0.191 22.216); --border: oklch(1 0 0 / 10%);
    --chart: oklch(0.667 0.295 322.15); --neutral: oklch(0.552 0.016 285.938);
    --destructive-tint: color-mix(in oklab, var(--destructive) 20%, transparent);
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--background); color: var(--foreground);
  font: 12px/1.625 Geist, "Geist Variable", ui-sans-serif, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased; }
main { max-width: 1200px; margin: 0 auto; padding: 48px 24px 64px;
  display: flex; flex-direction: column; gap: 16px; }
header { margin-bottom: 16px; }
h1 { font-size: 16px; line-height: 24px; font-weight: 500; margin: 0; }
h2 { font-size: 14px; line-height: 20px; font-weight: 500; margin: 0; }
.sub { color: var(--muted-foreground); margin: 2px 0 0; }
code { font-family: var(--mono); font-size: 11px; }
.sub code { color: var(--foreground); background: var(--muted); padding: 2px 4px; }
a { color: inherit; text-underline-offset: 4px; text-decoration-color: var(--border); }
a:hover { text-decoration-color: currentColor; }
.skip { position: absolute; left: -9999px; }
.skip:focus { left: 16px; top: 8px; background: var(--card); padding: 4px 8px; }
.stats { display: flex; flex-wrap: wrap; gap: 1px; background: var(--frame);
  box-shadow: 0 0 0 1px var(--frame); }
.stat { flex: 1 1 200px; padding: 16px; background: var(--card); }
.stat .label, .stat .note { color: var(--muted-foreground); }
.stat .value { font-size: 24px; line-height: 32px; font-weight: 500; letter-spacing: -0.02em;
  margin-top: 4px; }
.card { display: flex; flex-direction: column; gap: 16px; padding: 16px 0; overflow: hidden;
  background: var(--card); box-shadow: 0 0 0 1px var(--frame); }
.card-header { display: flex; flex-wrap: wrap; align-items: flex-start;
  justify-content: space-between; gap: 4px 16px; padding: 0 16px; }
.card-header p { color: var(--muted-foreground); margin: 4px 0 0; }
.card-content { padding: 0 16px; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 0; padding: 0;
  list-style: none; color: var(--muted-foreground); }
.legend li { display: flex; align-items: center; gap: 6px; }
/* --k is viewBox units per pixel, set by the script so text and marks keep their pixel size */
svg.chart { width: 100%; height: auto; display: block; overflow: visible; }
.grid line, .axis { stroke: var(--border); stroke-width: 1; }
.grid line, .axis, .best { vector-effect: non-scaling-stroke; }
.tick { fill: var(--muted-foreground); font-size: calc(12px * var(--k, 1));
  font-variant-numeric: tabular-nums; }
.best { fill: none; stroke: var(--chart); stroke-width: 2;
  stroke-linejoin: round; stroke-linecap: round; }
.pt { cursor: default; outline: none; }
.pt .hit { fill: transparent; }
.pt .mark, .pt .hit { transform-box: fill-box; transform-origin: center;
  transform: scale(var(--k, 1)); transition: transform 80ms; }
.pt:hover .mark, .pt:focus-visible .mark { transform: scale(calc(1.4 * var(--k, 1))); }
circle.keep { fill: var(--chart); stroke: var(--card); stroke-width: 2; }
circle.discard { fill: var(--neutral); stroke: var(--card); stroke-width: 2; }
.fail { stroke: var(--destructive); stroke-width: 2; stroke-linecap: round; }
#tip { position: fixed; pointer-events: none; min-width: 128px; max-width: 320px;
  padding: 6px 10px; background: var(--background); color: var(--foreground);
  border: 1px solid var(--border); line-height: 1.5; white-space: pre-line;
  box-shadow: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1); }
#tip::first-line { font-weight: 500; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border);
  vertical-align: middle; white-space: nowrap; }
th { height: 40px; font-weight: 500; }
th:first-child, td:first-child { padding-left: 16px; }
th:last-child, td:last-child { padding-right: 16px; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover { background: color-mix(in oklab, var(--muted) 50%, transparent); }
.num { font-variant-numeric: tabular-nums; text-align: right; }
.desc { white-space: normal; min-width: 240px; }
.reason { color: var(--muted-foreground); display: block; }
.badge { display: inline-flex; align-items: center; height: 20px; padding: 0 8px;
  border: 1px solid var(--border); font-weight: 500; white-space: nowrap; }
.badge.keep { background: var(--primary); color: var(--primary-foreground);
  border-color: transparent; }
.badge.discard { color: var(--muted-foreground); }
.badge.crash, .badge.timeout { background: var(--destructive-tint); color: var(--destructive);
  border-color: transparent; }
.thumbs { display: inline-flex; gap: 4px; margin-right: 8px; vertical-align: middle; }
.thumbs:empty { display: none; }
.thumbs img { display: block; height: 28px; box-shadow: 0 0 0 1px var(--frame); }
.empty { color: var(--muted-foreground); text-align: center; margin: 0; padding: 48px 24px;
  border: 1px dashed var(--border); }
.card .empty { border: 0; padding: 24px; }
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
const chart = document.querySelector('svg.chart');
if (chart) {
  new ResizeObserver(() => {
    const width = chart.getBoundingClientRect().width;
    if (width) chart.style.setProperty('--k', chart.viewBox.baseVal.width / width);
  }).observe(chart);
}
"""


def write_report(
    state_dir: Path, title: str, manifest: Manifest, records: list[Record], probes: list[Probe]
) -> Path:
    """Render the report for one branch's records and write it atomically."""
    path = state_dir / "report.html"
    partial = path.with_name("report.html.tmp")
    partial.write_text(render(state_dir, title, manifest, records, probes), encoding="utf-8")
    partial.replace(path)
    return path


def render(
    state_dir: Path, title: str, manifest: Manifest, records: list[Record], probes: list[Probe]
) -> str:
    """The report page, showing only the probes on the records' branch."""
    metric, goal = manifest.metric, manifest.goal
    body = [
        f'<header><h1>{escape(title)}</h1><p class="sub">{escape(goal.capitalize())} '
        f"<code>{escape(metric)}</code> · refreshes every {REFRESH_SECONDS} s</p></header>",
        '<a class="skip" href="#runs">Skip to the runs table</a>',
    ]
    if records:
        branch = records[-1]["branch"]
        probes = [probe for probe in probes if probe["branch"] == branch]
        body += [
            stats(metric, records),
            chart_card(metric, goal, records),
            harness_card(records, probes, goal),
            probes_card(probes),
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
    tally = " · ".join(f"{n} {STATUS_LABELS[s]}" for s, n in counts.items() if n)
    tiles = [tile("Runs", str(len(records)), tally)]
    if kept:
        tiles.insert(0, tile(f"Best {metric}", fmt(kept[-1])))
        if kept[0] != 0 and len(kept) > 1:
            change = (kept[-1] - kept[0]) / abs(kept[0]) * 100
            tiles.insert(1, tile("Change vs baseline", f"{change:+.2f}%", f"from {fmt(kept[0])}"))
    return f'<div class="stats">{"".join(tiles)}</div>'


def tile(label: str, value: str, note: str = "") -> str:
    return (
        f'<div class="stat"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div><div class="note">{escape(note)}</div></div>'
    )


def card(title: str, description: str, content: str, action: str = "") -> str:
    return (
        f'<div class="card"><div class="card-header"><div><h2>{escape(title)}</h2>'
        f"<p>{escape(description)}</p></div>{action}</div>{content}</div>"
    )


def badge(status: str) -> str:
    label = STATUS_LABELS.get(status, status)
    return f'<span class="badge {escape(status)}">{escape(label)}</span>'


# Chart --------------------------------------------------------------------------------


def chart_card(metric: str, goal: str, records: list[Record]) -> str:
    kept = kept_values(records)
    scale = " (log scale)" if kept and domain(kept)[2] else ""
    better = "lower" if goal == "minimize" else "higher"
    description = f"{metric} by run{scale}, {better} is better"
    if not kept:
        return card(
            "Progress", description, '<p class="empty">No baseline yet, so nothing to plot.</p>'
        )
    content = f'<div class="card-content">{chart(metric, records, kept)}</div>'
    return card("Progress", description, content, legend())


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


def axes(count: int, lo: float, hi: float, log: bool) -> str:
    y = y_scale(lo, hi, log)
    x = x_scale(count)
    bottom = HEIGHT - BOTTOM
    parts = ['<g class="grid">']
    for value in log_ticks(lo, hi) if log else nice_ticks(lo, hi):
        parts.append(
            f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y(value):.1f}" y2="{y(value):.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{LEFT - 8}" y="{y(value):.1f}" dy="0.35em" text-anchor="end">'
            f"{fmt(value)}</text>"
        )
    parts.append("</g>")
    # Run numbers are integers, so only use steps of 1, 2, or 5 times a power of ten.
    for run in sorted({int(t) for t in nice_ticks(0, max(count, 2), 5, (1, 2, 5, 10)) if t >= 1}):
        parts.append(
            f'<text class="tick" x="{x(run):.1f}" y="{bottom}" dy="1.3em" text-anchor="middle">'
            f"{run}</text>"
        )
    parts.append(
        f'<line class="axis" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{bottom}" y2="{bottom}"/>'
    )
    return "".join(parts)


def chart(metric: str, records: list[Record], kept: list[float]) -> str:
    lo, hi, log = domain(kept)
    x, y = x_scale(len(records)), y_scale(lo, hi, log)
    points = [point(index, r, metric, x, y) for index, r in enumerate(records, start=1)]
    return (
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="group" '
        f'aria-label="Progress of {escape(metric)} over {len(records)} runs">'
        f"{axes(len(records), lo, hi, log)}{best_svg(records, x, y)}{''.join(points)}</svg>"
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
    label = STATUS_LABELS.get(record["status"], record["status"])
    value = record["metric"]
    tip = f"#{record['id']} {label}\n"  # the tooltip shows one line per part
    tip += f"{metric} {fmt(value)}" if value is not None else (record["reason"] or "no metric")
    tip += f"\n{record['description']}"
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


# Research harness ---------------------------------------------------------------------

SHOWN_PROBES = 20


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
    probes: int  # the agent's own evaluations, run while it chose this window's runs
    probe_seconds: float
    labs: tuple[str, ...]  # versions of lab/ that chose the runs, in order


def windows(
    records: Sequence[Record], probes: Sequence[Probe], goal: str, size: int
) -> list[Window]:
    result = []
    best: float | None = None
    for index, start in enumerate(range(0, len(records), size)):
        chunk = records[start : start + size]
        began = best
        for record in chunk:
            value = record["metric"]
            if record["status"] == "keep" and value is not None:
                began = value if began is None else began  # the baseline starts the first window
                best = value
        mine = [probe for probe in probes if probe["runs"] // size == index]
        result.append(
            Window(
                first=chunk[0]["id"],
                last=chunk[-1]["id"],
                runs=len(chunk),
                kept=sum(r["status"] == "keep" for r in chunk),
                failed=sum(r["status"] in ("crash", "timeout") for r in chunk),
                gain=relative_gain(began, best, goal),
                seconds=sum(r["seconds"] for r in chunk),
                probes=len(mine),
                probe_seconds=sum(probe["seconds"] for probe in mine),
                labs=tuple(dict.fromkeys(r["lab"] for r in chunk)),
            )
        )
    return result


def relative_gain(began: float | None, best: float | None, goal: str) -> float:
    if not began or best is None:  # no baseline yet, or a zero to measure from
        return 0.0
    return (began - best if goal == "minimize" else best - began) / abs(began)


def harness_card(records: list[Record], probes: list[Probe], goal: str) -> str:
    rows = []
    for window in windows(records, probes, goal, WINDOW_RUNS):
        hours = (window.seconds + window.probe_seconds) / 3600
        per_hour = f"{window.gain / hours:+.2%}" if hours else ""
        labs = " ".join(
            f'<a href="lab-versions/{quote(lab)}/strategy.md"><code>{escape(lab[:7])}</code></a>'
            for lab in window.labs
        )
        rows.append(
            f"<tr><td>#{escape(window.first)} to #{escape(window.last)}</td>"
            f'<td class="num">{window.kept}/{window.runs}</td>'
            f'<td class="num">{window.failed}</td><td class="num">{window.probes}</td>'
            f'<td class="num">{window.gain:+.2%}</td><td class="num">{per_hour}</td>'
            f"<td>{labs}</td></tr>"
        )
    return card(
        "Research harness",
        f"Each window of {WINDOW_RUNS} runs and the version of lab/ that chose them",
        '<div class="table-wrap"><table>'
        '<caption class="skip">Each window of runs and the research harness behind it</caption>'
        '<thead><tr><th>Runs</th><th class="num">Kept</th><th class="num">Failed</th>'
        '<th class="num">Probes</th><th class="num">Gain</th>'
        '<th class="num">Gain per compute-hour</th><th>Lab versions</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>",
    )


def probes_card(probes: list[Probe]) -> str:
    if not probes:
        return ""
    rows = []
    for probe in reversed(probes[-SHOWN_PROBES:]):
        results = " ".join(f"{key}={fmt(value)}" for key, value in probe["metrics"].items())
        reason = f'<span class="reason">{escape(probe["reason"])}</span>' if probe["reason"] else ""
        log = f"probes/{quote(probe['id'])}/run.log"
        rows.append(
            f"<tr><td>#{escape(probe['id'])}</td><td>{badge(probe['status'])}</td>"
            f"<td><code>{escape(results)}</code></td>"
            f'<td class="num">{probe["seconds"]:g}</td>'
            f'<td class="desc">{escape(probe["description"])}{reason}</td>'
            f"<td><code>{escape(probe['lab'][:7])}</code></td>"
            f'<td><a href="{escape(log)}">log</a></td></tr>'
        )
    return card(
        "Probes",
        "The agent's own evaluations, newest first",
        '<div class="table-wrap"><table>'
        '<caption class="skip">The agent\'s own evaluations, newest first</caption>'
        '<thead><tr><th>Probe</th><th>Status</th><th>Results</th><th class="num">Seconds</th>'
        "<th>What it checked</th><th>Lab</th><th>Log</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>",
    )


# Table --------------------------------------------------------------------------------


def table(state_dir: Path, metric: str, records: list[Record]) -> str:
    kept = kept_values(records)
    baseline = kept[0] if kept else None
    rows = [row(state_dir, r, baseline) for r in reversed(records)]
    return card(
        "Runs",
        "All runs, newest first",
        '<div class="table-wrap"><table id="runs">'
        '<caption class="skip">All runs, newest first</caption><thead><tr><th>Run</th>'
        f'<th>Status</th><th class="num">{escape(metric)}</th><th class="num">vs baseline</th>'
        '<th class="num">Seconds</th><th>What was tried</th><th>Commit</th><th>Artifacts</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>",
    )


def row(state_dir: Path, record: Record, baseline: float | None) -> str:
    value = record["metric"]
    change = ""
    if value is not None and baseline:
        change = f"{(value - baseline) / abs(baseline) * 100:+.2f}%"
    reason = f'<span class="reason">{escape(record["reason"])}</span>' if record["reason"] else ""
    return (
        f"<tr><td>#{escape(record['id'])}</td><td>{badge(record['status'])}</td>"
        f'<td class="num">{fmt(value) if value is not None else "&ndash;"}</td>'
        f'<td class="num">{change}</td><td class="num">{record["seconds"]:g}</td>'
        f'<td class="desc">{escape(record["description"])}{reason}</td>'
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
