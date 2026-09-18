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

# ROSTER GATE. The prompt hands the arm a table of 16 repos at pinned commits; if the
# index cannot serve one of them, the run scores against a repo the arm could never
# reach, and nothing downstream can tell that apart from the arm searching badly.
# manifest.json drifted exactly this way — five roster repos absent, one knowledgeId
# that existed nowhere — so the check is done against the LIVE index, every run, and
# is not something anyone has to remember.
#
# Exit 2 is "server did not answer": that is a reason to stop, not to proceed blind,
# because an unreachable index looks identical to an empty one from inside the arm.
# SKIP_ROSTER_GATE=1 is for replaying an arm offline against artifacts already on disk.
# The roster checked is THIS arm's own prompt, not one hardcoded case's. A single-repo
# arm needs its one repository indexed and nothing else, and was being failed for a
# roster its prompt never mentions.
if [ "${SKIP_ROSTER_GATE:-0}" != "1" ]; then
  python3 "$(dirname "$0")/refresh_roster.py" --prompt "$PROMPT" || {
    echo "FAIL: roster gate — the live index does not match the prompt's repo table."
    echo "      Fix the index or the prompt before spending a run. SKIP_ROSTER_GATE=1 overrides."
    exit 1
  }
fi

# CONCURRENCY GUARD. Everything below — the spent check, the --fresh suffix pick, and the
# run's own `>` redirection — assumes this process is the only one working on this arm, and
# none of it is atomic. Two launches a few seconds apart both read SPENT=0 while the roster
# gate above is still running, then both truncate the same raw_response.json, and the
# surviving answer is whichever CLI happens to exit last. A --fresh pair races the same way
# on the `_vN` scan and drops two runs into one directory. That is what cost the 2026-09-17
# run its result.
#
# shlock, not flock(1): flock is util-linux and is NOT present on macOS, so a guard written
# around it would be a no-op here. shlock records the holder's PID and — unlike a bare
# lockfile — clears a lock whose process is gone, so a crashed or ^C'd run does not wedge
# the arm until someone deletes a file by hand.
#
# Keyed on the arm as NAMED, before --fresh may redirect it, because the name is exactly
# what --fresh derives its suffix from.
LOCK="$ARMDIR/.run.lock"
if ! /usr/bin/shlock -f "$LOCK" -p $$; then
  echo "FAIL: $ARMDIR is already being run by PID $(tr -d ' \n' < "$LOCK" 2>/dev/null)."
  echo "      Concurrent launches truncate each other's raw_response.json. Wait for that"
  echo "      run to finish, or if the PID is dead re-run this and the stale lock clears."
  exit 1
fi
trap 'rm -f "$LOCK"' EXIT
trap 'rm -f "$LOCK"; exit 130' INT TERM

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
# turn to re-send full history at full input price — ~5x the bill for the same work.

