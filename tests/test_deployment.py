"""Synthetic deployment checks; no real mount or service calls are made.

SPDX-License-Identifier: LGPL-3.0-only
"""
import argparse
import difflib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workaround', ROOT / 'scripts/workaround.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state = self.base / 'state'
        self.persistent = self.base / 'etc/dropin.conf'
        self.temporary = self.base / 'run/dropin.conf'
        self.repo = self.base / 'repo'
        (self.repo / 'patches').mkdir(parents=True)
        self.targets = tuple(str(self.base / f'module{i}.py') for i in range(2))
        self.originals = {}
        entries = []
        for i, target in enumerate(self.targets):
            original = f"VALUE = 'original_{i}'\n".encode()
            replacement = f"VALUE = 'patched_{i}_longer'\n".encode()
            Path(target).write_bytes(original)
            self.originals[target] = original
            filename = f'patches/000{i}-change.patch'
            difference = ''.join(difflib.unified_diff(original.decode().splitlines(True),
                                                     replacement.decode().splitlines(True),
                                                     fromfile='a/source.py', tofile='b/source.py'))
            (self.repo / filename).write_text(difference)
            entries.append(dict(target=target, patch=filename,
                                sha256_before=w.digest(original), sha256_after=w.digest(replacement)))
        self.manifest = dict(version='25.10.8', files=entries)
        (self.repo / 'upstream.json').write_text(json.dumps(self.manifest))
        self.mounts = set()
        self.calls = []
        self.service = 'inactive'
        self.fail_mount_number = None
        self.mount_count = 0
        self.fail_reload = False
        for name, value in [('STATE', self.state), ('TARGETS', self.targets),
                            ('PERSISTENT', self.persistent), ('TEMPORARY', self.temporary)]:
            manager = patch.object(w, name, value)
            manager.start()
            self.addCleanup(manager.stop)
        for name in ('trusted', 'release_check', 'host_namespace_check'):
            manager = patch.object(w, name)
            manager.start()
            self.addCleanup(manager.stop)
        self.real_run = w.run
        manager = patch.object(w, 'run', side_effect=self.fake_run)
        manager.start()
        self.addCleanup(manager.stop)
        manager = patch.object(w, 'mounted_targets', side_effect=lambda: self.mounts.copy())
        manager.start()
        self.addCleanup(manager.stop)
        manager = patch.object(w, 'owns_mount', side_effect=self.fake_owned)
        manager.start()
        self.addCleanup(manager.stop)

    def fake_owned(self, target, overlay):
        return str(target) in self.mounts and overlay.is_file() and target.read_bytes() == overlay.read_bytes()

    def fake_run(self, args):
        self.calls.append(args)
        if args[0] == 'patch':
            return self.real_run(args)  # Only patches isolated temporary files.
        if args[:2] == ['systemctl', 'show']:
            return self.service
        if args == ['systemctl', 'daemon-reload']:
            if self.fail_reload:
                raise w.Refusal('synthetic reload failure')
            return ''
        if args[:2] == ['mount', '--bind']:
            self.mount_count += 1
            if self.mount_count == self.fail_mount_number:
                raise w.Refusal('synthetic mount failure')
            self.mounts.add(args[3])
            Path(args[3]).write_bytes(Path(args[2]).read_bytes())
            return ''
        if args[:3] == ['mount', '-o', 'remount,bind,ro']:
            return ''
        if args[0] == 'umount':
            self.mounts.remove(args[1])
            Path(args[1]).write_bytes(self.originals[args[1]])
            return ''
        raise AssertionError(f'Unexpected external command: {args}')

    def args(self, apply=False, persistent=False, temporary=False):
        return argparse.Namespace(apply=apply, persistent=persistent, temporary=temporary)

    def test_preflight_does_not_write_state_mount_or_service(self):
        w.install(self.args(), self.repo)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.mounts, set())
        self.assertTrue(all(call[0] == 'patch' for call in self.calls))

    def test_second_source_mismatch_refuses_before_any_system_change(self):
        Path(self.targets[1]).write_text('UNEXPECTED = True\n')
        with self.assertRaises(w.Refusal):
            w.install(self.args(True, True), self.repo)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.mounts, set())
        self.assertEqual(self.calls, [])

    def test_persistent_install_reapply_and_rollback(self):
        w.install(self.args(True, True), self.repo)
        self.assertEqual(self.mounts, set(self.targets))
        self.assertIn('ExecStartPre=', self.persistent.read_text())
        self.assertIn('PYTHONPYCACHEPREFIX=', self.persistent.read_text())
        self.assertFalse(self.temporary.exists())
        # Both idempotent install and pre-start apply reuse owned mounts.
        w.install(self.args(True, True), self.repo)
        w.mount_overlays(w.check_state())
        self.assertEqual(self.mount_count, 2)
        w.rollback(self.args(True))
        self.assertEqual(self.mounts, set())
        self.assertFalse(self.persistent.exists())
        self.assertFalse(self.state.exists())
        self.assertEqual(len(list(self.base.glob('truenas-exos-2x14-rollback-*'))), 1)
        for target, data in self.originals.items():
            self.assertEqual(Path(target).read_bytes(), data)

    def test_temporary_mode_has_no_persistent_hook(self):
        w.install(self.args(True, temporary=True), self.repo)
        self.assertFalse(self.persistent.exists())
        self.assertNotIn('ExecStartPre=', self.temporary.read_text())
        self.assertIn('PYTHONPYCACHEPREFIX=', self.temporary.read_text())

    def test_cache_namespace_changes_with_patched_content(self):
        w.install(self.args(True, True), self.repo)
        original = w.dropin(True)
        changed = json.loads((self.state / 'upstream.json').read_text())
        changed['files'][0]['sha256_after'] = 'a' * 64
        (self.state / 'upstream.json').write_text(json.dumps(changed))
        self.assertNotEqual(original, w.dropin(True))

    def test_active_middleware_refuses_install_and_rollback(self):
        self.service = 'active'
        with self.assertRaises(w.Refusal):
            w.install(self.args(True, True), self.repo)
        self.assertFalse(self.state.exists())
        self.service = 'inactive'
        w.install(self.args(True, True), self.repo)
        self.service = 'active'
        with self.assertRaises(w.Refusal):
            w.rollback(self.args(True))
        self.assertEqual(self.mounts, set(self.targets))
        self.assertTrue(self.persistent.exists())

    def test_startup_apply_accepts_activating_but_refuses_active(self):
        for state in ('inactive', 'failed', 'activating'):
            self.service = state
            w.startup_check()
        for state in ('active', 'reloading', 'deactivating', ''):
            self.service = state
            with self.assertRaises(w.Refusal):
                w.startup_check()

    def test_second_mount_failure_unwinds_first_and_keeps_recovery(self):
        self.fail_mount_number = 2
        with self.assertRaises(w.Refusal):
            w.install(self.args(True, True), self.repo)
        self.assertEqual(self.mounts, set())
        self.assertTrue(self.state.exists())
        self.assertFalse(self.persistent.exists())
        w.check_state()

    def test_reload_failure_leaves_explicit_recoverable_state(self):
        self.fail_reload = True
        with self.assertRaises(w.Refusal):
            w.install(self.args(True, True), self.repo)
        self.assertTrue(self.persistent.exists())
        self.assertTrue(self.state.exists())
        self.fail_reload = False
        w.rollback(self.args(True))
        self.assertEqual(self.mounts, set())

    def test_refuses_external_mounts_and_dropins(self):
        self.mounts.add(self.targets[1])
        with self.assertRaises(w.Refusal):
            w.install(self.args(True, True), self.repo)
        self.assertFalse(self.state.exists())
        self.mounts.clear()
        self.persistent.parent.mkdir()
        self.persistent.write_text('[Service]\n# Someone else owns this\n')
        with self.assertRaises(w.Refusal):
            w.install(self.args(True, True), self.repo)
        self.assertFalse(self.state.exists())

    def test_rollback_check_is_read_only_and_does_not_remove_state(self):
        w.install(self.args(True, True), self.repo)
        self.calls.clear()
        w.rollback(self.args())
        self.assertEqual(self.calls, [])
        self.assertTrue(self.persistent.exists())
        self.assertEqual(self.mounts, set(self.targets))

    def test_rollback_after_upgrade_does_not_copy_old_source_back(self):
        w.install(self.args(True, True), self.repo)
        # Model reboot into upgraded OS: mounts vanish, underlying files change.
        self.mounts.clear()
        newer = b'NEW_OS_SOURCE = True\n'
        for target in self.targets:
            Path(target).write_bytes(newer)
        w.release_check.side_effect = w.Refusal('upgraded version')
        w.rollback(self.args(True))
        for target in self.targets:
            self.assertEqual(Path(target).read_bytes(), newer)

    def test_corrupt_overlay_refuses_startup_before_mounting(self):
        w.install(self.args(True, True), self.repo)
        self.mounts.clear()
        (self.state / 'overlays' / Path(self.targets[1]).name).write_text('CORRUPT = True\n')
        with self.assertRaises(w.Refusal):
            w.mount_overlays(w.check_state())
        self.assertEqual(self.mounts, set())


