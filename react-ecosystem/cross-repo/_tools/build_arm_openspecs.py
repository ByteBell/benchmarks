#!/usr/bin/env python3
"""Materialise the openspecs_mcp arm (Opus 5 + openspecs-index MCP, NO filesystem)
for a set of xrepo-v5 cases: run_prompt.txt, mcp_openspecs.json, sandbox.sb, run.sh.

The arm's whole search surface is the openspecs-index knowledge-graph MCP server.
Unlike claude_code_opus5, the fourteen checkouts are NOT readable: the sandbox
denies the benchmark root wholesale and re-opens nothing, so gold AND the repos
are both unreachable. A path in the answer can therefore only have come back
from an mcp__openspecs-index__* call - the arm cannot grep a tree it cannot see.

Isolation is kernel-enforced by sandbox-exec, not by convention:
  * the whole benchmark tree is denied with no re-allow, so golden.json,
    DONOTREADTHISFOLDER/, every sibling arm's results AND all fourteen
    checkouts fail with EPERM;
  * ~/.claude/projects is denied, so no transcript of any earlier session (nor
    this run's own) can be read back in;
  * cwd is a fresh empty directory per run, outside the benchmark tree;
  * --safe-mode is NOT usable here - it disables MCP servers, i.e. this arm's
    entire surface - so its guarantee is taken at the kernel instead: the
    user-level CLAUDE.md, skills, plugins, agents, commands, hooks and output
    styles are denied by the profile, and --setting-sources "" plus
    --disable-slash-commands close the flag-level path.
Network stays open: the MCP server is the surface, and it speaks HTTP.
"""
import json
import os
import re
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_prompts import BENCH_ROOT, CROSS, ROSTER

ARM = "openspecs_mcp"
CLAUDE = "/Users/sauravverma/.nvm/versions/node/v24.9.0/bin/claude"
SBX_DIR = "/tmp/xrepo-v5-sbx"
RUN_DIR = "/tmp/xrepo-v5-runs-mcp"
PROFILE = f"{SBX_DIR}/xrepo-v5-mcp.sb"
# The CLI opens --mcp-config from INSIDE the sandbox, and the whole benchmark
# tree is denied there, so the live copy has to sit outside it. The arm keeps
# an identical copy for the record.
MCP_CFG = f"{SBX_DIR}/mcp_openspecs.json"
HOME = os.path.expanduser("~")

# The MCP endpoint carries an access token, so it is NEVER written into source or
# into anything that lands in the repo. It comes from the environment, and an
# unset value is a hard error rather than a guessed default: a silently wrong
# endpoint would produce a run that looks clean and measured nothing.
#   export OPENSPECS_MCP_URL='http://localhost/mcp?access_token=...'
MCP_URL = os.environ.get("OPENSPECS_MCP_URL")
if not MCP_URL and __name__ == "__main__":
    sys.exit("OPENSPECS_MCP_URL is not set - export it before building an arm.")

# What gets written into the ARM DIRECTORY (and therefore into git) is redacted;
# the live config the CLI actually opens is written to SBX_DIR, outside the repo.
MCP_URL_REDACTED = re.sub(r"access_token=[^&]*", "access_token=${OPENSPECS_MCP_TOKEN}",
                          MCP_URL or "http://localhost/mcp?access_token=${OPENSPECS_MCP_TOKEN}")

TOOLS = ["roll_call", "blueprint", "stakeout", "manhunt", "dragnet",
         "cross_repo_lookup", "case_file", "case_notes", "cold_case",
         "collateral_damage", "evidence_locker", "file_a_complaint",
         "interrogation", "kingpin", "mugshot", "paper_trail",
         "pull_the_evidence", "read_the_fine_print", "shakedown",
         "the_receipts"]

# ---------------------------------------------------------------- prompt edits
# run_prompt.txt is prompt.txt with exactly three surface-dependent passages
# swapped. Everything else - the case id, the roster, the PROBLEM text, the TASK,
# the ranking rules, the cap, the output contract - is byte-identical to the
# arm-agnostic master, so the two arms answer the same question.

CHECKOUTS_OLD = f"""CHECKOUTS:  {BENCH_ROOT}/<repo>
            Each repository is checked out at exactly the commit below and at no
            other revision. The repositories at those commits are the whole world
            for this task: do not use any other revision, and do not rely on
            knowledge of these projects' later history."""

