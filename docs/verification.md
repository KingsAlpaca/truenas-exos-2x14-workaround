# Verification and evidence boundaries

The reported manual workaround produced 16 unique LUNs in live discovery after reboot. Inventory synchronization succeeded before reboot. **Post-reboot inventory has not yet been verified.** The new packaging and synthetic test results must not be presented as an additional hardware test.

## Keep the two observations separate

Live discovery describes the devices middleware can discover now. The inventory is middleware's stored disk record set. A correct live result does not prove that synchronization completed, that every stored identifier is correct, or that stale entries have been retired.

Run the read-only aggregate checker from this repository:

```sh
sudo python3 scripts/verify.py --expect-luns 16
```

It queries `device.get_disks` and `disk.query`, checks only the exact target model, and prints sanitized JSON counts and check results. It checks full 32-hex-digit LUN identity uniqueness, exact live/active-inventory LUN identity set membership, and inventory identifier uniqueness. Expired records are counted separately and excluded from the active comparison. A nonzero exit means the checks did not all pass. The checker never runs synchronization or changes inventory.

Keep the phase with each result: before reboot, after reboot before manual synchronization, or after manual synchronization. The checker cannot prove that udev exposed the expected properties, that the controller configuration matches the reported environment, or that a particular physical pairing is correct; inspect that evidence locally when reproducing the root cause.

For the reported eight-drive system, record these checks locally:

| Check | Expected result |
| --- | --- |
| Operating-system SCSI LUNs for `ST14000NM0001` | 16 |
| Physical-drive grouping | Eight pairs; paired LUNs share the serial and short `ID_WWN` |
| Full `ID_WWN_WITH_EXTENSION` values | 16 distinct 128-bit values |
| Middleware live LUN identities | 16 distinct full identifiers |
| `disk.sync_all` job | Completed successfully |
| Active inventory records for the target model | 16, each associated with the expected live LUN |
| Inventory identity uniqueness | 16 distinct identities, with no shared identifier across a pair |

Scope counts to the exact model. The boot device and other attached disks must not inflate the result. Do not count expired or stale inventory records as current devices. Compare the sets of live and inventory identifiers locally; a count alone can conceal wrong or duplicated mappings.

## Before reboot

1. Record the installed TrueNAS version and inspect the systemd service status.
2. Check the operating-system properties locally and run the aggregate checker for live middleware discovery and inventory.
3. Restart middleware with the workaround installed, then run `midclt call -job disk.sync_all` as documented in the README.
4. Wait for the synchronization job to finish. Run the checker again to confirm inventory count, identifier uniqueness, and correspondence with live discovery separately.
5. Save a private local record of the result. Publish only the aggregate results described below.

If any check fails, stop here. Do not attempt a pool operation as a workaround verification step.

## After reboot

1. Confirm middleware started and the persistent hook completed successfully.
2. Confirm that both patched files have the expected bind mounts.
3. Run the aggregate checker again **before manually rerunning `disk.sync_all`**. Check live discovery: 16 target LUNs with 16 distinct full identities.
4. Check the same report's active inventory count, unique identities, and exact correspondence to the live device set. This is the outstanding verification in the supplied test record.
5. If inventory is incomplete, record that result first. If you then choose to run `disk.sync_all`, document its outcome as a separate post-reboot synchronization test. Do not describe the initial boot inventory as passing.

## Publishable result template

Use only observations that were actually made:

```text
TrueNAS release: 25.10.8
Exact drive model: ST14000NM0001
Physical drive count: 8
SCSI LUN count for this model: <observed count>
Phase: <before reboot / after reboot, before manual sync / after manual sync>
Live discovery count: <observed count>
Unique full live identities: <observed count>
Active inventory count: <observed count / not checked>
Unique inventory identities: <observed count / not checked>
Live/inventory identity sets match: <yes / no / not checked>
Synchronization job: <success / failure / not run in this phase>
Persistent hook: <success / failure / not used>
Pool creation/replacement/SMART/resilver testing: not performed
```

Do not publish raw identifiers to substantiate the counts. See [privacy](privacy.md).

## Not established by these checks

Discovery and inventory checks establish only the observed identity behavior. They do not establish pool creation support, replacement procedures, SMART coverage, resilver correctness, throughput, multipath behavior, or behavior on other hardware or TrueNAS releases. Two LUNs on one physical disk remain one physical-drive failure domain.
