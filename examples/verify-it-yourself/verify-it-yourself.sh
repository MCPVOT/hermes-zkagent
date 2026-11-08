#!/usr/bin/env bash
# Check this receipt without trusting the plugin, the agent, or the author.
#
# Downloads the pinned zkagent release, checks it against the published SHA256SUMS, then runs
# two verifications: the real proof must accept, and a proof with one hex character changed must
# reject. Nothing here needs a secret key; verification uses the public vk.bin beside this script.
set -u
cd "$(dirname "$0")"

VERSION="v0.4.0+2026-11-08"
BASE="https://github.com/MCPVOT/zkagent/releases/download/${VERSION}"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)  ASSET="zkagent-linux-x86_64" ;;
  Darwin-arm64)  ASSET="zkagent-macos-aarch64" ;;
  *) echo "unsupported platform: $(uname -s)-$(uname -m)"; exit 2 ;;
esac

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
echo "== fetching $ASSET ($VERSION)"
curl -fsSL -o "$WORK/$ASSET" "$BASE/$ASSET" || { echo "download failed"; exit 2; }
curl -fsSL -o "$WORK/SHA256SUMS" "$BASE/SHA256SUMS" || { echo "checksums unavailable"; exit 2; }

EXPECTED="$(grep " $ASSET\$" "$WORK/SHA256SUMS" | cut -d' ' -f1)"
ACTUAL="$(shasum -a 256 "$WORK/$ASSET" 2>/dev/null | cut -d' ' -f1 || sha256sum "$WORK/$ASSET" | cut -d' ' -f1)"
if [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "CHECKSUM MISMATCH: expected $EXPECTED, got $ACTUAL"; exit 2
fi
echo "== checksum ok: $ACTUAL"
chmod +x "$WORK/$ASSET"

RUN="$(ls -d run-* | head -1)"
echo
echo "== 1. the real proof"
"$WORK/$ASSET" --json verify --keys keys --policy "$RUN/policy.json" \
  --receipt "$RUN/receipt.json" --proof "$RUN/proof.json"
GOOD=$?
echo "   exit $GOOD (0 means accept)"

echo
echo "== 2. one hex character changed in the proof"
"$WORK/$ASSET" --json verify --keys keys --policy "$RUN/policy.json" \
  --receipt "$RUN/receipt.json" --proof "$RUN/proof-tampered.json"
BAD=$?
echo "   exit $BAD (1 means reject)"

echo
if [ "$GOOD" -eq 0 ] && [ "$BAD" -eq 1 ]; then
  echo "PASS: the receipt verifies, and the tampered copy does not."
  exit 0
fi
echo "FAIL: expected accept then reject, got $GOOD then $BAD."
exit 1