CHECKOUTS_NEW = """INDEX:      openspecs-index knowledge graph (MCP)
            These repositories are NOT checked out on this machine and no copy of
            them is readable from the filesystem. They reach you only as indexed
            knowledge, at exactly the commit below and at no other revision. The
            repositories at those commits are the whole world for this task: do
            not use any other revision, and do not rely on knowledge of these
            projects' later history."""

SURFACE_OLD = """- Use the search surface this arm gives you, and nothing else. Whatever that
  surface is, every path you name must have been found through it: a run that
  reaches the answer by another route is a void measurement, not a faster one."""

SURFACE_NEW = """- Use the openspecs-index MCP server, and nothing else. That is your whole
  surface: no filesystem - no Bash, Read, Grep or Glob, and no checkout to run
  them against - no WebSearch or WebFetch, no subagents. Every path you name
  must have come back from an mcp__openspecs-index__* call. A run that reaches
  the answer by another route is a void measurement, not a faster one.
- Work in two phases, LOCATE then TRACE, and do not begin the second until the
  first has touched every repository.
  LOCATE (cheap and broad): open with roll_call to get each repository's
  knowledgeId - an opaque string, taken from roll_call, never guessed. This
  organization indexes MANY more knowledge bases than the fourteen in your
  roster (unrelated codebases, PDFs, other projects), so discard every row that
  is not one of your fourteen, and do not lean on an omitted-knowledgeId sweep:
  it returns a result mixed with those non-roster knowledge bases, in which a
  roster repository's signal can be thin or absent even when that repository
  holds the file you need. Once you have grounded the symptom in one file, fire
  cross_repo_lookup and collateral_damage from that file in parallel - but note
  that these trace REAL import / package / wire edges, and this roster is
  explicitly sibling projects with no shared package graph, so they can
  legitimately return nothing for a repository that still belongs in your
  answer. A thin result from those two is not a clearance. Then run stakeout
  scoped to EACH remaining roster repository's own knowledgeId, querying the
  CONTRACT the problem describes rather than the symptom's vocabulary.
  TRACE (only afterwards): case_file / interrogation / the_receipts, on the
  repositories LOCATE actually surfaced something in.
- Judge a repository by its files, never by the shape of its result list. If a
  repository's results contain ANY source file whose path or purpose plausibly
  touches the contract, open it (case_file or the_receipts) before you discard
  that repository. A result list that is mostly docs, guides, benchmarks or
  examples is not evidence the repository is uninvolved - it is a signal to
  look past them at the source files sitting in the same list. Dropping a
  repository because its result list "looked like docs" is the most expensive
  mistake available to you here.
- The graph's prose fields (purpose, summary, businessContext) were written by
  an LLM at index time and are leads, not evidence. The structural fields -
  relativePath, qualifiedName, startLine/endLine, commitHash, call edges - come
  from a parser. Rank on structure, and confirm behaviour with the_receipts
  before you assert it."""

PIN_OLD = """- Every path must exist in that repository AT THE PINNED COMMIT. These working
  trees are NOT clean: they carry untracked files that are not part of the commit
  and are not part of this task. Confirm every path against the commit itself
  (git cat-file -e <commit>:<path>) and drop anything that does not resolve."""

PIN_NEW = """- Every path must exist in that repository AT THE PINNED COMMIT. Pass the pinned
  commitHash on every call that accepts one, and name only paths that came back
  carrying that commitHash. Drop anything you cannot anchor to it - you have no
  filesystem to fall back on, so an unanchored path is a guess."""

CHECKOUT_OLD = """- CHECKOUT: before searching, confirm your surface is actually live for THESE
  repositories at THESE commits. Verify the revision of every repository you
  intend to answer from before you trust anything you read out of it. If a
  repository is missing, or sits at a different revision than the one pinned
  above, STOP and say so plainly. Do not answer from parametric memory, from
  another revision, or from a partially materialised tree, and do not return a
  guessed list. A run against a missing or drifted checkout is a failed run to be
  relaunched - it is not an empty answer."""

CHECKOUT_NEW = """- INDEX COVERAGE: before searching, confirm the graph is actually live for THESE
  repositories at THESE commits. roll_call reports only each repository's NEWEST
  indexed commit, so a row showing a different commit does NOT mean the pinned
  one is absent - probe it by passing the pinned commitHash to a search before
  you conclude anything. A repository may also be absent from the graph
  altogether. Report plainly, in your reasoning, any roster repository you could
  not reach at the pinned commit, and answer only from what the graph actually
  returned: do not fill a gap from parametric memory, from another revision, or
  with a guessed list."""