# Named tool by tool: a bare prefix does not gate reliably, and a run that silently
# gains Bash or Grep is a bare-arm run wearing this label.
# rap_sheet was missing from this list until 2026-09-17, and from every per-arm run.sh
# with it. It is the server's Stage-0 BRIEF — what each repository is FOR, ~150 tokens
# per repo, one call, and UNBUDGETED: it costs nothing that could have gone to
# retrieval. The cost of the omission was not the call, it was the ranking. On a
# cross-repo question the sweep vocabulary (store, state, cache, sync, update) is
# common to nearly every repo on the roster, so an unbriefed run ranks repos that
# merely share words above the one the question is about. Worse, seven v5 prompts
# INSTRUCTED the arm to call rap_sheet while this whitelist withheld it, so those runs
# were told to take a step they could not take. Arms from v9 on have it; arms before
# v9 never did, and their tool_calls show rap_sheet 0 for that reason, not by choice.
T=(roll_call rap_sheet blueprint stakeout manhunt dragnet cross_repo_lookup case_file case_notes \
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

# ---------------------------------------------------------------- sandbox (default ON)
# Until 2026-09-14 this runner applied NO kernel sandbox at all: the per-arm sandbox.sb
# was written by _tools/build_arm_plumbline.py (which builds an arm named `plumbline_mcp`)
# and nothing ever invoked it, so isolation rested entirely on --disallowedTools. That is
# a whitelist on the model, not a guarantee about the process. Take it at the kernel too.
#
# The profile is generated here with ABSOLUTE paths — sandbox-exec does NOT expand `~` in
# a (subpath ...), so a tilde silently turns the gold-blinding deny into a no-op.
# The one re-opened path is mcp_plumbline.json: the CLI itself must read it to bind the
# server, and the blanket deny would otherwise kill the run with EPERM before turn one.
# Redirections are opened by THIS shell, outside the sandbox, so the deny on file-write*
# does not stop raw_response.json / stderr.log from being written.
#
# Set NO_SANDBOX=1 to reproduce a pre-2026-09-14 run under the old conditions.
BENCH_ABS=$(cd "$(dirname "$0")" && pwd)
case "$MCP" in /*) MCP_ABS="$MCP" ;; *) MCP_ABS="$BENCH_ABS/$MCP" ;; esac
# Per-arm, not shared: concurrent runs would otherwise race on one profile file, and a
# reader hitting it mid-rewrite gets a truncated profile and a silently broken sandbox.
SLUG=$(echo "${CASE}_${ARM}" | tr -c 'A-Za-z0-9._-' '_')
PROFILE="${TMPDIR:-/tmp}/plumbline_arm.${SLUG}.sb"
cat > "$PROFILE" <<SBEOF
(version 1)
(allow default)
(deny file-read* (subpath "$BENCH_ABS"))
(allow file-read* (literal "$MCP_ABS"))
(deny file-write* (subpath "$BENCH_ABS"))
(deny file-read* (subpath "$HOME/.claude/projects"))
(deny file-read* (subpath "$HOME/.claude/history.jsonl"))
;; ...but a run MUST read its OWN overflowed tool results. An over-cap MCP result is
;; spilled to <projects>/<cwd-slug>/<session>/tool-results/<tool>-<ts>.txt and the
;; model is told to grep it; the deny above used to swallow that, losing the call with
;; nothing in the transcript to say so (xrepo-m09 v8 run 3, a 1,780-line stakeout).
;; RUN_PROJDIR is unique per run, so this re-opens only this run's spills — never a
;; sibling run's, and never a .jsonl.
(allow file-read* (require-all
                    (subpath (param "RUN_PROJDIR"))
                    (regex #"/tool-results(/|$)")))
SBEOF

. "$BENCH_ABS/arm_run_env.sh"
arm_run_env_init "${CASE}_${ARM}"

: "${NO_SANDBOX:=0}"
if [ "$NO_SANDBOX" = "1" ]; then
  SBX=()
  echo "WARNING: NO_SANDBOX=1 — running unsandboxed (pre-2026-09-14 conditions)"
else
  # Gate both ways before spending anything: gold must be unreadable AND the MCP config
  # must still be readable. A profile that fails either way is a broken measurement, and
  # the failure is silent — an unreadable config yields a 0-byte raw_response.json.
  # Which file holds the gold differs by case family: cross-repo uses golden.json,
  # cal.com.processed uses gold.json. Gate on the one that EXISTS — catting a file that
  # is merely absent fails for the wrong reason and passes this check vacuously.
  GOLDF=""
  for g in "$CASE/golden.json" "$CASE/gold.json"; do [ -f "$g" ] && { GOLDF="$g"; break; }; done
  [ -n "$GOLDF" ] || { echo "FAIL: no gold file in $CASE — cannot prove the sandbox blinds it"; exit 1; }
  sandbox-exec -D RUN_PROJDIR="$RUN_PROJDIR" -f "$PROFILE" /bin/cat "$GOLDF" >/dev/null 2>&1 \
    && { echo "FAIL: SANDBOX LEAK — $GOLDF is readable under $PROFILE"; exit 1; }
  sandbox-exec -D RUN_PROJDIR="$RUN_PROJDIR" -f "$PROFILE" /bin/cat "$MCP_ABS" >/dev/null 2>&1 \
    || { echo "FAIL: sandbox denies $MCP — the CLI could not bind the MCP server"; exit 1; }
  arm_run_env_probe "$PROFILE" || exit 1
  SBX=(sandbox-exec -D RUN_PROJDIR="$RUN_PROJDIR" -f "$PROFILE")
  echo "sandbox: $PROFILE (gold denied, MCP config readable)"
fi

# cwd is a neutral directory OUTSIDE the benchmark tree. It used to be the case dir, which
# the deny above makes unreadable — the CLI probes its cwd for project settings, so leaving
# it inside a denied tree invites failures unrelated to the measurement.

ST=$(date +%s)
# Write to a per-run filename and LINK it onto the canonical one, instead of redirecting
# straight at raw_response.json. `>` truncates when the redirection is SET UP — before the
# CLI has emitted a byte — so the canonical name would sit there as a live 0-byte file for
# the entire run, and `--output-format json` writes the whole answer only at the end. Anything
# that reads it mid-run sees an empty answer, and the spent check above cannot tell that file
# apart from a finished one. `ln` is atomic and refuses an existing target, so the canonical
# name appears exactly once and is already complete when it does.
RUNSTAMP=$(date +%Y%m%dT%H%M%S)
RAW="$ARMDIR/raw_response.$RUNSTAMP.json"
( cd "$RUNCWD" && "${SBX[@]}" claude -p "$(cat "$OLDPWD/$PROMPT")" \
    --model claude-opus-5 \
    --mcp-config "$OLDPWD/$MCP" --strict-mcp-config \
    --allowedTools "$ALLOW" --disallowedTools "$DENY" \
    --output-format json < /dev/null ) > "$RAW" 2> "$ARMDIR/stderr.log"
echo "wall_seconds=$(( $(date +%s) - ST ))" > "$ARMDIR/wall.txt"
ln "$RAW" "$ARMDIR/raw_response.json" || {
  echo "FAIL: $ARMDIR/raw_response.json appeared while this run was in flight; refusing to"
  echo "      overwrite it. THIS run's answer is intact and unscored at:"
  echo "      $RAW"
  exit 1
}
arm_run_env_assert

./finalize_arm.py "$CASE" "$ARM"

# --fresh may have redirected the run into a new arm; phase 2 must score THAT one,
# not the spent arm the caller named.
echo "$ARM" > "$CASE/.last_arm"

echo
echo "next: python3 score_arm.py $CASE $ARM"
