#!/bin/zsh
# Launch the openspecs_mcp arm for xrepo-v5-17-ephemeral-state-outlives-its-scope-medium in ONE fresh isolated headless session.
# Opus 5 + the openspecs-index knowledge-graph MCP server, and nothing else: no
# filesystem, no checkout, no network beyond the MCP endpoint, no subagents.
# Isolation is enforced by sandbox-exec (sandbox.sb): gold, the vault, every
# sibling arm's output AND all fourteen repository checkouts are unreadable.
set -e
ARM=/Users/sauravverma/programs/benchmarks/react-ecosystem/cross-repo/xrepo-v5-17-ephemeral-state-outlives-its-scope-medium/openspecs_mcp
CWD=/tmp/xrepo-v5-runs-mcp/xrepo-v5-17-ephemeral-state-outlives-its-scope-medium
PROFILE=/tmp/xrepo-v5-sbx/xrepo-v5-mcp.sb
CLAUDE=/Users/sauravverma/.nvm/versions/node/v24.9.0/bin/claude
PROBE=$ARM/preflight.py

# --- gate 1: the index must be live and must cover the pinned roster ---
python3 $PROBE || { echo "PREFLIGHT FAILED - not launching"; exit 1; }

# --- gate 2: never re-attempt a case in an arm that already ran ---
[ -f $ARM/raw_response.json ] && { echo "ALREADY RUN - refusing to re-attempt"; exit 1; }

# --- gate 3: the sandbox must be blinding the gold ---
sandbox-exec -f $PROFILE /bin/cat /Users/sauravverma/programs/benchmarks/react-ecosystem/cross-repo/xrepo-v5-17-ephemeral-state-outlives-its-scope-medium/golden.json >/dev/null 2>&1 \
  && { echo "SANDBOX LEAK: golden.json is readable"; exit 1; }

# --- gate 4: and the checkouts too - this arm must not be able to read a repo ---
for R in redux redux-toolkit react-redux reselect redux-thunk react jotai zustand db xyflow query table tldraw redux-devtools; do
  sandbox-exec -f $PROFILE /bin/ls /Users/sauravverma/programs/benchmarks/react-ecosystem/$R >/dev/null 2>&1 \
    && { echo "SANDBOX LEAK: checkout $R is readable"; exit 1; }
done

# openspecs-index tool surface, named explicitly (a bare prefix is not enough here)
T=(roll_call blueprint stakeout manhunt dragnet cross_repo_lookup case_file case_notes cold_case collateral_damage evidence_locker file_a_complaint interrogation kingpin mugshot paper_trail pull_the_evidence read_the_fine_print shakedown the_receipts)
ALLOW=$(printf "mcp__openspecs-index__%s," "${T[@]}"); ALLOW=${ALLOW%,}
DENY="Bash,Read,Write,Edit,Grep,Glob,WebFetch,WebSearch,Task,Agent,NotebookEdit,BashOutput,KillShell,TodoWrite"

mkdir -p $CWD
export DISABLE_PROMPT_CACHING=1
ST=$(date +%s)
( cd $CWD && sandbox-exec -f $PROFILE "$CLAUDE" -p "$(cat $ARM/run_prompt.txt)" \
    --model claude-opus-5 \
    --setting-sources "" --disable-slash-commands \
    --mcp-config /tmp/xrepo-v5-sbx/mcp_openspecs.json --strict-mcp-config \
    --allowedTools "$ALLOW" --disallowedTools "$DENY" \
    --permission-mode bypassPermissions \
    --max-budget-usd 30 \
    --output-format json < /dev/null ) > $ARM/raw_response.json 2> $ARM/stderr.log
echo "wall_seconds=$(( $(date +%s) - ST ))" > $ARM/wall.txt
echo "arm finished: xrepo-v5-17-ephemeral-state-outlives-its-scope-medium $(cat $ARM/wall.txt)"
