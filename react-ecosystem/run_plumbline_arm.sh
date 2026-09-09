#!/bin/zsh
# Execute one plumbline arm's prompt under the mandatory RUN CONDITIONS.
#
#   Usage: ./run_plumbline_arm.sh <case_dir> <arm_name> [prompt_file] [--fresh]
#
# Run it from the benchmark root. All paths are relative to it.
# Prompt defaults to <case_dir>/<arm_name>/{run_prompt,retrieval_prompt,prompt}.txt.
#
# --fresh re-measures a case whose arm is already spent: it copies that arm's prompt
# into the next free <arm>_vN and runs THAT, leaving the original answer untouched.
# Nothing needs to be told the commit — for cal.com the case directory IS the commit,
# and either way the prompt itself pins COMMIT / the per-repo commit table, so copying
# the prompt carries the revision with it.
#
# Headless `-p` with caching disabled, because the arm's own RUN CONDITIONS require
# a fresh session with cost and cache accounted for. An interactive session starts
# warm and writes no cost.json, which is what made the 2026-09-09 f66fffd1 run
# not-comparable.
set -e

FRESH=0
ARGS=()
for a in "$@"; do
  [ "$a" = "--fresh" ] && { FRESH=1; continue; }
  ARGS+=("$a")
done

CASE="${ARGS[1]:?usage: ./run_plumbline_arm.sh <case_dir> <arm_name> [prompt_file] [--fresh]}"
ARM="${ARGS[2]:?arm name}"
ARMDIR="$CASE/$ARM"
MCP="${MCP_CONFIG:-mcp_plumbline.json}"

PROMPT="${ARGS[3]:-}"
if [ -z "$PROMPT" ]; then
  for f in run_prompt.txt retrieval_prompt.txt prompt.txt; do
    [ -f "$ARMDIR/$f" ] && { PROMPT="$ARMDIR/$f"; break; }
  done
fi
[ -f "$PROMPT" ] || { echo "FAIL: no prompt file in $ARMDIR"; exit 1; }
[ -f "$MCP" ] || { echo "FAIL: no MCP config at $MCP (override with MCP_CONFIG=)"; exit 1; }

# One attempt per arm: a re-attempt is a warm run against a case already seen.
# Check ranked.json and result.json too — older arms were finalized without a
# raw_response.json, and keying on that alone would silently overwrite an answer.
SPENT=0
for evidence in raw_response.json ranked.json result.json; do
  [ -f "$ARMDIR/$evidence" ] && SPENT=1
done

if [ $SPENT -eq 1 ]; then
  if [ $FRESH -eq 0 ]; then
    echo "FAIL: $ARMDIR has already been run."
    echo "      Re-run it as a new arm with:  $0 $CASE $ARM --fresh"
    exit 1
  fi
  # Next free suffix. The prompt is the ONLY thing copied: results from the old arm
  # would make the new one look already-run, and would be scored as if they were its.
  BASE="${ARM%_v[0-9]##}"
  n=2
  while [ -e "$CASE/${BASE}_v${n}" ]; do n=$((n+1)); done
  NEWARM="${BASE}_v${n}"
  mkdir -p "$CASE/$NEWARM"
  cp "$PROMPT" "$CASE/$NEWARM/$(basename "$PROMPT")"
  echo "--fresh: $ARM is spent — running $NEWARM instead (prompt copied, commit pinned inside it)"
  ARM="$NEWARM"
  ARMDIR="$CASE/$ARM"
  PROMPT="$ARMDIR/$(basename "$PROMPT")"
elif [ $FRESH -eq 1 ]; then
  echo "--fresh ignored: $ARMDIR has no results yet, running it directly."
fi

mkdir -p "$ARMDIR"
export DISABLE_PROMPT_CACHING=1

# Named tool by tool: a bare prefix does not gate reliably, and a run that silently
# gains Bash or Grep is a bare-arm run wearing this label.
T=(roll_call blueprint stakeout manhunt dragnet cross_repo_lookup case_file case_notes \
   cold_case collateral_damage evidence_locker file_a_complaint interrogation kingpin \
   mugshot paper_trail pull_the_evidence read_the_fine_print shakedown the_receipts)
ALLOW=$(printf "mcp__plumbline__%s," "${T[@]}"); ALLOW=${ALLOW%,}
ALLOW="$ALLOW,Write"   # the prompt requires commands.log / output.log / raw_response.json
# Write is the ONLY non-MCP tool granted. Read/Grep/Glob stay denied: they would let the
# arm answer off the checkout, which is the failure this whole gate exists to catch.
# Monitor runs arbitrary shell, so it belongs here beside Bash: on 2026-09-09 an arm called it
# to build its own cost.json and was stopped only because --allowedTools is a whitelist, not
# because this list anticipated it. Loosen the whitelist and it is an open door to the checkout.
DENY="Bash,Monitor,Read,Edit,Grep,Glob,WebFetch,WebSearch,Task,NotebookEdit,BashOutput,KillShell"

ST=$(date +%s)
( cd "$CASE" && claude -p "$(cat "$OLDPWD/$PROMPT")" \
    --model claude-opus-5 \
    --mcp-config "$OLDPWD/$MCP" --strict-mcp-config \
    --allowedTools "$ALLOW" --disallowedTools "$DENY" \
    --output-format json < /dev/null ) > "$ARMDIR/raw_response.json" 2> "$ARMDIR/stderr.log"
echo "wall_seconds=$(( $(date +%s) - ST ))" > "$ARMDIR/wall.txt"

./finalize_arm.py "$CASE" "$ARM"

# --fresh may have redirected the run into a new arm; phase 2 must score THAT one,
# not the spent arm the caller named.
echo "$ARM" > "$CASE/.last_arm"

echo
echo "next: python3 score_arm.py $CASE $ARM"
