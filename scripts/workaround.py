#!/usr/bin/env python3
"""Guarded bind-mount deployment. No pool, database, or block-device writes.

SPDX-License-Identifier: LGPL-3.0-only
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

STATE = Path('/etc/truenas-exos-2x14')
PERSISTENT = Path('/etc/systemd/system/middlewared.service.d/20-exos-2x14.conf')
TEMPORARY = Path('/run/systemd/system/middlewared.service.d/20-exos-2x14.conf')
TARGETS = (
    '/usr/lib/python3/dist-packages/middlewared/utils/disks_/disk_class.py',
    '/usr/lib/python3/dist-packages/middlewared/plugins/device_/device_info.py',
)
RELEASES = {'25.10.8', 'TrueNAS-SCALE-25.10.8'}
SERVICE = 'middlewared.service'


class Refusal(RuntimeError):
    pass


def run(args):
    # Never echo stdout/stderr: middleware commands can expose local identifiers.
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise Refusal(f'{Path(args[0]).name} failed (exit {result.returncode}); inspect locally.')
    return result.stdout.strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def release_check(path=Path('/etc/version')):
    if path.read_text().strip() not in RELEASES:
        raise Refusal('Only the exact TrueNAS SCALE 25.10.8 release is accepted. Roll back before upgrading.')


def host_namespace_check():
    if os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'):
        raise Refusal('A private mount namespace is unsupported; mounts must be visible to middlewared.')


def stopped_check():
    state = run(['systemctl', 'show', '--property=ActiveState', '--value', SERVICE])
    if state not in ('inactive', 'failed'):
        raise Refusal('Stop middlewared explicitly before install or rollback; it must not be running.')


def startup_check():
    state = run(['systemctl', 'show', '--property=ActiveState', '--value', SERVICE])
    if state not in ('inactive', 'failed', 'activating'):
        raise Refusal('Startup apply refuses to change files while middlewared is running.')


def trusted(path):
    """Reject symlinks and group/world-writable or non-root-owned deployment paths."""
    for item in [path, *path.parents]:
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Refusal(f'Unsafe ownership, permissions, or symlink: {item}')


def manifest_at(base):
    manifest = json.loads((base / 'upstream.json').read_text())
    if manifest['version'] != '25.10.8' or tuple(f['target'] for f in manifest['files']) != TARGETS:
        raise Refusal('Unexpected manifest version or target paths.')
    for entry in manifest['files']:
        if not re.fullmatch(r'patches/[a-z0-9-]+\.patch', entry['patch']):
            raise Refusal('Unsafe patch path in manifest.')
        for name in ('sha256_before', 'sha256_after'):
            if not re.fullmatch(r'[0-9a-f]{64}', entry[name]):
                raise Refusal('Invalid source checksum in manifest.')
    return manifest


def apply_patch(original, patch_file):
    # Work on a private copy only. No patch utility ever writes /usr.
    with tempfile.TemporaryDirectory(prefix='exos-patch-') as temp:
        output = Path(temp) / 'source.py'
        output.write_bytes(original)
        run(['patch', '--batch', '--forward', '--fuzz=0', '--no-backup-if-mismatch',
             str(output), str(patch_file.resolve())])
        patched = output.read_bytes()
    compile(patched, '<patched middleware>', 'exec')
    return patched


def mounted_targets():
    # Only exact mountpoints count, not /usr's parent filesystem.
    result = set()
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        value = line.split()[4]
        value = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
        result.add(value)
    return result


def owns_mount(target, overlay):
    return str(target) in mounted_targets() and overlay.is_file() and os.path.samefile(target, overlay)


def dropin(persistent):
    # A separate cache prevents a pre-existing .pyc bypassing patched source.
    # Key it by patched content, including when a same-size revision is installed
    # after rollback during the same boot (timestamp pyc headers omit hashes).
    base = STATE if STATE.exists() else Path(__file__).resolve().parent.parent
    if base == STATE:
        trusted(STATE / 'upstream.json')
    hashes = ''.join(entry['sha256_after'] for entry in manifest_at(base)['files'])
    cache_key = digest(hashes.encode())[:16]
    text = ('# Managed by truenas-exos-2x14-workaround\n[Service]\n'
            f'Environment=PYTHONPYCACHEPREFIX=/run/truenas-exos-2x14-pycache-{cache_key}\n')
    if persistent:
        text += f'ExecStartPre=/usr/bin/python3 {STATE}/workaround.py apply\n'
    return text


def check_dropins():
    for path, persistent in ((PERSISTENT, True), (TEMPORARY, False)):
        trusted(path)
        if path.exists() and path.read_text() != dropin(persistent):
            raise Refusal(f'Refusing to overwrite or remove an unrecognized drop-in: {path}')


def check_state():
    trusted(STATE)
    for path in STATE.rglob('*'):
        trusted(path)
    manifest = manifest_at(STATE)
    for entry in manifest['files']:
        name = Path(entry['target']).name
        for directory, key in (('originals', 'sha256_before'), ('overlays', 'sha256_after')):
            if digest((STATE / directory / name).read_bytes()) != entry[key]:
                raise Refusal('Staged source checksum mismatch; preserve state and investigate locally.')
    return manifest


def validate_targets(manifest, allow_owned=False):
    mounts = mounted_targets()
    for entry in manifest['files']:
        target = Path(entry['target'])
        trusted(target)
        overlay = STATE / 'overlays' / target.name
        if str(target) in mounts:
            if not allow_owned or not owns_mount(target, overlay):
                raise Refusal('A target already has an unrecognized mount; do not stack mounts.')
            key = 'sha256_after'
        else:
            key = 'sha256_before'
        if digest(target.read_bytes()) != entry[key]:
            raise Refusal(f'Source checksum mismatch: {target}. No fuzzy patching is allowed.')


def prepare(base):
    manifest = manifest_at(base)
    validate_targets(manifest)
    prepared = []
    for entry in manifest['files']:
        original = Path(entry['target']).read_bytes()
        patched = apply_patch(original, base / entry['patch'])
        if digest(patched) != entry['sha256_after']:
            raise Refusal('Patched source checksum mismatch.')
        prepared.append((entry, original, patched))
    return manifest, prepared


def stage(base, prepared):
    trusted(STATE.parent)
    # Rename within /etc for atomic publication of complete recovery state.
    temp = Path(tempfile.mkdtemp(prefix='.exos-2x14-', dir=STATE.parent))
    try:
        (temp / 'originals').mkdir(mode=0o700)
        (temp / 'overlays').mkdir(mode=0o700)
        (temp / 'upstream.json').write_bytes((base / 'upstream.json').read_bytes())
        (temp / 'workaround.py').write_bytes(Path(__file__).read_bytes())
        for entry, original, patched in prepared:
            name = Path(entry['target']).name
            (temp / 'originals' / name).write_bytes(original)
            overlay = temp / 'overlays' / name
            overlay.write_bytes(patched)
            # Also invalidate timestamp-based bytecode for manual inspection.
            modified = Path(entry['target']).stat().st_mtime_ns + 2_000_000_000
            os.utime(overlay, ns=(modified, modified))
        for path in temp.rglob('*'):
            path.chmod(0o700 if path.is_dir() else 0o600)
        temp.rename(STATE)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def mount_overlays(manifest):
    validate_targets(manifest, allow_owned=True)  # Validate BOTH before modifying either.
    new_mounts = []
    try:
        for entry in manifest['files']:
            target = Path(entry['target'])
            overlay = STATE / 'overlays' / target.name
            if owns_mount(target, overlay):
                continue
            run(['mount', '--bind', str(overlay), str(target)])
            new_mounts.append(target)
            run(['mount', '-o', 'remount,bind,ro', str(target)])
        validate_targets(manifest, allow_owned=True)
    except Exception:
        for target in reversed(new_mounts):
            # No lazy/forced unmount: a failure requires operator attention.
            run(['umount', str(target)])
        raise


def install(args, base):
    release_check()
    host_namespace_check()
    check_dropins()
    if STATE.exists() or STATE.is_symlink():
        manifest = check_state()
        if manifest != manifest_at(base) or (STATE / 'workaround.py').read_bytes() != Path(__file__).read_bytes():
            raise Refusal('Different workaround already staged; roll it back before installing this revision.')
        validate_targets(manifest, allow_owned=True)
        prepared = None
    else:
        manifest, prepared = prepare(base)
    if not args.apply:
        print('Preflight passed; no system files or mounts changed. This is not hardware validation.')
        return
    if not (args.persistent or args.temporary):
        raise Refusal('Choose --persistent or --temporary explicitly with --apply.')
    stopped_check()
    if prepared is not None:
        stage(base, prepared)
    chosen, other = (PERSISTENT, TEMPORARY) if args.persistent else (TEMPORARY, PERSISTENT)
    if other.exists():
        raise Refusal('Another mode is installed; roll back before changing modes.')
    mount_overlays(manifest)
    # Preserve recoverable state/mounts on a drop-in or reload failure. Middleware
    # stays stopped, and rollback is available. Never delete backup state here.
    chosen.parent.mkdir(parents=True, exist_ok=True)
    trusted(chosen.parent)
    fd, temporary = tempfile.mkstemp(prefix='.exos-', dir=chosen.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(dropin(args.persistent))
        os.chmod(temporary, 0o644)
        os.replace(temporary, chosen)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    run(['systemctl', 'daemon-reload'])
    print('Installed. Start middlewared, explicitly run disk.sync_all, then verify live discovery AND inventory.')


def rollback(args):
    # Deliberately no release gate: recovery must work after an accidental upgrade.
    host_namespace_check()
    check_dropins()
    if not STATE.exists():
        if any(p.exists() for p in (PERSISTENT, TEMPORARY)):
            raise Refusal('Hook exists but recovery state is missing; use documented console recovery.')
        print('No staged workaround found; nothing changed.')
        return
    manifest = check_state()
    for entry in manifest['files']:
        target = Path(entry['target'])
        if str(target) in mounted_targets() and not owns_mount(target, STATE / 'overlays' / target.name):
            raise Refusal('Unrecognized target mount; rollback refuses to unmount it.')
    if not args.apply:
        print('Rollback preflight passed; no changes made. Inventory changes are not reversed by rollback.')
        return
    stopped_check()
    # Remove startup persistence first. On interruption, rerun rollback.
    for path in (PERSISTENT, TEMPORARY):
        if path.exists():
            path.unlink()
    run(['systemctl', 'daemon-reload'])
    for entry in reversed(manifest['files']):
        target = Path(entry['target'])
        if owns_mount(target, STATE / 'overlays' / target.name):
            run(['umount', str(target)])
    # Keep backups and helper in an archive, never overwrite newer OS files.
    archive = Path(tempfile.mkdtemp(prefix='truenas-exos-2x14-rollback-', dir=STATE.parent))
    STATE.rename(archive / 'state')
    print(f'Overlays and hook removed. Recovery state retained in {archive}. Start middlewared explicitly.')
    print('Inventory/database changes are NOT undone. Review them locally; do not delete records blindly.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'rollback', 'apply'))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true', help='read-only preflight (default)')
    modes.add_argument('--apply', action='store_true', help='explicitly perform changes')
    persistence = parser.add_mutually_exclusive_group()
    persistence.add_argument('--persistent', action='store_true')
    persistence.add_argument('--temporary', action='store_true')
    args = parser.parse_args(argv)
    if args.action != 'install' and (args.persistent or args.temporary):
        parser.error('persistence modes apply only to install')
    try:
        mutation = args.apply or args.action == 'apply'
        if mutation and os.geteuid() != 0:
            raise Refusal('Root is required to change mounts or the service drop-in.')
        if args.action == 'apply' and (args.check or args.apply):
            raise Refusal('The startup apply action takes no flags.')
        # Serialize every mutating operation, including systemd pre-start hooks.
        lock = None
        if mutation:
            lockpath = Path('/run/truenas-exos-2x14.lock')
            trusted(lockpath)
            lock = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if args.action == 'install':
                install(args, Path(__file__).resolve().parent.parent)
            elif args.action == 'rollback':
                rollback(args)
            else:
                release_check()
                host_namespace_check()
                startup_check()
                mount_overlays(check_state())
                print('Exos overlays validated and mounted before middleware startup.')
        finally:
            if lock is not None:
                os.close(lock)
    except (Refusal, OSError, ValueError, KeyError, SyntaxError) as error:
        # Deployment errors concern paths/checks only, never raw device data.
        print(f'REFUSED: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
