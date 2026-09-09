#!/bin/zsh
# Phase 1 for an opencode arm. Phase 2 is unchanged: python3 score_arm.py <case> <arm>
#
#   MODEL=opencode/tencent/hy4-preview ./run_opencode_arm.sh <case_dir> <arm_name> [prompt_file]
#
# WHY THIS IS A SEPARATE SCRIPT FROM run_retrieval_arm.sh
# -------------------------------------------------------
# opencode has no --mcp-config and no --allowedTools/--disallowedTools. Its whole
# tool surface comes from the GLOBAL ~/.config/opencode/opencode.jsonc, so a run
# cannot be gated per-arm the way every claude arm is. opencode also ships its own
# Read/Grep/Bash/Write, which are NOT disabled by the MCP config. An opencode arm is
# therefore NOT surface-comparable to a headless claude arm, and its cost.json must
# say so — the existing opencode_hy4_mcp_plumbline_v1 arms already record exactly
# that. Treat these as a separate series, never as a row against the gated arms.
set -e
ROOT=${0:A:h}
CASE="${1:?usage: MODEL=<provider/model> ./run_opencode_arm.sh <case_dir> <arm_name> [prompt_file]}"
ARM="${2:?arm name}"
MODEL="${MODEL:?set MODEL, e.g. MODEL=opencode/tencent/hy4-preview}"
ARMDIR="$ROOT/$CASE/$ARM"

PROMPT="${3:-}"
if [ -z "$PROMPT" ]; then
  for f in run_prompt.txt retrieval_prompt.txt prompt.txt; do
    [ -f "$ARMDIR/$f" ] && { PROMPT="$ARMDIR/$f"; break; }
  done
fi
[ -f "$PROMPT" ] || { echo "FAIL: no prompt file in $ARMDIR"; exit 1; }

# One attempt per arm, same rule as the claude runners.
for e in raw_response.json ranked.json result.json; do
  [ -f "$ARMDIR/$e" ] && { echo "FAIL: $ARMDIR has already been run ($e present)"; exit 1; }
done

echo "  case:  $CASE"
echo "  arm:   $ARM"
echo "  model: $MODEL"
echo "  mcp:   from ~/.config/opencode/opencode.jsonc (global; NOT per-run gated)"

ST=$(date +%s)
( cd "$ROOT/$CASE" && opencode run --pure --format json -m "$MODEL" "$(cat "$PROMPT")" ) \
  > "$ARMDIR/raw_response.json" 2> "$ARMDIR/stderr.log" || true
echo "wall_seconds=$(( $(date +%s) - ST ))" > "$ARMDIR/wall.txt"

# finalize_arm.py parses claude -p's envelope ({result, session_id, total_cost_usd});
# opencode --format json emits a different shape, so extraction is done here.
python3 "$ROOT/finalize_opencode_arm.py" "$CASE" "$ARM" "$MODEL"
echo
echo "next: python3 score_arm.py $CASE $ARM"
