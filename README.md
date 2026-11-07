# homelab-heartbeat

A dead-man's switch for homelab alerting. **This repo alarms on silence, not on signal.**

## Why it exists

On 2026-06-24 the n8n 1.x → 2.x upgrade blocked `$env` access inside Code nodes.
`Cluster Service Health Watcher` — the thing that posted alerts to Discord — began
failing on every run. It logged **4061 errors and zero successes**. Nobody was told,
because the thing that would have told us was the thing that broke.

It was discovered sixteen days later, by a human remembering that he used to get
Discord messages and had stopped.

At the same time, Alertmanager was routing every alert it raised — including four
criticals — to a receiver literally named `"null"`. It had done so since install.

Two alerting systems. Neither had ever delivered an alert on its own behalf. The
failure was invisible in both because **nothing watched the watchers**, and a watcher
that never fires is indistinguishable from a watcher with nothing to report.

## How it works

Two stamps, watching two **different** chains. This distinction is the whole point:

```
heartbeat.json  — proves n8n is EXECUTING
  n8n schedule trigger (hourly) → $env read → GitHub App token → stamp

watchdog.json   — proves an alert can LEAVE THE CLUSTER
  Prometheus (Watchdog alert, always firing by design)
    → Alertmanager (delivers it)
      → n8n webhook
        → GitHub App token → stamp

dead-man (GitHub Actions, cron 37 * * * *, ubuntu-latest)
  ├─ heartbeat.json older than 180 min?  →  FAILS  →  GitHub emails you (every failed run)
  └─ watchdog.json  older than  60 min?  →  FAILS  →  GitHub emails you (every failed run)
```