COVERAGE_LEDGER = """COVERAGE LEDGER (write this out before your final JSON)
One line per roster repository that roll_call gave you a knowledgeId for. Name any
roster repository absent from the graph entirely and skip it. Every line ends in
exactly one of three words:

  EVIDENCED   you are naming at least one file in this repository
  CLEARED     you made at least ONE call scoped to this repository's own
              knowledgeId, you opened every plausible source file its results
              contained, and none of them owns the contract. If that first call
              surfaced NO plausible source file at all, make a second scoped
              attempt from a different angle - a re-phrased stakeout, or manhunt
              on a likely symbol or hook name - before writing CLEARED: a query
              that found nothing is a query problem at least as often as it is a
              fact about the repository. Do NOT spend a second call on a
              repository whose first result you have already inspected and ruled
              out on the files themselves; that call buys nothing.
  UNVERIFIED  anything less than that

You may not submit a final answer while any repository is UNVERIFIED - go and make
the calls first. CLEARED is a legitimate and common outcome: several of these
repositories may correctly end CLEARED, and a repository that genuinely does not
own this contract must NOT be given a file just to fill its row. The ledger exists
to prove you looked, not to make you find something.

"""

DELIVERABLES = """DELIVERABLES: the harness captures this run for you - it records the full transcript,
every tool call, and the cost, and it writes ranked.json from your final message. You
have no write tools; do not attempt to create files. Your only job is the ranked answer.

"""


def run_prompt(case):
    src = open(f"{CROSS}/{case}/prompt.txt").read()
    for old, new in ((CHECKOUTS_OLD, CHECKOUTS_NEW), (SURFACE_OLD, SURFACE_NEW),
                     (PIN_OLD, PIN_NEW), (CHECKOUT_OLD, CHECKOUT_NEW)):
        assert old in src, f"{case}: prompt.txt drifted, cannot find:\n{old[:80]}"
        src = src.replace(old, new)
    marker = "\nFINAL MESSAGE\n"
    assert marker in src
    return src.replace(
        marker, "\n" + COVERAGE_LEDGER + DELIVERABLES + "FINAL MESSAGE\n")


def profile():
    return f"""(version 1)
(allow default)

;; ---- benchmark tree: blind ALL of it, checkouts included, nothing re-opened ----
;; This arm's surface is the openspecs-index MCP server, so it has no business
;; reading anything under the benchmark root. Denying it wholesale (no allow rule
;; follows) means golden.json, DONOTREADTHISFOLDER/, every sibling arm's
;; ranked.json/result.json AND all fourteen checkouts fail with EPERM. A path in
;; the answer therefore cannot have come off disk.
(deny file-read* (subpath "{BENCH_ROOT}"))
(deny file-write* (subpath "{BENCH_ROOT}"))

;; ---- no session transcript is readable: not an earlier run's, not this one's ----
(deny file-read* (subpath "{HOME}/.claude/projects"))
(deny file-read* (subpath "{HOME}/.claude/history.jsonl"))

;; ---- the operator's customizations cannot reach the model ----
;; claude_code_opus5 got this from --safe-mode. This arm cannot use --safe-mode:
;; it disables MCP servers, which for this arm is the whole search surface. So the
;; same guarantee is taken at the kernel instead, and the CLI's own auth files
;; (~/.claude/.credentials.json, ~/.claude.json) are deliberately left readable.
(deny file-read* (subpath "{HOME}/.claude/CLAUDE.md"))
(deny file-read* (subpath "{HOME}/.claude/skills"))
(deny file-read* (subpath "{HOME}/.claude/plugins"))
(deny file-read* (subpath "{HOME}/.claude/agents"))
(deny file-read* (subpath "{HOME}/.claude/commands"))
(deny file-read* (subpath "{HOME}/.claude/hooks"))
(deny file-read* (subpath "{HOME}/.claude/output-styles"))

;; Network is deliberately left open: the MCP server IS the search surface and it
;; speaks HTTP on localhost. seatbelt cannot scope a network rule to one port on
;; this profile version, so purity of the surface is enforced by the tool
;; allowlist (--allowedTools / --disallowedTools) and audited from the transcript
;; by finalize.py, which fails the run on any non-MCP tool call.
"""


