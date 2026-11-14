# TrueNAS SCALE Exos 2X14 LUN identity workaround

An **unsupported, model-specific workaround tested on TrueNAS SCALE 25.10.8 only** for Seagate Exos 2X14 `ST14000NM0001` SAS dual-actuator drives. It preserves each actuator's full NAA identifier so that middleware can distinguish the two LUNs on one physical drive.

This repository documents an operator-reported working workaround and provides reproducible patches, guarded installation and rollback scripts, and synthetic tests. The packaged scripts have not been exercised on the reported NAS. Passing the synthetic tests is not hardware validation.

## Reported environment and evidence

| Component | Reported configuration |
| --- | --- |
| Operating system | TrueNAS SCALE 25.10.8 |
| Server | Dell PowerEdge T430 |
| Controller | PERC H730P in HBA mode |
| Drives | Eight Seagate Exos 2X14 `ST14000NM0001` SAS drives |
| Operating-system devices | 16 SCSI LUNs; two per physical drive |

| Observation | Verification status |
| --- | --- |
| Original middleware inventory | Eight records despite 16 discovered LUNs |
| Temporary bind mounts | Reported tested |
| Persistent systemd `ExecStartPre` hook | Reported tested |
| Inventory synchronization with `disk.sync_all` | Reported successful **before reboot** |
| Live discovery after reboot | Reported **16 unique LUNs** |
| Middleware inventory after reboot | **Not yet verified; check separately** |
| Pool creation, disk replacement, SMART behavior, resilvering | **Not tested; no claims made** |

These statements describe the supplied test record. This repository contains no raw machine logs or identifying hardware values. Sixteen LUNs still belong to eight physical drives: the two actuators of one drive share a physical failure domain. Inventory visibility does not establish a safe pool topology.

## Why the inventory collapsed

For each physical drive in the reported environment, both LUNs expose the same serial number and the same shortened 64-bit `ID_WWN`. Their full 128-bit `ID_WWN_WITH_EXTENSION` values are distinct. Middleware's shortened LUN identity therefore collided between the two actuators, collapsing 16 devices into eight inventory records.

The two minimal patches apply only to `ST14000NM0001`:

1. `DiskEntry.lunid` in `disk_class.py` preserves the full NAA identifier.
2. `get_lunid` in `device_info.py` prefers `ID_WWN_WITH_EXTENSION` for that model.

Both discovery paths must agree on the identity. Restarting middleware loads the patched code; a separate `disk.sync_all` refreshes its inventory. The patch does not change drive firmware, controller configuration, partitions, or ZFS metadata.

## Before installation

Use a maintenance window with local or out-of-band console access: stopping middleware interrupts the management UI and API. Export the TrueNAS configuration and protect backups before proceeding. Review the patches and scripts locally.

The installer requires `/etc/version` to contain exactly `25.10.8` (also accepting the product-prefixed spelling `TrueNAS-SCALE-25.10.8`) and verifies SHA-256 hashes of both original and patched files against [the pinned upstream manifest](upstream.json). Nightly/build suffixes and other releases are refused. It does not make the system root writable or install OS packages. Do not bypass a version or source mismatch. A prior manual workaround or another modification must be reconciled first.

The NAS must already provide `/usr/bin/python3`, GNU `patch`, `mount`, `umount`, `systemctl`, and `midclt`. If a prerequisite is missing, stop and investigate; installing packages on the appliance is outside this procedure. Development tests need Python 3.10 or later and GNU `patch`.

The persistent payload lives in `/etc/truenas-exos-2x14`. The systemd drop-in is `/etc/systemd/system/middlewared.service.d/20-exos-2x14.conf`. These files must remain on storage available before middleware starts; do not move the payload to a pool that needs middleware to become available.

## Install

From a local copy of this repository on the NAS, first perform the read-only checks:

```sh
sudo bash scripts/install.sh --check
```

Stop middleware, apply the persistent variant, then start middleware:

```sh
sudo systemctl stop middlewared
sudo bash scripts/install.sh --apply --persistent
sudo systemctl start middlewared
```

Run each command separately and proceed only if it succeeds. The scripts require middleware to be stopped for changes. If installation fails, read the error and resolve it before starting middleware; do not continue to inventory synchronization on an unsuccessful installation.

For a temporary test instead, replace the install command with:

```sh
sudo bash scripts/install.sh --apply --temporary
```

Temporary bind mounts disappear at reboot. Temporary mode also creates an environment-only drop-in under `/run/systemd/system/middlewared.service.d/20-exos-2x14.conf`; that drop-in disappears at reboot. Persistent installation adds an `ExecStartPre` hook under `/etc` that validates the release and source and reapplies the bind mounts before middleware starts. The hook does not run `disk.sync_all`.

