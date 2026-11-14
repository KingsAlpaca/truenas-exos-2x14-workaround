# Licensing and source provenance

The patches modify [TrueNAS middleware](https://github.com/truenas/middleware),
maintained by iXsystems / TrueNAS and its contributors. Upstream authors retain
their copyrights; this repository does not claim ownership of their work.

The patch baseline is the upstream `TS-25.10.8` tag, resolved on 2026-11-11 to
commit [`29ed02b8082f38494b0d2896bada8151f0d59716`](https://github.com/truenas/middleware/tree/29ed02b8082f38494b0d2896bada8151f0d59716).
The exact paths and SHA-256 hashes before and after applying the patches are in
[`upstream.json`](upstream.json).

The two affected upstream files are:

- [`disk_class.py`](https://github.com/truenas/middleware/blob/29ed02b8082f38494b0d2896bada8151f0d59716/src/middlewared/middlewared/utils/disks_/disk_class.py)
- [`device_info.py`](https://github.com/truenas/middleware/blob/29ed02b8082f38494b0d2896bada8151f0d59716/src/middlewared/middlewared/plugins/device_/device_info.py)

Neither file carries a separate copyright or license header at this commit.
The upstream repository's
[`LICENSE`](https://github.com/truenas/middleware/blob/29ed02b8082f38494b0d2896bada8151f0d59716/LICENSE)
contains GNU LGPL version 3; a verbatim copy is included as this repository's
[`LICENSE`](LICENSE). LGPL version 3 incorporates GPL version 3, whose complete
text is included in [`LICENSES/GPL-3.0.txt`](LICENSES/GPL-3.0.txt).

Upstream also contains
[`LICENSE.IX`](https://github.com/truenas/middleware/blob/29ed02b8082f38494b0d2896bada8151f0d59716/LICENSE.IX)
for enterprise-only portions where specified. Neither of these two files is
marked with that license. This repository does not redistribute those
enterprise-only portions or a TrueNAS binary image.

The patches, accompanying scripts, synthetic tests and documentation contributed
here are provided under GNU LGPL version 3 (`LGPL-3.0-only`). This does not
relicense other upstream components. Preserve upstream notices when distributing
source reconstructed with these patches, and include both license texts and this
modification notice. The installer preserves the original file contents except
for the documented patch hunks.

## Modification notice — 2026-11-11

For the exact model `ST14000NM0001`, `DiskEntry.lunid` now preserves valid 128-bit
NAA identifiers, and `DeviceService.get_lunid` prefers valid 128-bit
`ID_WWN_WITH_EXTENSION` values. Existing fallback behavior is retained. These
are independent community modifications, unsupported by TrueNAS. The upstream
commit, minimal unified patches and hashes make the changed source
reconstructable without including a full modified middleware tree.

The GNU license texts retain their original Free Software Foundation copyright
notices. No warranty is provided; see the included licenses.
