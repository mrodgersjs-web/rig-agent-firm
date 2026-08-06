#!/usr/bin/env bash
# Generate a shields.io-compatible endpoint badge from agent-firm status JSONs.
#
# Usage:
#   ./scripts/generate-dashboard.sh [status_dir] [output_file]
#
# Defaults:
#   status_dir = ./status
#   output_file = ./badge.json

set -euo pipefail

STATUS_DIR="${1:-status}"
BADGE_FILE="${2:-badge.json}"

if [[ ! -d "$STATUS_DIR" ]]; then
  echo "Status directory not found: $STATUS_DIR" >&2
  exit 1
fi

mkdir -p "$(dirname "$BADGE_FILE")"

online=0
offline=0
unknown=0
total=0

for f in "$STATUS_DIR"/*.json; do
  [[ -e "$f" ]] || continue
  total=$((total + 1))
  # Python is reliably present on GitHub Actions runners and most dev machines.
  status=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unknown'))" < "$f")
  case "$status" in
    online)  online=$((online + 1)) ;;
    offline) offline=$((offline + 1)) ;;
    *)       unknown=$((unknown + 1)) ;;
  esac
done

if [[ $total -eq 0 ]]; then
  color="lightgrey"
  message="no data"
elif [[ $offline -gt 0 ]]; then
  color="red"
  message="$online/$total online"
elif [[ $unknown -gt 0 ]]; then
  color="yellow"
  message="$online/$total online"
else
  color="brightgreen"
  message="$online/$total online"
fi

cat > "$BADGE_FILE" <<EOF
{
  "schemaVersion": 1,
  "label": "agent firm",
  "message": "$message",
  "color": "$color"
}
EOF

echo "Wrote $BADGE_FILE: $message ($color)"
