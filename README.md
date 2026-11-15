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

dead-man (GitHub Actions, cron 37 * * * *, ubuntu-latest)   — the backstop and the history
  ├─ heartbeat.json older than 180 min?  →  FAILS  →  GitHub emails you (every failed run)
  └─ watchdog.json  older than  60 min?  →  FAILS  →  GitHub emails you (every failed run)

healthchecks.io (Hobbyist, $0, its own clock)               — the fast alarm
  ├─ watchdog-alert-path  ← Alertmanager pings it with every Watchdog delivery   (15 min + 20 grace)
  ├─ n8n-heartbeat        ← the canary pings it after each successful stamp       (1 h + 30 grace)
  └─ dead-man-runs        ← each PASSING dead-man run pings it                    (12 h + 2 h grace)
        a ping that stops arriving  →  healthchecks.io emails you
```

The two layers fail differently, which is why both exist. healthchecks.io notices in
minutes but keeps only a short ping log. GitHub notices in hours, but every stamp is a
commit, and homelab#1843 builds the outage history from those commits. Either one
alone still alerts if the other is down (homelab-heartbeat#20).

**The cron is a request, not a guarantee.** GitHub delays scheduled runs under load
and drops the ones it can't fit, and on this account it creates scheduled runs only
about five times a day however often the cron asks (homelab-heartbeat#10,
homelab#1906). Measured gaps between consecutive runs:

| cron | runs/day | gap p50 / p95 / max |
|---|---|---|
| `0 */6` (Jul–Oct 2026) | 3.8 | 6.0 / 9.6 / 14.0 h |
| `25`/`35 */6`, other repos (Oct) | 3.3 | 7.2 / 9.6 / 13.6 h |
| `17 * * * *`, other repos (Oct) | 4.8 | 5.1 / 7.5 / 10.5 h |
| **`37 * * * *`, this repo (Nov 2–14 2026, 46 runs)** | 3.6 | 6.9 / 9.1 / **11.4 h** |

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

### Worst-case detection latency

A stamp has to go stale first, and then a run has to actually execute. The worst
case is a run that checks just before the threshold is crossed, followed by the
longest gap GitHub leaves before the next run (11.4 h measured, above):

| stamp | threshold | + worst gap | email at most this long after the last good stamp |
|---|---|---|---|
| `watchdog.json` (alert path) | 60 min | 11.4 h | **~12.5 h** |
| `heartbeat.json` (n8n executing) | 180 min | 11.4 h | **~14.5 h** |

A typical gap is 6.9 h (p50), which puts the watchdog leg at about 8 h, but plan
around the worst case: about 12–14 h, not the 1 h or 6 h the thresholds and the old
cron suggest. An outage shorter than the gap between two runs can open
and close without either run seeing it (it happened on 2026-10-25, dvystrcil/homelab#1802), and
a point-sample alarm can never catch that. The gap history lives in dvystrcil/homelab#1843.

That is the GitHub layer's bound, and it was accepted (homelab-heartbeat#10,
2026-11-14): GitHub's scheduler can't deliver less, and every faster trigger considered
either ran on the cluster, needed a dispatch credential, or broke GitHub's Actions
terms by holding a hosted runner for hours. The bound came down instead with a second
off-cluster observer that has its own clock (homelab-heartbeat#20):

| healthchecks.io check | pinged by | period + grace | email after the pings stop |
|---|---|---|---|
| `watchdog-alert-path` | Alertmanager, every Watchdog delivery (~15–20 min) | 15 min + 20 min | **~35 min** |
| `n8n-heartbeat` | the canary, after each successful stamp | 1 h + 30 min | **~90 min** |
| `dead-man-runs` | each passing dead-man run | 12 h + 2 h | **~14 h** (watches the dead-man itself) |

The pings carry no state of their own. A ping URL is a credential (whoever holds it
can keep a check green), so the URLs live in Infisical and in this repo's Actions
secrets, never in git.

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

| what broke | `heartbeat.json` | `watchdog.json` | dead-man | healthchecks.io |
|---|---|---|---|---|
| n8n `$env` regresses again | stops | stops | fails | `n8n-heartbeat` |
| n8n scheduler wedged (n8n-workflow#89) | stops | — | fails | `n8n-heartbeat` |
| n8n down, or its App token expired | stops | stops | fails | `n8n-heartbeat` |
| **Prometheus down / not evaluating** | *keeps stamping* | **stops** | fails | `watchdog-alert-path` |
| **Alertmanager down or misrouted to `null`** | *keeps stamping* | **stops** | fails | `watchdog-alert-path` |
| whole cluster down | stops | stops | fails | both cluster checks |
| power/ISP out | stops | stops | fails | both cluster checks |
| **dead-man itself is broken** (disabled, failing, never scheduled) | — | — | — | `dead-man-runs` |

The Watchdog's healthchecks.io ping comes straight from Alertmanager, not through n8n,
so an n8n outage turns `watchdog.json` stale but leaves `watchdog-alert-path` green.
That is correct: `n8n-heartbeat` is the check that catches n8n.

The two rows in bold are exactly what `heartbeat.json` could never see, and are the
reason this repo needed a second stamp.

In the dead-man column, "fails" means the email arrives within the GitHub layer's
[worst-case detection latency](#worst-case-detection-latency): up to ~12.5 h for a row
the watchdog leg catches, ~14.5 h for one only `heartbeat.json` catches. The
healthchecks.io column is the one that arrives in minutes (~35 min alert path, ~90 min
n8n).

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

**The notification paths are GitHub's and healthchecks.io's own email**, not Discord.
Discord may be exactly what is broken. The alarm must not route through anything this
system builds.

**Every failure mode is fail-closed.** A dead App token, an unparseable file, a missing
file, an expired credential — all produce a red workflow. There is deliberately no
"skip if not configured" escape hatch, because that is the exact shape of the bug this
repo exists to catch.

## Current status

| stamp | state |
|---|---|
| `heartbeat.json` | **live.** Stamped hourly by `Alerting Canary — heartbeat stamp`; the `schedule` cron is enabled and passing. |
| `watchdog.json` | **live.** Stamped every ~20 min by Alertmanager's `Watchdog` alert through the `monitoring/discord/watchdog-deadman` receiver (dvystrcil/n8n-workflow#164). |
| healthchecks.io | **live.** `watchdog-alert-path` (dvystrcil/prometheus#99), `n8n-heartbeat` (dvystrcil/n8n-workflow#375), `dead-man-runs` (this repo). 3 of the Hobbyist plan's 20 checks. |

While `watchdog.json` holds its seed the dead-man **warns but does not fail**, so an
in-progress rollout cannot cry wolf on every run — an alarm that fires before it
has ever told the truth gets muted.

That leniency is time-boxed. The seed carries an `arm_after` timestamp, and past it
the seed escalates to a hard failure: *armed, but no bird*. A warning nobody reads is
the precise bug this repo exists to catch, so the grace period expires by itself
rather than depending on anyone remembering.

## Who watches the dead-man?

healthchecks.io's `dead-man-runs` check. Every run that passes pings it as its last
step, so a dead-man that stops running (disabled, never scheduled) or keeps failing goes
silent there, and healthchecks.io emails within ~14 h. The step has no `if:`, so a run
whose checks failed never pings and can't keep the check green. It is also
`continue-on-error`, so an unreachable healthchecks.io can't turn a healthy run red and
send a false "canary dead" email (tests/test_dead_man_ping.py). A missing secret fails
closed the same way: no ping, so the check alerts.

The recursion stops at healthchecks.io itself. Nothing on our side watches it, and
nothing should: it runs on infrastructure we do not operate and reports through a
channel we did not build. If it and GitHub are both down at once, you will find out
from the internet.

## Refs

- [homelab#431](https://github.com/dvystrcil/homelab/issues/431) — the canary design
- [n8n-workflow#87](https://github.com/dvystrcil/n8n-workflow/issues/87) — the `$env` outage
- [n8n-workflow#88](https://github.com/dvystrcil/n8n-workflow/pull/88) — alerting restored
- [n8n-workflow#164](https://github.com/dvystrcil/n8n-workflow/pull/164) — the `watchdog.json` receiver
