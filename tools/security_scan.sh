#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    printf 'Install local scanners with: brew install gitleaks osv-scanner\n' >&2
    exit 2
  fi
}

require_command gitleaks
require_command osv-scanner

printf '%s\n' '== gitleaks: repository history =='
gitleaks git --no-banner --redact --exit-code 1 "$PROJECT_ROOT"

printf '%s\n' '== gitleaks: working tree =='
gitleaks dir --no-banner --redact --exit-code 1 "$PROJECT_ROOT"

printf '%s\n' '== OSV-Scanner: project dependencies =='
osv-scanner scan source --recursive --allow-no-lockfiles "$PROJECT_ROOT"

printf '%s\n' 'Security scans passed.'
