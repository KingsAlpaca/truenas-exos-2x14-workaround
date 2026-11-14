# SPDX-License-Identifier: LGPL-3.0-only
"""Exercise actual unified patches with upstream method excerpts, entirely offline."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "upstream.json").read_text())
MODEL = "ST14000NM0001"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PatchBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.base = Path(cls.tmp.name)
        for entry in MANIFEST["files"]:
            destination = cls.base / entry["source"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "tests/fixtures" / destination.name, destination)
            result = subprocess.run(
                ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(ROOT / entry["patch"])],
                cwd=cls.base, capture_output=True, text=True, check=True,
            )
            if "fuzz" in result.stdout.lower():
                raise AssertionError(result.stdout)
        disk_path = Path(MANIFEST["files"][0]["source"])
        info_path = Path(MANIFEST["files"][1]["source"])
        cls.pristine = load_module(ROOT / "tests/fixtures" / disk_path.name, "pristine_disk")
        cls.patched = load_module(cls.base / disk_path, "patched_disk")
        cls.original_info = load_module(ROOT / "tests/fixtures" / info_path.name, "original_info")
        cls.patched_info = load_module(cls.base / info_path, "patched_info")

    def disk_value(self, module, wwid, model=MODEL, serial="SYNTHETIC-01", attribute="lunid", fallback=False):
        values = {
            "device/model": model,
            "device/serial": serial,
            "wwid" if fallback else "device/wwid": wwid,
        }

        def opener(instance, *, relative_path=None, absolute_path=None, mode="r"):
            return values.get(relative_path)

        with patch.object(module.DiskEntry, "_DiskEntry__opener", opener):
            disk = module.DiskEntry(name="sdsynthetic", devpath="/dev/sdsynthetic")
            return getattr(disk, attribute)

    def udev_value(self, module, properties, sysfs_value="sysfs-fallback"):
        instance = module.DeviceService()
        instance.safe_retrieval = lambda props, key, default: props.get(key, default)
        dev = SimpleNamespace(properties=properties, sys_name="sdsynthetic")
        with patch.object(module, "DiskEntry", lambda **kwargs: SimpleNamespace(lunid=sysfs_value), create=True):
            return instance.get_lunid(dev)

    def test_eight_shared_serial_pairs_become_sixteen_unique_identifiers(self):
        original_ids, patched_ids, udev_ids = set(), set(), set()
        for drive in range(8):
            short = f"600000000000{drive:04x}"
            serial = f"SYNTHETIC-{drive:02d}"
            for lun in range(2):
                full = short + f"{lun:016x}"
                wwid = "naa." + full
                original_ids.add(self.disk_value(self.pristine, wwid, serial=serial, attribute="identifier"))
                patched_ids.add(self.disk_value(self.patched, wwid, serial=serial, attribute="identifier"))
                properties = {"ID_MODEL": MODEL, "ID_WWN": "0x" + short, "ID_WWN_WITH_EXTENSION": "0x" + full}
                old_lunid = self.udev_value(self.original_info, properties)
                new_lunid = self.udev_value(self.patched_info, properties)
                self.assertEqual(old_lunid, short)
                self.assertEqual(new_lunid, full)
                udev_ids.add(f"{{serial_lunid}}{serial}_{new_lunid}")
        self.assertEqual(len(original_ids), 8)
        self.assertEqual(len(patched_ids), 16)
        self.assertEqual(udev_ids, patched_ids)

    def test_sysfs_prefixes_normalization_and_fallback(self):
        full = "6000000000000001000000000000000a"
        for prefix in ("naa.", "0x"):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.disk_value(self.patched, "  " + prefix.upper() + full.upper() + "  "), full)
                self.assertEqual(self.disk_value(self.patched, prefix + full, fallback=True), full)
        self.assertEqual(self.disk_value(self.patched, "naa." + full, model="  " + MODEL + " "), full)

    def test_other_models_and_eui_keep_upstream_behavior(self):
        full = "6000000000000001000000000000000a"
        for model in ("OTHER-MODEL", "ST14000NM0001X", "", None):
            for prefix in ("naa.", "0x", "eui.", "t10.", "unknown."):
                with self.subTest(model=model, prefix=prefix):
                    self.assertEqual(self.disk_value(self.patched, prefix + full, model), self.disk_value(self.pristine, prefix + full, model))
        self.assertEqual(self.disk_value(self.patched, "eui." + full), full)

    def test_sysfs_missing_short_and_malformed_values_keep_original_fallback(self):
        for wwid in (None, "", "naa.", "naa.6000000000000001", "naa.6000000000000001zzzzzzzzzzzzzzzz", "naa.not-a-number", "t10.SYNTHETIC"):
            with self.subTest(wwid=wwid):
                self.assertEqual(self.disk_value(self.patched, wwid), self.disk_value(self.pristine, wwid))

    def test_extended_udev_normalization(self):
        full = "6000000000000001000000000000000a"
        for value in (full, "0x" + full, "  0X" + full.upper() + "  "):
            with self.subTest(value=value):
                self.assertEqual(self.udev_value(self.patched_info, {"ID_MODEL": " " + MODEL + " ", "ID_WWN_WITH_EXTENSION": value}), full)

    def test_udev_missing_or_invalid_extension_uses_original_fallback(self):
        for extension in ("", "0x", "0x6000000000000001", "0x6000000000000001zzzzzzzzzzzzzzzz"):
            for short in ("", "0x6000000000000001"):
                props = {"ID_MODEL": MODEL, "ID_WWN_WITH_EXTENSION": extension, "ID_WWN": short}
                with self.subTest(extension=extension, short=short):
                    self.assertEqual(self.udev_value(self.patched_info, props), self.udev_value(self.original_info, props))
        self.assertEqual(self.udev_value(self.patched_info, {"ID_MODEL": MODEL}), "sysfs-fallback")

    def test_udev_other_model_does_not_use_extended_identifier(self):
        for model in ("OTHER-MODEL", "ST14000NM0001X", ""):
            props = {"ID_MODEL": model, "ID_WWN_WITH_EXTENSION": "0x6000000000000001000000000000000a", "ID_WWN": "0x6000000000000001"}
            self.assertEqual(self.udev_value(self.patched_info, props), self.udev_value(self.original_info, props))


if __name__ == "__main__":
    unittest.main()