def run_sh(case):
    arm = f"{CROSS}/{case}/{ARM}"
    cwd = f"{RUN_DIR}/{case}"
    return f"""#!/bin/zsh
# Launch the {ARM} arm for {case} in ONE fresh isolated headless session.
# Opus 5 + the openspecs-index knowledge-graph MCP server, and nothing else: no
# filesystem, no checkout, no network beyond the MCP endpoint, no subagents.
# Isolation is enforced by sandbox-exec (sandbox.sb): gold, the vault, every
# sibling arm's output AND all fourteen repository checkouts are unreadable.
set -e
ARM={arm}
CWD={cwd}
PROFILE={PROFILE}
CLAUDE={CLAUDE}
PROBE=$ARM/preflight.py

# --- gate 1: the index must be live and must cover the pinned roster ---
python3 $PROBE || {{ echo "PREFLIGHT FAILED - not launching"; exit 1; }}

# --- gate 2: never re-attempt a case in an arm that already ran ---
[ -f $ARM/raw_response.json ] && {{ echo "ALREADY RUN - refusing to re-attempt"; exit 1; }}

# --- gate 3: the sandbox must be blinding the gold ---
sandbox-exec -f $PROFILE /bin/cat {CROSS}/{case}/golden.json >/dev/null 2>&1 \\
  && {{ echo "SANDBOX LEAK: golden.json is readable"; exit 1; }}

# --- gate 4: and the checkouts too - this arm must not be able to read a repo ---
for R in {' '.join(r for r, _ in ROSTER)}; do
  sandbox-exec -f $PROFILE /bin/ls {BENCH_ROOT}/$R >/dev/null 2>&1 \\
    && {{ echo "SANDBOX LEAK: checkout $R is readable"; exit 1; }}
done

# openspecs-index tool surface, named explicitly (a bare prefix is not enough here)
T=({' '.join(TOOLS)})
ALLOW=$(printf "mcp__openspecs-index__%s," "${{T[@]}}"); ALLOW=${{ALLOW%,}}
DENY="Bash,Read,Write,Edit,Grep,Glob,WebFetch,WebSearch,Task,Agent,NotebookEdit,BashOutput,KillShell,TodoWrite"

mkdir -p $CWD
export DISABLE_PROMPT_CACHING=1
ST=$(date +%s)
( cd $CWD && sandbox-exec -f $PROFILE "$CLAUDE" -p "$(cat $ARM/run_prompt.txt)" \\
    --model claude-opus-5 \\
    --setting-sources "" --disable-slash-commands \\
    --mcp-config {MCP_CFG} --strict-mcp-config \\
    --allowedTools "$ALLOW" --disallowedTools "$DENY" \\
    --permission-mode bypassPermissions \\
    --max-budget-usd 30 \\
    --output-format json < /dev/null ) > $ARM/raw_response.json 2> $ARM/stderr.log
echo "wall_seconds=$(( $(date +%s) - ST ))" > $ARM/wall.txt
echo "arm finished: {case} $(cat $ARM/wall.txt)"
"""


def main(cases):
    os.makedirs(SBX_DIR, exist_ok=True)
    open(PROFILE, "w").write(profile())
    for case in cases:
        adir = f"{CROSS}/{case}/{ARM}"
        os.makedirs(adir, exist_ok=True)
        open(f"{adir}/run_prompt.txt", "w").write(run_prompt(case))
        open(f"{adir}/sandbox.sb", "w").write(profile())
        def cfg(url):
            return {"mcpServers": {"openspecs-index": {"type": "http",
                                                      "url": url}}}
        # repo copy: redacted, safe to commit. live copy: real token, outside the repo.
        json.dump(cfg(MCP_URL_REDACTED), open(f"{adir}/mcp_openspecs.json", "w"), indent=2)
        json.dump(cfg(MCP_URL), open(MCP_CFG, "w"), indent=2)
        rp = f"{adir}/run.sh"
        open(rp, "w").write(run_sh(case))
        os.chmod(rp, os.stat(rp).st_mode | stat.S_IXUSR | stat.S_IXGRP)
        print(f"{case:54} -> {ARM}/")
    print(f"\nshared profile: {PROFILE}")


if __name__ == "__main__":
    main(sys.argv[1:])
