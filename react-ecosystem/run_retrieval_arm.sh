#!/bin/zsh
# Shared core for the non-plumbline retrieval arms. Do not call this directly —
# call one of the three thin entry points, which set SURFACE and nothing else:
#
#   ./run_bare_arm.sh      <case_dir> <arm_name> [prompt_file] [--fresh]
#   ./run_graphify_arm.sh  <case_dir> <arm_name> [prompt_file] [--fresh]
#   ./run_turbovec_arm.sh  <case_dir> <arm_name> [prompt_file] [--fresh]
#
# This is run_plumbline_arm.sh's mechanism, generalised over the tool surface, so
# every arm is measured by ONE code path instead of four. Before this, plumbline ran
# from a root script with a spent-arm guard and --fresh versioning while graphify,
# turbovec and bare ran from per-arm generated run.sh files — the bare cal.com arms
# had no script at all. Four mechanisms meant four sets of run conditions, and the
# conditions are the measurement.
#
# Phase 2 is separate and unchanged:  python3 score_arm.py <case_dir> <arm_name>
#
# Run from the benchmark root. All paths are relative to it.
set -e

[ -n "$SURFACE" ] || { echo "FAIL: SURFACE unset — call run_<surface>_arm.sh, not this script"; exit 1; }

# `exec` from the wrapper replaces $0 with this file, so error messages would tell the
# caller to re-run a script they never invoked. ME is the name they actually typed.
ME="./run_${SURFACE}_arm.sh"

FRESH=0
ARGS=()
for a in "$@"; do
  [ "$a" = "--fresh" ] && { FRESH=1; continue; }
  ARGS+=("$a")
done

CASE="${ARGS[1]:?usage: $ME <case_dir> <arm_name> [prompt_file] [--fresh]}"
ARM="${ARGS[2]:?arm name}"
ARMDIR="$CASE/$ARM"
ROOT="${0:A:h}"
IDX="$ROOT/cross-repo/_indexes"

# --- tool surface -----------------------------------------------------------
# Named tool by tool. A bare prefix does not gate reliably, and an arm that silently
# gains Bash or Read is a bare-arm run wearing another arm's label. Write is granted
# to every arm because the prompt's DELIVERABLES require it; it is the ONLY overlap.
case "$SURFACE" in
  bare)
    MCP=""
    ALLOW="Bash,Read,Grep,Glob,Write"
    # No MCP tool can be named in a denylist (the server is not loaded), so the gate
    # that matters here is --strict-mcp-config with no config: nothing to connect to.
    DENY="WebFetch,WebSearch,Task,NotebookEdit,Monitor"
    ;;
  graphify)
    MCP="${MCP_CONFIG:-$IDX/graphify_mcp/mcp_graphify.json}"
    T=(query_graph get_node get_neighbors get_community god_nodes graph_stats shortest_path)
    ALLOW=$(printf "mcp__graphify__%s," "${T[@]}"); ALLOW="${ALLOW%,},Write"
    DENY="Bash,Monitor,Read,Edit,Grep,Glob,WebFetch,WebSearch,Task,NotebookEdit,BashOutput,KillShell"
    ;;
  turbovec)
    MCP="${MCP_CONFIG:-$IDX/turbovec_mcp/mcp_turbovector.json}"
    T=(search get_file read_lines index_info)
    ALLOW=$(printf "mcp__turbovector__%s," "${T[@]}"); ALLOW="${ALLOW%,},Write"
    DENY="Bash,Monitor,Read,Edit,Grep,Glob,WebFetch,WebSearch,Task,NotebookEdit,BashOutput,KillShell"
    ;;
  serena)
    MCP="${MCP_CONFIG:-$IDX/serena_mcp/mcp_serena.json}"
    # Read-only retrieval surface. activate_project is REQUIRED here and is not needed by
    # any other surface: serena is single-project by construction, the cross-repo roster is
    # 15 separate projects, and without it the arm can only ever see whichever one happens
    # to be active. get_current_config is what lets it discover the other 14 by name.
    T=(activate_project get_current_config list_dir find_file read_file get_symbols_overview
       find_symbol find_referencing_symbols find_declaration find_implementations
       search_for_pattern)
    ALLOW=$(printf "mcp__serena__%s," "${T[@]}"); ALLOW="${ALLOW%,},Write"
    # execute_shell_command is serena's own Bash and would make this a bare arm wearing
    # serena's label — it can grep the tree and read golden.json. It is excluded by the
    # allow-list above (a whitelist), and named here so that stays deliberate rather than
    # incidental. Same for the write/refactor tools (create_text_file, replace_*, insert_*,
    # rename_symbol, safe_delete_symbol) and the memory tools, which persist state across
    # runs and would leak one case's findings into the next.
    DENY="Bash,Monitor,Read,Edit,Grep,Glob,WebFetch,WebSearch,Task,NotebookEdit,BashOutput,KillShell"
    ;;
  gitnexus)
    # MCP_CONFIG is REQUIRED here, unlike the other surfaces, and there is no default
    # on purpose. gitnexus resolves repositories through a registry, and every cal.com
    # commit registers under the same name "cal.com" — distinguished only by path. A
    # shared registry would let an arm query a different commit's index and report it
    # as this one's. Each case therefore carries its own GITNEXUS_HOME holding exactly
    # one repo, named in that case's mcp_gitnexus.json.
    [ -n "$MCP_CONFIG" ] || { echo "FAIL: gitnexus needs MCP_CONFIG=<case>/claudecli_opus5_mcp_gitnexus/mcp_gitnexus.json (per-case registry)"; exit 1; }
    MCP="$MCP_CONFIG"
    # Read-only retrieval tools only. `rename` is a multi-file WRITE and is excluded;
    # `detect_changes` reads uncommitted diffs and these trees are clean; `explain` and
    # `pdg_query` need the PDG substrate, which `analyze --pdg` did not build; `group_*`
    # need a configured repo group. Offering a tool that cannot work spends turns on it.
    T=(list_repos query cypher context impact trace check route_map tool_map shape_check api_impact)
    ALLOW=$(printf "mcp__gitnexus__%s," "${T[@]}"); ALLOW="${ALLOW%,},Write"
    DENY="Bash,Monitor,Read,Edit,Grep,Glob,WebFetch,WebSearch,Task,NotebookEdit,BashOutput,KillShell"
    ;;
  *)
    echo "FAIL: unknown SURFACE '$SURFACE'"; exit 1 ;;