class IndependentGuards(unittest.TestCase):
    def test_release_is_exact_no_nightly_or_suffix(self):
        with tempfile.TemporaryDirectory() as temp:
            version = Path(temp) / 'version'
            for valid in ('25.10.8', 'TrueNAS-SCALE-25.10.8'):
                version.write_text(valid + '\n')
                w.release_check(version)
            for invalid in ('25.10.7', '25.10.80', '25.10.8-MASTER', 'TrueNAS-SCALE-25.10.7', 'TrueNAS-SCALE-25.10.8-MASTER'):
                version.write_text(invalid)
                with self.assertRaises(w.Refusal):
                    w.release_check(version)

    def test_namespace_guard(self):
        with patch.object(w.os, 'readlink', side_effect=['mnt:[1]', 'mnt:[2]']):
            with self.assertRaises(w.Refusal):
                w.host_namespace_check()

    def test_trust_refuses_symlink_and_world_writable_ancestor(self):
        # Ownership mocked to root so this works on unprivileged CI runners.
        real_lstat = Path.lstat
        def root_stat(path):
            result = real_lstat(path)
            values = list(result)
            values[4] = 0
            return os.stat_result(values)
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'lstat', root_stat):
            parent = Path(temp)
            link = parent / 'symlink'
            link.symlink_to('/etc')
            with self.assertRaises(w.Refusal):
                w.trusted(link)
            # /tmp itself is writable: trusted() must reject even a root-owned child.
            with self.assertRaises(w.Refusal):
                w.trusted(parent)


if __name__ == '__main__':
    unittest.main()
