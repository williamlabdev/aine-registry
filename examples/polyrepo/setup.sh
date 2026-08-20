#!/bin/sh
set -eu

base_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$base_dir/setup.py" "$@"