esac

# Monitor is denied alongside Bash on the MCP arms: it runs arbitrary shell, and on
# 2026-09-09 a plumbline arm reached for it to build its own cost.json. It was stopped
# only because --allowedTools is a whitelist. Naming it keeps that from being luck.

PROMPT="${ARGS[3]:-}"
if [ -z "$PROMPT" ]; then
  for f in run_prompt.txt retrieval_prompt.txt prompt.txt; do
    [ -f "$ARMDIR/$f" ] && { PROMPT="$ARMDIR/$f"; break; }
  done
fi
[ -f "$PROMPT" ] || { echo "FAIL: no prompt file in $ARMDIR"; exit 1; }
# Resolve now: the run cds into $CASE, after which a bench-root-relative prompt path
# no longer resolves. ${:A} makes it absolute without requiring the file to move.
PROMPT="${PROMPT:A}"
if [ -n "$MCP" ]; then
  [ -f "$MCP" ] || { echo "FAIL: no MCP config at $MCP (override with MCP_CONFIG=)"; exit 1; }
fi

# --- gate: every checkout on its pinned commit ------------------------------
# The per-arm run.sh files this replaces each carried an inlined 15-line drift gate.
# Dropping it would be a regression, so it is kept and read from ONE roster —
# build_prompts.py's, the same one the prompt's commit table is rendered from, so the
# gate cannot drift from what the prompt claims. Skipped when the roster is absent
# (the single-repo cal.com cases pin their commit inside the prompt instead).
if [ -f "$ROOT/cross-repo/_tools/build_prompts.py" ]; then
  echo "checkout gate: verifying roster against pinned commits"
  python3 - "$ROOT" <<'PY' || exit 1
import subprocess, sys, os
sys.path.insert(0, os.path.join(sys.argv[1], "cross-repo/_tools"))
from build_prompts import ROSTER
bad, n_checked = [], 0
for repo, commit in ROSTER:
    d = os.path.join(sys.argv[1], repo)
    if not os.path.isdir(d):
        continue
    head = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    n_checked += 1
    if head != commit:
        bad.append(f"  {repo}: at {head[:12]}, pinned {commit[:12]}")
if bad:
    print("CHECKOUT DRIFT — refusing to run:\n" + "\n".join(bad))
    sys.exit(1)
print(f"checkout gate: {n_checked} repositories on their pinned commits")
PY
fi

# --- gate: one attempt per arm ----------------------------------------------
# A re-attempt is a warm run against a case already seen. ranked.json and result.json
# are checked too: older arms were finalized without a raw_response.json, and keying
# on that alone would silently overwrite an answer that was already paid for.
SPENT=0
for evidence in raw_response.json ranked.json result.json; do
  [ -f "$ARMDIR/$evidence" ] && SPENT=1
done

