"""Record how late GitHub started this scheduled run (homelab-heartbeat#10 AC2).

GitHub delays and drops `schedule` events under load, so "cron every 6 h" does
not mean "checked every 6 h". This writes `delay = now - nominal cron slot` to
the step summary on every run, making the scheduler's lateness re-measurable
from run history instead of a one-off analysis.

Telemetry only: it never exits non-zero for a bad input. The dead-man's verdict
belongs to the freshness check, not to this.

Limitation: the slot is the MOST RECENT one at or before now. A run more than
one cadence late is attributed to the next slot and under-reports its delay;
the interval between consecutive runs is the number that catches that.
"""
import os
import sys
from datetime import datetime, timedelta, timezone


def _field(spec, lo, hi):
    """Values a cron minute/hour field matches. Supports N, *, */N, a-b, lists."""
    out = set()
    for part in spec.split(","):
        base, _, step = part.partition("/")
        step = int(step) if step else 1
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            start, end = (int(x) for x in base.split("-"))
        else:
            start = end = int(base)
        if not (lo <= start <= end <= hi) or step < 1:
            raise ValueError(f"cron field {spec!r} out of range {lo}-{hi}")
        out.update(range(start, end + 1, step))
    return out


def nominal_slot(cron, now):
    """Latest UTC time <= now that `cron` fires at (minute/hour fields only)."""
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 cron fields, got {cron!r}")
    if fields[2:] != ["*", "*", "*"]:
        # Ignoring these would silently report a wrong slot.
        raise ValueError(f"day/month/weekday fields unsupported: {cron!r}")
    minutes = _field(fields[0], 0, 59)
    hours = _field(fields[1], 0, 23)

    t = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    for _ in range(24 * 60 + 1):
        if t.minute in minutes and t.hour in hours:
            return t
        t -= timedelta(minutes=1)
    raise ValueError(f"cron {cron!r} never fires")


def render(event_name, cron, now):
    """Step-summary lines for this run."""
    lines = ["## trigger delay", ""]
    if event_name != "schedule":
        lines.append(f"Event `{event_name}`: not a scheduled run, delay n/a.")
        return lines + [""]
    try:
        slot = nominal_slot(cron, now)
    except ValueError as e:
        print(f"::warning::trigger delay: {e}")
        return lines + [f"Cron `{cron}`: could not compute a delay ({e}).", ""]
    delay = (now - slot).total_seconds() / 60
    return lines + [
        f"- cron: `{cron}`",
        f"- nominal slot: `{slot:%Y-%m-%dT%H:%M:%SZ}`",
        f"- started: `{now:%Y-%m-%dT%H:%M:%SZ}`",
        f"- delay: **{delay:.1f} min**",
        "",
        "_Measured against the most recent slot; a run more than one cadence late"
        " reads as the next slot. Compare run intervals too._",
        "",
    ]


def main():
    lines = render(os.environ.get("GITHUB_EVENT_NAME", ""),
                   os.environ.get("SCHEDULE", ""),
                   datetime.now(timezone.utc))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