**The cron is a request, not a guarantee.** GitHub delays scheduled runs under load
and drops the ones it can't fit, and on this account it creates scheduled runs only
about five times a day however often the cron asks (homelab-heartbeat#10,
homelab#1906). Measured gaps between consecutive runs:

| cron | runs/day | gap p50 / p95 / max |
|---|---|---|
| `0 */6` (Jul–Oct 2026) | 3.8 | 6.0 / 9.6 / 14.0 h |
| `25`/`35 */6`, other repos (Oct) | 3.3 | 7.2 / 9.6 / 13.6 h |
| `17 * * * *`, other repos (Oct) | 4.8 | 5.1 / 7.5 / 10.5 h |

Moving off minute 0 did not help: off-peak `*/6` crons run as late as minute-0 ones.
Asking hourly does: it turns the same scheduler into more runs and a shorter worst
gap, so the cron is `37 * * * *`. It is still GitHub-scheduled on purpose. No trigger
on the cluster (n8n included) may be part of the dead-man, because the dead-man is the
observer of the cluster and must keep working when the cluster is compromised or gone.

The two thresholds measure *stamp age* and do not depend on run spacing. Spacing
decides only how long after a stamp goes stale someone gets the email: up to the
threshold plus the gap to the next run that actually executes. Every run writes its
trigger delay (start minus nominal slot) to the step summary. At an hourly cron that
delay wraps at 60 min, so use the interval between runs in the run history as the
real measure.

### What a long outage looks like in your inbox

**One email per failed run. GitHub does not deduplicate them.** Verified 2026-11-07
with two trip-test runs two minutes apart (`max_age_minutes=1`, runs
[42335995316](https://github.com/dvystrcil/homelab-heartbeat/actions/runs/42335995316)
and [42336143945](https://github.com/dvystrcil/homelab-heartbeat/actions/runs/42336143945)):
the second failed straight after the first, with no pass between, and still sent its
own email. The scheduled failures on 2026-10-25, 10-26 and 10-27 each sent one too.
Each email arrives within about a minute, with the subject
`[dvystrcil/homelab-heartbeat] Run failed: dead-man - main (<short sha>)`.

**Who gets them.** GitHub's docs say notifications for scheduled workflows go "to the
user who last modified the cron syntax in the workflow file", or to whoever last
re-enabled a disabled workflow
([Events that trigger workflows § schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[Notifications for workflow runs](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)).
That is whoever merges the next cron change, so a cron edit also moves the alarm to a
new inbox. The recipient also needs Actions notifications set to *Email* (Settings →
Notifications → System → Actions), or GitHub sends nothing at all.

**Volume.** One email arrives per run GitHub actually creates, not one per cron slot.
At about 5 runs a day, an outage sends one email every few hours: a 10-hour outage
sends roughly 2–3, and a full day about 5. The ceiling is 24 a day, if GitHub ever ran
every hourly slot. Gmail groups emails with the same subject into one conversation,
and the subject carries the `main` SHA. When the stamps stop committing, that SHA
freezes and every email after the first stacks in one thread, so count the messages
inside the thread, not the threads.

**Not verified.** Two failures in a row as *scheduled* runs on the *same* SHA. The
trip-test runs were manual and on different SHAs. The earlier back-to-back outages
(2026-08-01, 2026-09-06) left no email in the inbox today, but they are past Gmail's
30-day Trash purge, so they can't tell "never sent" from "deleted". Don't delete
dead-man emails: they are the evidence for the next check.

`watchdog.json` exists because `heartbeat.json` alone was never enough, though this
README previously claimed it was. The canary reads `$env` but **never posts to
Discord and never touches Alertmanager** — `alerting_canary.json` has a single
commit and `git log -S 'discord'` on it returns nothing. Prometheus and Alertmanager
were nowhere in its chain, so both could die with `heartbeat.json` staying perfectly
fresh (dvystrcil/homelab#431).

| what broke | `heartbeat.json` | `watchdog.json` | dead-man |
|---|---|---|---|
| n8n `$env` regresses again | stops | stops | fails |
| n8n scheduler wedged (n8n-workflow#89) | stops | — | fails |
| n8n down, or its App token expired | stops | stops | fails |
| **Prometheus down / not evaluating** | *keeps stamping* | **stops** | fails |
| **Alertmanager down or misrouted to `null`** | *keeps stamping* | **stops** | fails |
| whole cluster down | stops | stops | fails |
| power/ISP out | stops | stops | fails |
| **dead-man itself is broken** | — | — | *see below* |

The two rows in bold are exactly what `heartbeat.json` could never see, and are the
reason this repo needed a second stamp.

### Still not covered

**Discord delivery itself.** Neither stamp proves a message reached Discord;
`watchdog.json` proves Alertmanager *delivered a notification to n8n*, which is a
different receiver on the same alert. Discord health is currently evidenced only by
`alertmanager_notifications_total{integration="discord"}` (905 sent, 4 failed as of
2026-08-19). Closing that gap needs a canary that posts to a low-noise `#canary`
channel — homelab#431 AC5/AC6. Written down rather than left implied, because an
uncovered gap that *looks* covered is how this repo came to exist.

## Design constraints (each one is load-bearing)

**Runs on `ubuntu-latest`, never on the ARC runner.** Everywhere else in this homelab
the in-cluster runner is preferred. Here it would be fatal: a dead cluster means no
runner, no job, no failure — and silence would read as health. The watcher must not
share a failure domain with the watched.

**Heartbeat commits keep the schedule alive.** GitHub disables scheduled workflows
after 60 days of repository inactivity. A dead-man that quietly switches itself off is
worse than none. Because the canary commits here hourly, this repo is never inactive.
The mechanism defends itself.

**The notification path is GitHub's own email**, not Discord. Discord may be exactly
what is broken. The alarm must not route through anything this system builds.

**Every failure mode is fail-closed.** A dead App token, an unparseable file, a missing
file, an expired credential — all produce a red workflow. There is deliberately no
"skip if not configured" escape hatch, because that is the exact shape of the bug this
repo exists to catch.

## Current status

| stamp | state |
|---|---|
| `heartbeat.json` | **live.** Stamped hourly by `Alerting Canary — heartbeat stamp`; the `schedule` cron is enabled and passing. |
| `watchdog.json` | **seeded, arming.** Stamped once `dvystrcil/prometheus` routes `Watchdog` away from `null` to the receiver in dvystrcil/n8n-workflow#164. |

While `watchdog.json` holds its seed the dead-man **warns but does not fail**, so an
in-progress rollout cannot cry wolf every six hours — an alarm that fires before it
has ever told the truth gets muted.

That leniency is time-boxed. The seed carries an `arm_after` timestamp, and past it
the seed escalates to a hard failure: *armed, but no bird*. A warning nobody reads is
the precise bug this repo exists to catch, so the grace period expires by itself
rather than depending on anyone remembering.

## Who watches the dead-man?

Nobody, and that is deliberate — the recursion has to stop somewhere. It stops here
because this layer has the fewest moving parts (one cron, one file, one comparison),
runs on infrastructure we do not operate, and reports through a channel we did not
build. If GitHub Actions and GitHub email are both down, you will find out from the
internet.

## Refs

- [homelab#431](https://github.com/dvystrcil/homelab/issues/431) — the canary design
- [n8n-workflow#87](https://github.com/dvystrcil/n8n-workflow/issues/87) — the `$env` outage
- [n8n-workflow#88](https://github.com/dvystrcil/n8n-workflow/pull/88) — alerting restored
- [n8n-workflow#164](https://github.com/dvystrcil/n8n-workflow/pull/164) — the `watchdog.json` receiver
