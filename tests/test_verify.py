# SPDX-License-Identifier: LGPL-3.0-only
"""Synthetic discovery/inventory checks; these do not access any disks."""

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("verify", Path(__file__).resolve().parents[1] / "scripts/verify.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def synthetic_disks():
    live, inventory = {}, []
    for drive in range(8):
        for lun in range(2):
            name = f"synthetic-disk-{drive}-{lun}"
            # Invented values only: paired LUNs share a 64-bit prefix and serial.
            short = f"{drive + 1:016x}"
            full = short + f"{lun + 1:016x}"
            row = {
                "name": name, "model": "ST14000NM0001", "lunid": full,
                "serial": f"SYNTHETIC-{drive}", "ID_WWN": "0x" + short,
            }
            live[name] = row
            inventory.append({**row, "identifier": "{serial_lunid}" + row["serial"] + "_" + full, "expiretime": None})
    return live, inventory


class VerificationTests(unittest.TestCase):
    def test_sixteen_full_ids_pass(self):
        live, inventory = synthetic_disks()
        report = verify.summarize(live, inventory)
        self.assertTrue(report["passed"])
        self.assertEqual(report["counts"]["live_unique_lunids"], 16)
        self.assertEqual(report["counts"]["active_inventory_model_rows"], 16)

    def test_original_eight_inventory_rows_fail_independently(self):
        live, inventory = synthetic_disks()
        report = verify.summarize(live, inventory[::2])
        self.assertFalse(report["passed"])
        self.assertTrue(report["checks"]["live_count_expected"])
        self.assertFalse(report["checks"]["active_inventory_count_expected"])

    def test_matching_counts_with_different_ids_fail(self):
        live, inventory = synthetic_disks()
        inventory[0]["lunid"] = "f" * 32
        report = verify.summarize(live, inventory)
        self.assertTrue(report["checks"]["active_inventory_count_expected"])
        self.assertFalse(report["checks"]["live_inventory_lunids_match"])
        self.assertFalse(report["passed"])

    def test_expired_rows_are_counted_but_excluded(self):
        live, inventory = synthetic_disks()
        expired = {**inventory[0], "expiretime": {"$date": 1}, "lunid": "ab" * 8}
        report = verify.summarize(live, inventory + [expired])
        self.assertTrue(report["passed"])
        self.assertEqual(report["counts"]["expired_inventory_model_rows"], 1)

    def test_invalid_expiry_cannot_hide_an_unknown_record(self):
        for invalid in (False, 0, [], {}, {"$date": True}, {"$date": "unknown"}, "unknown"):
            with self.subTest(invalid=invalid):
                live, inventory = synthetic_disks()
                unknown = {**inventory[0], "expiretime": invalid}
                report = verify.summarize(live, inventory + [unknown])
                self.assertFalse(report["passed"])
                self.assertFalse(report["checks"]["inventory_expiry_valid"])

    def test_iso_expiry_is_also_accepted(self):
        live, inventory = synthetic_disks()
        expired = {**inventory[0], "expiretime": "2000-01-01T00:00:00Z"}
        self.assertTrue(verify.summarize(live, inventory + [expired])["passed"])

    def test_duplicate_live_lunids_fail(self):
        live, inventory = synthetic_disks()
        rows = list(live.values())
        rows[1]["lunid"] = rows[0]["lunid"]
        report = verify.summarize(live, inventory)
        self.assertFalse(report["passed"])
        self.assertEqual(report["counts"]["duplicate_live_lunids"], 1)

    def test_duplicate_inventory_identifiers_fail(self):
        live, inventory = synthetic_disks()
        inventory[1]["identifier"] = inventory[0]["identifier"]
        report = verify.summarize(live, inventory)
        self.assertFalse(report["passed"])
        self.assertEqual(report["counts"]["duplicate_active_inventory_identifiers"], 1)

    def test_swapped_device_mappings_fail(self):
        live, inventory = synthetic_disks()
        inventory[0]["name"], inventory[1]["name"] = inventory[1]["name"], inventory[0]["name"]
        report = verify.summarize(live, inventory)
        self.assertTrue(report["checks"]["live_inventory_lunids_match"])
        self.assertFalse(report["checks"]["live_inventory_mapping_match"])
        self.assertFalse(report["passed"])

    def test_short_or_invalid_ids_fail(self):
        for invalid in (None, "", "f" * 16, "g" * 32, [], 42):
            with self.subTest(invalid=invalid):
                live, inventory = synthetic_disks()
                inventory[0]["lunid"] = invalid
                self.assertFalse(verify.summarize(live, inventory)["passed"])

    def test_missing_inventory_fields_fail(self):
        for field in ("model", "lunid", "name", "identifier", "expiretime"):
            with self.subTest(field=field):
                live, inventory = synthetic_disks()
                del inventory[0][field]
                self.assertFalse(verify.summarize(live, inventory)["passed"])

    def test_malformed_shapes_fail_without_exception(self):
        good_live, good_inventory = synthetic_disks()
        cases = [
            ([], good_inventory), (None, good_inventory), (good_live, {}),
            (good_live, None), ({"bad": "malformed"}, good_inventory),
            (good_live, good_inventory + ["malformed"]),
            ({**good_live, "unknown": {"model": []}}, good_inventory),
        ]
        for live, inventory in cases:
            with self.subTest(live_type=type(live), inventory_type=type(inventory)):
                self.assertFalse(verify.summarize(live, inventory)["passed"])

    def test_unrelated_model_is_excluded_and_model_match_is_exact(self):
        live, inventory = synthetic_disks()
        for model in ("OTHER", "ST14000NM0001-EXTRA"):
            live[model] = {"model": model}
            inventory.append({"model": model})
        self.assertTrue(verify.summarize(live, inventory)["passed"])

    def test_present_null_model_is_valid_for_unrelated_devices(self):
        live, inventory = synthetic_disks()
        live["synthetic-unknown-model"] = {"model": None}
        inventory.append({"model": None})
        self.assertTrue(verify.summarize(live, inventory)["passed"])

    def test_null_model_on_target_still_fails_expected_count(self):
        live, inventory = synthetic_disks()
        inventory[0]["model"] = None
        report = verify.summarize(live, inventory)
        self.assertTrue(report["checks"]["inventory_response_valid"])
        self.assertFalse(report["checks"]["active_inventory_count_expected"])
        self.assertFalse(report["passed"])

    def test_model_whitespace_and_id_prefix_normalization(self):
        live, inventory = synthetic_disks()
        for row in live.values():
            row["model"] = " ST14000NM0001 "
            row["lunid"] = "naa." + row["lunid"].upper()
        for row in inventory:
            row["lunid"] = "0x" + row["lunid"]
        self.assertTrue(verify.summarize(live, inventory)["passed"])

    def test_output_never_contains_raw_identifiers(self):
        live, inventory = synthetic_disks()
        before = copy.deepcopy((live, inventory))
        serialized = json.dumps(verify.summarize(live, inventory))
        for row in inventory:
            for key in ("name", "serial", "lunid", "identifier", "ID_WWN"):
                self.assertNotIn(row[key], serialized)
        self.assertEqual((live, inventory), before)

    def test_main_runs_only_read_queries(self):
        live, inventory = synthetic_disks()
        results = [subprocess.CompletedProcess([], 0, json.dumps(data), "") for data in (live, inventory)]
        output = io.StringIO()
        with patch.object(verify.subprocess, "run", side_effect=results) as run, contextlib.redirect_stdout(output):
            self.assertEqual(verify.main(["--expect-luns", "16"]), 0)
        self.assertEqual(run.call_args_list[0].args[0], ["midclt", "call", "device.get_disks"])
        self.assertEqual(run.call_args_list[1].args[0], ["midclt", "call", "disk.query", "[]", '{"extra":{"include_expired":true}}'])
        self.assertTrue(json.loads(output.getvalue())["passed"])

    def test_query_failures_do_not_leak_command_output(self):
        for error in (
            subprocess.CalledProcessError(1, "SENSITIVE-command", output="SENSITIVE-output", stderr="SENSITIVE-error"),
            subprocess.TimeoutExpired("SENSITIVE-command", 30, output="SENSITIVE-output"),
            FileNotFoundError("SENSITIVE-path"),
        ):
            output = io.StringIO()
            with self.subTest(error=type(error)), patch.object(verify.subprocess, "run", side_effect=error), contextlib.redirect_stdout(output):
                self.assertEqual(verify.main([]), 1)
            self.assertNotIn("SENSITIVE", output.getvalue())
            self.assertFalse(json.loads(output.getvalue())["passed"])

    def test_invalid_json_does_not_leak_raw_content(self):
        output = io.StringIO()
        result = subprocess.CompletedProcess([], 0, "SENSITIVE-not-json", "")
        with patch.object(verify.subprocess, "run", return_value=result), contextlib.redirect_stdout(output):
            self.assertEqual(verify.main([]), 1)
        self.assertNotIn("SENSITIVE", output.getvalue())

    def test_expected_count_must_be_positive(self):
        for value in (0, -1, True, "16"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                verify.summarize({}, [], value)


if __name__ == "__main__":
    unittest.main()
