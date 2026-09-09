#!/bin/zsh
# Prepare one cal.com case for a gitnexus arm: per-case registry, MCP config, prompt.
#   ./setup_gitnexus_arm.sh <sha>
set -e
ROOT=/Users/sauravverma/programs/benchmarks/react-ecosystem
SHA="${1:?sha}"
CASE=$ROOT/cal.com.processed/$SHA
ARM=$CASE/claudecli_opus5_mcp_gitnexus
HOME_DIR=$CASE/_gnx_home

[ -d "$CASE/repo/.gitnexus" ] || { echo "FAIL: no gitnexus index at $CASE/repo/.gitnexus - run analyze first"; exit 1; }
[ "$(git -C $CASE/repo rev-parse HEAD)" = "$SHA" ] || { echo "FAIL: checkout drift"; exit 1; }

# Per-case registry holding exactly this repo. Without it the shared registry lists
# every indexed cal.com commit under the same name and the arm can query the wrong one.
mkdir -p "$HOME_DIR" "$ARM"
GITNEXUS_HOME="$HOME_DIR" gitnexus index "$CASE/repo" >/dev/null 2>&1
N=$(GITNEXUS_HOME="$HOME_DIR" gitnexus list 2>/dev/null | sed -n 's/.*Indexed Repositories (\([0-9]*\)).*/\1/p' | head -1)
N=${N:-0}
[ "$N" = "1" ] || { echo "FAIL: per-case registry holds $N repos, expected exactly 1"; exit 1; }

cat > "$ARM/mcp_gitnexus.json" <<JSON
{"mcpServers":{"gitnexus":{"type":"stdio","command":"$(which gitnexus)","args":["mcp"],"env":{"GITNEXUS_HOME":"$HOME_DIR"}}}}
JSON
echo "prepared $SHA: registry=1 repo, config written"
