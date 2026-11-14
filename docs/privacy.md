# Sharing diagnostics without identifiers

This repository contains synthetic examples and aggregate observations. It intentionally excludes the private troubleshooting conversation, raw NAS output, serial numbers, WWNs, hostnames, IP addresses, credentials, pool and dataset names, and support bundles.

Review both file contents and Git history before publishing changes. Removing a secret or identifier from the current version does not remove it from earlier commits.

## Prefer aggregate results

Publish counts and pass/fail observations using the [verification template](verification.md). Keep the raw evidence on the machine or in an access-controlled location outside this repository.

Commands that inspect udev, middleware disks, service status, or journals can reveal identifying details. Treat their output as private until reviewed. Avoid screenshots of the NAS UI or terminal; they often expose more information than the intended result.

## Synthetic examples

When a report needs to show a collision, generate invented values instead of truncating real ones. Use consistent labels such as `SYNTHETIC-DRIVE-01` for a serial. Give its two LUNs the same invented short identifier and different invented full identifiers. Label all such output **synthetic**.

Do not publish hashed serials or WWNs as a substitute for redaction: stable hashes still link records and are unnecessary for proving aggregate uniqueness. Do not upload configuration exports, debug archives, shell history, or arbitrary environment dumps.

## Review before sharing

- Include only the exact release, product models, counts, test phase, and result needed to reproduce the issue.
- Check filenames, image metadata, command prompts, logs, and exception messages for identifiers.
- Keep installation state, backups, downloaded source trees, and local diagnostic output out of Git.
- Replace copied output with a short human-reviewed summary when its privacy is uncertain.

If credentials were accidentally published, revoke them through their issuer; deleting the text alone is insufficient.
