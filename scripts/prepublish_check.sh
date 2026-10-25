#!/usr/bin/env bash
# Read-only check before publishing: lists the tracked files plus what `git add -A` would stage, and fails on private
# material.
#   scripts/prepublish_check.sh        # exit 0 = safe to publish everything listed
# Generic secret patterns are below. Personal identifiers to scan for (names of accounts, tenants, hosts) live in an
# untracked file, PITCHIQ_PRIVATE_PATTERNS (default: handoff/private_patterns.txt, one extended regex per line), so this
# script never publishes the strings it looks for. It never stages or commits anything.
set -euo pipefail
cd "$(dirname "$0")/.."
LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT
{ git ls-files; git add -A --dry-run | sed -E "s/^add '(.*)'$/\1/"; } | sort -u > "$LIST"
echo "files that would be published: $(wc -l < "$LIST")"
fail=0
if grep -E '^(handoff/|others/)|(^|/)\.env$|\.tar\.gz$|\.log$' "$LIST"; then
  echo "FAIL: private paths would be staged (above)"; fail=1
fi
PATTERN='[a-z0-9-]+\.services\.ai\.azure\.com|[a-z0-9-]+\.openai\.azure\.com|/subscriptions/[0-9a-f-]{8}|Microsoft Confidential|InstrumentationKey=|AccountKey=|SharedAccessKey=|BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20}|github_pat_'
PRIVATE="${PITCHIQ_PRIVATE_PATTERNS:-handoff/private_patterns.txt}"
if [ -f "$PRIVATE" ]; then
  extra="$(grep -v -E '^\s*(#|$)' "$PRIVATE" | paste -sd '|' -)"
  [ -n "$extra" ] && PATTERN="$PATTERN|$extra"
else
  echo "note: $PRIVATE not found; only generic secret patterns are checked"
fi
hits="$(grep -v -x scripts/prepublish_check.sh "$LIST" | tr '\n' '\0' | xargs -0 -r grep -I -l -E "$PATTERN" -- 2>/dev/null || true)"  # its own generic patterns are not private
if [ -n "$hits" ]; then
  echo "FAIL: private strings in:"; echo "$hits"
  fail=1
fi
big="$(tr '\n' '\0' < "$LIST" | xargs -0 -r du -k -- 2>/dev/null | awk '$1 > 10240 {print $2 " (" $1 " KB)"}')"
if [ -n "$big" ]; then echo "FAIL: files over 10 MB:"; echo "$big"; fail=1; fi
echo "total size: $(tr '\n' '\0' < "$LIST" | xargs -0 -r du -ck -- 2>/dev/null | tail -1 | cut -f1) KB"
[ "$fail" -eq 0 ] && echo "OK: nothing private would be published" || exit 1