Both modes set `PYTHONPYCACHEPREFIX` to `/run/truenas-exos-2x14-pycache-<content-hash>`, isolating Python bytecode from existing caches and other patch revisions. The installer and startup hook refuse a private mount namespace because its mounts might not reach the middleware process. They also refuse unknown mounts, unsafe deployment-file permissions, or an unrecognized drop-in at the managed path.

After middleware starts successfully, explicitly refresh the inventory:

```sh
sudo midclt call -job disk.sync_all
```

Wait for job completion and check for errors. This is an inventory write, not a read-only diagnostic. Follow [the verification procedure](docs/verification.md) before and after a reboot. Do not infer inventory success from live discovery alone.

The read-only checker reports sanitized counts and consistency checks:

```sh
sudo python3 scripts/verify.py --expect-luns 16
```

After reboot, run this checker before manually repeating synchronization so that the reboot result remains distinguishable from a later refresh.

## Roll back

Review the rollback checks first:

```sh
sudo bash scripts/rollback.sh --check
```

Then stop middleware, remove this workaround, and restart:

```sh
sudo systemctl stop middlewared
sudo bash scripts/rollback.sh --apply
sudo systemctl start middlewared
```

Rollback removes the managed hook and bind mounts, exposing the underlying OS files again. It archives recovery state under `/etc/truenas-exos-2x14-rollback-*/state`. It never copies an older source file over an upgraded OS file, and deliberately permits rollback after a version change. It does not undo inventory records created by synchronization and does not edit pool metadata. Returning to the unpatched identity behavior can reintroduce the original collision. Do not delete inventory rows manually as part of this procedure.

Roll back **before changing TrueNAS versions**. The workaround is not tested on other releases or boot environments. Its version/source checks deliberately refuse incompatible code; a persistent hook that fails these checks can prevent middleware from starting. Keep console access and a copy of this repository available for recovery.

If the checkout is unavailable, the root-owned installed helper can perform the same rollback:

```sh
sudo /usr/bin/python3 /etc/truenas-exos-2x14/workaround.py rollback --check
sudo systemctl stop middlewared
sudo /usr/bin/python3 /etc/truenas-exos-2x14/workaround.py rollback --apply
sudo systemctl start middlewared
```

If recovery state is missing or corrupted, preserve it for inspection. From the console, inspect the two managed drop-ins and exact target mounts shown in `upstream.json`. Disable only this project's identified drop-ins, run `systemctl daemon-reload`, and unmount only overlays you have confirmed belong to this workaround. Do not force or lazily unmount unknown mounts, overwrite `/usr`, or delete other middleware drop-ins. Start middleware only after the underlying OS files are visible. This manual path requires local inspection; the scripts deliberately refuse ambiguous recovery.

## Synthetic tests

On a development machine with Python 3, run:

```sh
python3 -m unittest discover -s tests -v
```

The tests use invented data and do not require disks, root access, or TrueNAS. They check identifier behavior and script safeguards. They do not validate actual SCSI discovery, controller behavior, boot persistence, inventory writes, or pool operations.

See [verification](docs/verification.md) for the evidence still needed and [privacy](docs/privacy.md) before sharing any diagnostic result.

## Source and licensing

The patches target [TrueNAS middleware `TS-25.10.8`](https://github.com/truenas/middleware/tree/29ed02b8082f38494b0d2896bada8151f0d59716), commit `29ed02b8082f38494b0d2896bada8151f0d59716`. This repository includes the upstream [LGPLv3 license](LICENSE), its incorporated [GPLv3 text](LICENSES/GPL-3.0.txt), and [source attribution and modification notices](THIRD_PARTY_NOTICES.md). Contributions here use `LGPL-3.0-only`; preserve these notices when redistributing reconstructed source.

To reproduce the patches on a development machine, first clone [this repository](https://github.com/KingsAlpaca/truenas-exos-2x14-workaround), then run the following from its root, with `../middleware-25.10.8` unused:

```sh
workaround_dir=$PWD
git clone --filter=blob:none --branch TS-25.10.8 https://github.com/truenas/middleware.git ../middleware-25.10.8
git -C ../middleware-25.10.8 checkout --detach 29ed02b8082f38494b0d2896bada8151f0d59716
git -C ../middleware-25.10.8 apply --check "$workaround_dir/patches/0001-disk-class-full-naa.patch" "$workaround_dir/patches/0002-device-info-extended-wwn.patch"
git -C ../middleware-25.10.8 apply "$workaround_dir/patches/0001-disk-class-full-naa.patch" "$workaround_dir/patches/0002-device-info-extended-wwn.patch"
```

This changes only the isolated upstream checkout. Compare SHA-256 values with `upstream.json`; do not copy the checkout over the NAS package files. TrueNAS, Dell, and Seagate names identify the tested products; this project is not an endorsement or supported integration from their vendors.
