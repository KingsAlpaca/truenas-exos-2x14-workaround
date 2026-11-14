#!/bin/sh
# SPDX-License-Identifier: LGPL-3.0-only
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 "$SCRIPT_DIR/workaround.py" rollback "$@"
