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

```
in-cluster canary (n8n, hourly)
  ├─ exercises the REAL alert path ($env → Discord webhook)
  └─ only on success: stamps heartbeat.json in this repo
                      (GitHub App installation token, auto-rotated hourly)

dead-man (GitHub Actions, cron */6h, ubuntu-latest)
  ├─ reads heartbeat.json
  └─ stamped_at older than 180 min?  →  workflow FAILS  →  GitHub emails you
```

Every failure collapses to the same visible outcome:

| what broke | heartbeat | dead-man |
|---|---|---|
| `$env` regresses again | stops | fails |
| Discord webhook rotated/revoked | stops | fails |
| n8n down, or its App token expired | stops | fails |
| whole cluster down | stops | fails |
| power/ISP out | stops | fails |
| **dead-man itself is broken** | — | *see below* |

All of them mean *go look*. None of them can be mistaken for health.

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

The `schedule` trigger is **commented out**, and `heartbeat.json` holds a seed value
with `stamped_at: 1970-01-01`.

Nothing stamps it yet — the in-cluster canary does not exist (homelab#431 AC5).
Enabling the cron now would email a failure every six hours forever, and an alarm that
cries wolf before it has ever told the truth gets muted. Enable `schedule` in the same
PR that deploys the canary.

Until then the workflow runs on `workflow_dispatch` only, and correctly reports the
seed as a failure: **the dead-man is armed, but the mine has no bird.**

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
