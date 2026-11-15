"""dead-man.yml pings healthchecks.io after a passing run (homelab-heartbeat#20 AC5).

healthchecks.io's dead-man-runs check (period 12 h, grace 2 h) emails when the
pings stop. That watches the GitHub dead-man itself, which nothing watched
before ("dead-man itself is broken" in the README).

Its whole value is that a ping means "a run executed AND passed". So:
  - the ping is the LAST step, after the freshness check;
  - it has no `if:` that could run it after a failure (always(), failure(),
    !cancelled()). Default success() is the only acceptable gate;
  - a failing ping never turns the dead-man red (continue-on-error): an
    unreachable healthchecks.io must not send a false "canary dead" email;
  - the URL comes from a secret, never a literal (it is a credential).

Parsed as text, not YAML: ubuntu-latest's system python has no guaranteed
PyYAML, and the step layout here is fixed.

Run: python3 -m unittest discover -s tests
"""
import pathlib
import re
import unittest

WF = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "dead-man.yml"
TEXT = WF.read_text()


def steps():
    body = TEXT.split("\n    steps:\n", 1)[1]
    # Each step starts at "      - " (6 spaces); keep its block text.
    return [b for b in re.split(r"\n(?=      - )", "\n" + body) if b.strip().startswith("- ")]


def name(block):
    m = re.search(r"name:\s*(.+)", block)
    return m.group(1).strip() if m else ""


class DeadManPing(unittest.TestCase):
    def setUp(self):
        self.steps = steps()
        self.ping = [s for s in self.steps if "hc-ping" in s.lower() or "HEALTHCHECKS_DEADMAN_PING_URL" in s]

    def test_one_ping_step_and_it_is_last(self):
        self.assertEqual(len(self.ping), 1, "expected exactly one healthchecks.io ping step")
        self.assertIs(self.ping[0], self.steps[-1], "the ping must be the last step")
        names = [name(s) for s in self.steps]
        self.assertLess(names.index("Check heartbeat freshness"), len(names) - 1)

    def test_ping_only_runs_on_success(self):
        m = re.search(r"^\s+if:\s*(.+)$", self.ping[0], re.M) if self.ping else None
        if m:
            self.assertRegex(m.group(1), r"^\$\{\{\s*success\(\)\s*\}\}$|^success\(\)$",
                             "only success() may gate the ping")

    def test_failed_ping_never_fails_the_run(self):
        self.assertTrue(self.ping and re.search(r"continue-on-error:\s*true", self.ping[0]))

    def test_url_comes_from_a_secret(self):
        self.assertTrue(self.ping and "${{ secrets.HEALTHCHECKS_DEADMAN_PING_URL }}" in self.ping[0])
        self.assertNotRegex(TEXT, r"hc-ping\.com/[0-9a-f-]{8,}", "no ping URL committed")


if __name__ == "__main__":
    unittest.main()
