# Method excerpts from TrueNAS middleware TS-25.10.8.
# Upstream commit: 29ed02b8082f38494b0d2896bada8151f0d59716
# SPDX-License-Identifier: LGPL-3.0-only
# See THIRD_PARTY_NOTICES.md; local import/class scaffolding is synthetic.
# The upstream example serial in a comment was replaced with a synthetic label.

import dataclasses
import functools
import string


@dataclasses.dataclass(frozen=True, kw_only=True)
class DiskEntry:
    name: str | None = None
    devpath: str | None = None

    def __opener(
        self,
        *,
        relative_path: str | None = None,
        absolute_path: str | None = None,
        mode: str = "r",
    ) -> str | None:
        if relative_path is None and absolute_path is None:
            raise ValueError("relative_path or absolute_path is required")

        if relative_path is not None:
            path = f"/sys/block/{self.name}/{relative_path}"
        else:
            path = absolute_path

        try:
            with open(path, mode) as f:
                return f.read().strip()
        except Exception:
            pass


    @functools.cached_property
    def serial(self) -> str | None:
        """The disk's serial number as reported by sysfs"""
        # nvme devices
        serial = self.__opener(relative_path="device/serial")
        if not serial:
            # virtio-blk devices (vd)
            serial = self.__opener(relative_path="serial")

        if not serial:
            if raw := self.__opener(relative_path="device/vpd_pg80", mode="rb"):
                # VPD page 0x80 (Unit Serial Number) structure:
                #   Byte 0: Peripheral qualifier & device type
                #   Byte 1: Page code (0x80)
                #   Bytes 2-3: Page length (16-bit big-endian)
                #   Bytes 4+: ASCII serial-number data
                #
                # Reference: SCSI Primary Commands (SPC-7 rev 1) — Unit Serial Number VPD page 0x80, Table 599
                if len(raw) >= 4:
                    # unit serial page (vpd_pg80) command can never be > 255 characters
                    # So we will grab page length from 3
                    # Guard against devices that report a length larger than the buffer
                    page_len = min(raw[3], len(raw) - 4)

                    # Extract only the serial number data, skipping the 4-byte header
                    serial_txt = raw[4:4 + page_len].decode('ascii', errors='ignore')
                    serial = serial_txt.rstrip('\x00').strip()
                else:
                    serial = ""

        if not serial:
            # pmem devices have a uuid attribute that we use as serial
            serial = self.__opener(relative_path="uuid")

        # strip is required because we see these cases otherwise
        # >>> d.serial reported as '        SYNTHETIC-01'
        return serial.strip() if serial else None


    @functools.cached_property
    def lunid(self) -> str | None:
        """The disk's 'wwid' as presented in sysfs.

        NOTE: 'lunid' might be a bit of a misnomer since
            we're using the 'wwid' property of the disk
            but it is the same principle and it allows us
            to use common terms that most recognize."""
        HEX = set(string.hexdigits.lower())

        wwid = self.__opener(relative_path="device/wwid")
        if wwid is None:
            wwid = self.__opener(relative_path="wwid")

        if wwid is not None:
            # Normalize: strip whitespace and convert to lowercase
            wwid = wwid.strip().lower()

            # udev handling of WWN identifiers:
            # - For SCSI devices: ID_WWN is set only for NAA descriptors (via scsi_id)
            # - For NVMe devices: ID_WWN is set to the wwid sysfs value, which includes eui. prefix
            #   (see /usr/lib/udev/rules.d/60-persistent-storage.rules)
            # - For both: The middleware strips prefixes to get a consistent format
            #
            # We strip naa., 0x, and eui. prefixes to match middleware behavior.
            # t10 identifiers are not used for ID_WWN and return None.
            original_prefix = None
            for prefix in ("naa.", "0x", "eui."):
                if wwid.startswith(prefix):
                    original_prefix = prefix
                    wwid = wwid[len(prefix):]
                    break
            else:
                # t10.* and others are not used for ID_WWN in udev
                return None

            # Remove spaces after prefix stripping
            wwid = wwid.replace(" ", "")

            # Truncate to 16 characters ONLY for NAA WWNs with valid hex characters.
            # This matches udev's ID_WWN behavior for NAA WWNs specifically.
            # EUI identifiers (common for NVMe) are NOT truncated.
            #
            # Reference: https://github.com/systemd/systemd/blob/e65455feade65c798fd1742220768eba7f81755b/
            # src/udev/scsi_id/scsi_serial.c
            # check_fill_0x83_id(): if (id_search->id_type == SCSI_ID_NAA && wwn != NULL)
            #                       strncpy(wwn, serial + s, 16);
            if original_prefix in ("naa.", "0x") and len(wwid) > 16 and set(wwid[:16]) <= HEX:
                wwid = wwid[:16]

        return wwid if wwid else None


    @functools.cached_property
    def model(self) -> str | None:
        """The disk's model as reported by sysfs"""
        return self.__opener(relative_path="device/model")


    @functools.cached_property
    def identifier(self) -> str:
        """Return, ideally, a unique identifier for the disk.

        NOTE: If someone is using a usb 'hub', for example, then
            all bets are off the table. Those devices will often
            report duplicate serial numbers for all disks attached
            to it AND will report the same lunid. It's impossible
            for us to handle that and this is a scenario that isn't
            supported."""
        if self.serial and self.lunid:
            return f"{{serial_lunid}}{self.serial}_{self.lunid}"
        elif self.serial:
            return f"{{serial}}{self.serial}"
        elif partitions := self.partitions():
            with contextlib.suppress(Exception):
                # We don't want to crash if we can't read partitions
                for part in filter(
                    lambda p: PART_TYPES.get(p.partition_type_guid, "UNKNOWN") == "ZFS",
                    partitions
                ):
                    return f"{{uuid}}{part.unique_partition_guid}"

        # If we reach here, we have no serial or partitions
        return f"{{devicename}}{self.name}"