if [ $SPENT -eq 1 ]; then
  if [ $FRESH -eq 0 ]; then
    echo "FAIL: $ARMDIR has already been run."
    echo "      Re-run it as a new arm with:  $ME $CASE $ARM --fresh"
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
  echo "--fresh: $ARM is spent — running $NEWARM instead (prompt copied, commits pinned inside it)"
  ARM="$NEWARM"
  ARMDIR="$CASE/$ARM"
  PROMPT="$ARMDIR/$(basename "$PROMPT")"
elif [ $FRESH -eq 1 ]; then
  echo "--fresh ignored: $ARMDIR has no results yet, running it directly."
fi

mkdir -p "$ARMDIR"

# Headless `-p` with caching disabled: the RUN CONDITIONS require a fresh session with
# cost and cache accounted for. An interactive session starts warm and writes no
# cost.json, which is what made the 2026-09-09 f66fffd1 run not-comparable.
# Caching left ON (2026-09-14, by request): DISABLE_PROMPT_CACHING=1 was forcing every
# turn to re-send full history at full input price — ~5x the bill for the same work.
# Set DISABLE_PROMPT_CACHING=1 in the environment to restore the cold-run condition.
: "${DISABLE_PROMPT_CACHING:=0}"; export DISABLE_PROMPT_CACHING

MCPARGS=()
[ -n "$MCP" ] && MCPARGS=(--mcp-config "$MCP")
# --strict-mcp-config is passed even with no config: it is what stops the bare arm
# inheriting an MCP server from the user's own settings and quietly becoming an
# indexed arm.
if [ -n "$DRYRUN" ]; then
  echo "DRYRUN — would run:"
  echo "  cwd:     $CASE"
  echo "  prompt:  $PROMPT ($(wc -c < "$PROMPT" | tr -d ' ') bytes)"
  echo "  mcp:     ${MCP:-<none, --strict-mcp-config with no config>}"
  echo "  allow:   $ALLOW"
  echo "  deny:    $DENY"
  echo "  writes:  $ARMDIR/{raw_response.json,stderr.log,wall.txt}"
  exit 0
fi

# --- kernel-enforced gold blindness -----------------------------------------
# The run cds into $CASE, where golden.json sits. The BARE arm holds Read/Grep/Glob/Bash,
# so "do not read golden.json" in the prompt is an instruction, not a control — the arm
# can simply read the answer and score 1.0. The older per-arm run.sh files wrapped every
# run in sandbox-exec (their cost.json records gold_readable: false); generalising them
# into this script dropped that, leaving the canonical path weaker than what it replaced.
# Restored here: when the arm carries a sandbox.sb, the run happens inside it.
SANDBOX=()
RUNCWD="$CASE"
if [ -f "$ARMDIR/sandbox.sb" ]; then
  SANDBOX=(/usr/bin/sandbox-exec -f "${ARMDIR:A}/sandbox.sb")
  # The profile denies the whole benchmark tree, and a process cannot run in a directory
  # it may not read: with cwd=$CASE every sandboxed run died as `pwd: .: Operation not
  # permitted`, surfacing only as "unknown error" and a zero-byte raw_response.json.
  # The per-arm run.sh files this script replaced used a fresh empty directory outside the
  # tree (their cost.json says so: "cwd was a fresh empty directory"); that is restored here.
  RUNCWD="/tmp/xrepo-runs/$(basename "$CASE")-$ARM"
  rm -rf "$RUNCWD"; mkdir -p "$RUNCWD"
  echo "isolation: sandbox-exec, profile $ARMDIR/sandbox.sb (cwd $RUNCWD)"
else
  echo "WARNING: no sandbox.sb in $ARMDIR — gold is readable from the run's cwd."
  echo "         For the bare arm that is disqualifying; add a profile before trusting the score."
fi

ST=$(date +%s)
( cd "$RUNCWD" && "${SANDBOX[@]}" claude -p "$(cat "$PROMPT")" \
    --model claude-opus-5 \
    "${MCPARGS[@]}" --strict-mcp-config \
    --setting-sources "" --disable-slash-commands \
    --allowedTools "$ALLOW" --disallowedTools "$DENY" \
    --output-format json < /dev/null ) > "$ARMDIR/raw_response.json" 2> "$ARMDIR/stderr.log"
echo "wall_seconds=$(( $(date +%s) - ST ))" > "$ARMDIR/wall.txt"

"$ROOT/finalize_arm.py" "$CASE" "$ARM"

# --fresh may have redirected the run into a new arm; phase 2 must score THAT one,
# not the spent arm the caller named.
echo "$ARM" > "$CASE/.last_arm"

echo
echo "next: python3 score_arm.py $CASE $ARM"
