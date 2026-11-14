#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-only
"""Read-only, count-only verification for the ST14000NM0001 workaround.

This tool checks discovery and inventory independently. It neither synchronizes
inventory nor establishes the safety of any pool operation. No device identifiers
or subprocess output are emitted, including on failure.
"""

import argparse
from datetime import datetime
import json
import math
import re
import subprocess


MODEL = "ST14000NM0001"
FULL_LUNID = re.compile(r"[0-9a-f]{32}\Z")


def normalize_lunid(value):
    """Return a full 128-bit identifier, or None for an invalid value."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if value.startswith("0x"):
        value = value[2:]
    elif value.startswith("naa."):
        value = value[4:]
    return value if FULL_LUNID.fullmatch(value) else None


def _is_model(record):
    return isinstance(record["model"], str) and record["model"].strip().upper() == MODEL


def _record_shape(record):
    # Upstream explicitly returns model=None when ID_MODEL is unavailable.
    return (
        isinstance(record, dict) and "model" in record
        and (record["model"] is None or isinstance(record["model"], str))
    )


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _valid_expiry(value):
    if value is None:
        return True
    if isinstance(value, dict) and set(value) == {"$date"}:
        timestamp = value["$date"]
        return type(timestamp) is int or (type(timestamp) is float and math.isfinite(timestamp))
    if isinstance(value, str) and ("T" in value or " " in value):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return True
        except ValueError:
            pass
    return False


def summarize(live, inventory, expected_luns=16):
    """Summarize untrusted middleware JSON without returning its identifiers."""
    if type(expected_luns) is not int or expected_luns < 1:
        raise ValueError("expected_luns must be a positive integer")

    live_shape = isinstance(live, dict)
    inventory_shape = isinstance(inventory, list)
    live_items = list(live.items()) if live_shape else []
    inventory_items = inventory if inventory_shape else []
    live_shape = live_shape and all(
        _nonempty_string(name) and _record_shape(row) for name, row in live_items
    )
    inventory_shape = inventory_shape and all(_record_shape(row) for row in inventory_items)

    live_model = [
        (name, row) for name, row in live_items
        if _record_shape(row) and _is_model(row)
    ]
    inventory_model = [row for row in inventory_items if _record_shape(row) and _is_model(row)]
    expiry_known = all("expiretime" in row for row in inventory_model)
    active = [row for row in inventory_model if "expiretime" in row and row["expiretime"] is None]
    expired = [row for row in inventory_model if "expiretime" in row and row["expiretime"] is not None]

    live_ids = [normalize_lunid(row.get("lunid")) for _, row in live_model]
    active_ids = [normalize_lunid(row.get("lunid")) for row in active]
    live_valid_ids = [value for value in live_ids if value is not None]
    active_valid_ids = [value for value in active_ids if value is not None]
    identifiers = [row.get("identifier") for row in active]
    valid_identifiers = [value for value in identifiers if _nonempty_string(value)]
    active_names = [row.get("name") for row in active]
    valid_active_names = [value for value in active_names if _nonempty_string(value)]
    live_names_valid = all(
        _nonempty_string(name) and row.get("name") == name for name, row in live_model
    )
    active_names_valid = len(valid_active_names) == len(active)
    live_ids_valid = len(live_valid_ids) == len(live_model)
    active_ids_valid = len(active_valid_ids) == len(active)

    checks = {
        "live_response_valid": live_shape,
        "inventory_response_valid": inventory_shape,
        "inventory_expiry_known": expiry_known,
        "inventory_expiry_valid": expiry_known and all(_valid_expiry(row["expiretime"]) for row in inventory_model),
        "live_count_expected": len(live_model) == expected_luns,
        "active_inventory_count_expected": len(active) == expected_luns,
        "live_lunids_full_length": live_ids_valid,
        "active_inventory_lunids_full_length": active_ids_valid,
        "live_lunids_unique": len(set(live_valid_ids)) == len(live_model),
        "active_inventory_lunids_unique": len(set(active_valid_ids)) == len(active),
        "active_inventory_identifiers_present": len(valid_identifiers) == len(active),
        "active_inventory_identifiers_unique": len(set(valid_identifiers)) == len(active),
        "live_names_valid": live_names_valid,
        "active_inventory_names_valid": active_names_valid,
        "active_inventory_names_unique": len(set(valid_active_names)) == len(active),
        "live_inventory_lunids_match": (
            live_ids_valid and active_ids_valid and set(live_valid_ids) == set(active_valid_ids)
        ),
        "live_inventory_mapping_match": (
            live_names_valid and active_names_valid and live_ids_valid and active_ids_valid
            and {(name, normalize_lunid(row.get("lunid"))) for name, row in live_model}
            == {(row["name"], normalize_lunid(row.get("lunid"))) for row in active}
        ),
    }
    return {
        "expected_luns": expected_luns,
        "counts": {
            "live_model_luns": len(live_model),
            "live_unique_lunids": len(set(live_valid_ids)),
            "active_inventory_model_rows": len(active),
            "active_inventory_unique_lunids": len(set(active_valid_ids)),
            "expired_inventory_model_rows": len(expired),
            "duplicate_live_lunids": len(live_valid_ids) - len(set(live_valid_ids)),
            "duplicate_active_inventory_lunids": len(active_valid_ids) - len(set(active_valid_ids)),
            "duplicate_active_inventory_identifiers": len(valid_identifiers) - len(set(valid_identifiers)),
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def _query(arguments):
    try:
        result = subprocess.run(
            ["midclt", "call", *arguments],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return json.loads(result.stdout), True
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
        return None, False


def _positive_int(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-luns", type=_positive_int, default=16, help="expected model LUN count (default: 16)")
    args = parser.parse_args(argv)
    live, live_ok = _query(["device.get_disks"])
    inventory, inventory_ok = _query(["disk.query", "[]", '{"extra":{"include_expired":true}}'])
    report = summarize(live, inventory, args.expect_luns)
    report["checks"]["live_query_succeeded"] = live_ok
    report["checks"]["inventory_query_succeeded"] = inventory_ok
    report["passed"] = all(report["checks"].values())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
