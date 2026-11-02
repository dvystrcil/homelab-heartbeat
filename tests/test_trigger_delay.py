"""Tests for .github/scripts/trigger_delay.py (homelab-heartbeat#10 AC2).

Run: python3 -m unittest discover -s tests
"""
import importlib.util
import pathlib
import re
import unittest
from datetime import datetime, timezone

_PATH = pathlib.Path(__file__).resolve().parent.parent / ".github" / "scripts" / "trigger_delay.py"
_spec = importlib.util.spec_from_file_location("trigger_delay", _PATH)
td = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(td)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class NominalSlot(unittest.TestCase):
    def test_real_late_run_maps_to_most_recent_slot(self):
        # Observed 2026-10-29T22:56:33Z on '37 */6': the 18:37 slot, 259 min late.
        slot = td.nominal_slot("37 */6 * * *", utc(2026, 10, 29, 22, 56, 33))
        self.assertEqual(slot, utc(2026, 10, 29, 18, 37))

    def test_on_time_run_has_its_own_slot(self):
        now = utc(2026, 11, 1, 12, 37)
        self.assertEqual(td.nominal_slot("37 */6 * * *", now), now)

    def test_one_minute_before_slot_belongs_to_previous_slot(self):
        slot = td.nominal_slot("37 */6 * * *", utc(2026, 11, 1, 12, 36, 59))
        self.assertEqual(slot, utc(2026, 11, 1, 6, 37))

    def test_wraps_to_previous_day(self):
        slot = td.nominal_slot("37 */6 * * *", utc(2026, 11, 1, 0, 10))
        self.assertEqual(slot, utc(2026, 10, 31, 18, 37))

    def test_old_minute_zero_schedule(self):
        slot = td.nominal_slot("0 */6 * * *", utc(2026, 10, 25, 15, 38, 57))
        self.assertEqual(slot, utc(2026, 10, 25, 12, 0))

    def test_hourly_schedule(self):
        # The deferred cadence option from the issue must also work unchanged.
        slot = td.nominal_slot("23 * * * *", utc(2026, 11, 1, 9, 5))
        self.assertEqual(slot, utc(2026, 11, 1, 8, 23))

    def test_lists_and_ranges(self):
        slot = td.nominal_slot("15,45 1-3 * * *", utc(2026, 11, 1, 2, 30))
        self.assertEqual(slot, utc(2026, 11, 1, 2, 15))

    def test_day_fields_unsupported_rather_than_wrong(self):
        # Silently ignoring a day/month/weekday field would report a wrong slot.
        with self.assertRaises(ValueError):
            td.nominal_slot("37 */6 * * 1", utc(2026, 11, 1, 12, 0))

    def test_malformed_cron(self):
        with self.assertRaises(ValueError):
            td.nominal_slot("37 */6 * *", utc(2026, 11, 1, 12, 0))


class Render(unittest.TestCase):
    def test_schedule_reports_delay_and_slot(self):
        text = "\n".join(td.render("schedule", "37 */6 * * *", utc(2026, 10, 29, 22, 56, 33)))
        self.assertIn("2026-10-29T18:37:00Z", text)
        self.assertIn("**259.6 min**", text)
        self.assertIn("37 */6 * * *", text)

    def test_manual_dispatch_has_no_delay(self):
        text = "\n".join(td.render("workflow_dispatch", "", utc(2026, 11, 1, 12, 0)))
        self.assertIn("n/a", text)
        self.assertNotIn(" min**", text)

    def test_unparseable_cron_is_reported_not_raised(self):
        # The delay is telemetry; it must never be what fails the dead-man.
        text = "\n".join(td.render("schedule", "37 */6 * * 1", utc(2026, 11, 1, 12, 0)))
        self.assertIn("could not compute", text)


class LiveWorkflow(unittest.TestCase):
    def test_every_dead_man_cron_has_a_computable_slot(self):
        # The telemetry only supports minute/hour fields. A cron edit that adds
        # a day field would quietly turn every run's delay into a warning.
        wf = (_PATH.parent.parent / "workflows" / "dead-man.yml").read_text()
        crons = re.findall(r"^\s*- cron: '([^']+)'", wf, re.M)
        self.assertTrue(crons, "no cron found in dead-man.yml")
        for cron in crons:
            td.nominal_slot(cron, utc(2026, 11, 1, 12, 0))


if __name__ == "__main__":
    unittest.main()
