# Exact method excerpts from TrueNAS middleware TS-25.10.8.
# Upstream commit: 29ed02b8082f38494b0d2896bada8151f0d59716
# SPDX-License-Identifier: LGPL-3.0-only
# See THIRD_PARTY_NOTICES.md; local import/class scaffolding is synthetic.

import re


def private(func):
    return func


class DeviceService:

    @private
    def get_lunid(self, dev):
        # Try udev ID_WWN first (for NAA format WWIDs)
        lunid = self.safe_retrieval(dev.properties, 'ID_WWN', '').removeprefix('0x').removeprefix('eui.')
        if lunid:
            return lunid

        # NAS-137807: Fallback to sysfs wwid for EUI-64 format WWIDs not exposed in udev properties
        # Uses DiskEntry.lunid which handles sysfs wwid retrieval and normalization
        disk_entry = DiskEntry(name=dev.sys_name, devpath=f'/dev/{dev.sys_name}')
        return disk_entry.lunid
