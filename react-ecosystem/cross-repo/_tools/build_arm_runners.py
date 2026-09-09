#!/usr/bin/env python3
"""Write run.sh + sandbox.sb for every arm of every cross-repo case.

    build_arm_runners.py [--dry-run]

WHY THIS EXISTS
---------------
The cases were renamed (xrepo-v6-2-partial-key-lets-siblings-collide-hard ->
hard-partial-key-lets-siblings-collide) and every run.sh kept the old absolute path:
all 33 pointed at directories that no longer exist, so no arm could be launched.

Worse, gate 4 proves the sandbox is blinding gold by trying to `cat` the case's
golden.json and requiring failure. With a stale path that cat fails because the FILE
is missing, so the gate passes for the wrong reason — a sandbox that was not blinding
gold would sail through it. The generated gate cats the CURRENT golden.json and
additionally asserts the file exists first, so "missing" can never read as "blinded".

Never touches run_prompt.txt or any result: prompts and answers are records of what
was asked and returned. Existing run.sh/sandbox.sb are kept as *.stale.bak.
"""
import json, os, re, stat, sys

CROSS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.dirname(CROSS)
CLAUDE = "/Users/sauravverma/.nvm/versions/node/v24.9.0/bin/claude"
DRY = "--dry-run" in sys.argv

# Canonical roster, taken from build_prompts.py so the sandbox can never re-open a
# different set of repos than the prompt names. router is included: the masters list
# 15 and an arm told about a repo it cannot read would fail for the wrong reason.
sys.path.insert(0, os.path.join(CROSS, "_tools"))
ROSTER = [
    ("redux", "3aa561f9fc13f3287e6d83764f85fe0e82437c5f"),
    ("redux-toolkit", "b1c5130154c454ca8387f55da6122fcb507c560c"),
    ("react-redux", "ad5d1e0816d0cb1464b25cf2853f2a7d73433f5b"),
    ("reselect", "8d87c27b75f55883629be75d1eae1c27832d907a"),
    ("redux-thunk", "184205d49f707c6f203269e0d39ad85824801816"),
    ("react", "3a717e42438afac81020cdec297dadb5613a4304"),
    ("jotai", "5c4ca26b0db5571114be58393e17854a771f7790"),
    ("zustand", "beca84e600e4e250f6b244d22878e72948f331c7"),
    ("db", "7f0fa36ff6d48b0494ed5f6a1223fd18c317e41f"),
    ("xyflow", "360f5b13e2bc6899ea06b4be1a49b068d86926cf"),
    ("query", "46d7f02f1c7b9fcd3255082cc7103e8bfa3dab76"),
    ("table", "d08af367e11c539bb7e18c2472aa761149ef6db6"),
    ("tldraw", "5590d14d8edd4faab7dc1177b6e1adb28876fd23"),
    ("redux-devtools", "f4b4668c30ae08920c59a76cc4629c35c16ef0fa"),
    ("router", "3dee5b2e9453d01a3172c73426959007b290b3fc"),
]

IDX = f"{CROSS}/_indexes"

# surface -> (mcp config, allowed tools, extra sandbox READ paths, extra WRITE paths,
#             gate-2 files that must exist)
SURFACES = {
    "bare": dict(
        mcp=None,
        allow=["Bash", "Read", "Grep", "Glob"],
        reads=[], writes=[], needs=[],
        # bare is the only surface that may read the checkouts THROUGH tools.
        note="Opus 5 with filesystem tools over the pinned checkouts and nothing else."),
    "graphify": dict(
        mcp=f"{IDX}/graphify_mcp/mcp_graphify.json",
        allow=[f"mcp__graphify__{t}" for t in
               ("query_graph", "get_node", "get_neighbors", "get_community",
                "god_nodes", "graph_stats", "shortest_path")],
        reads=[f"{IDX}/graphify/merged", f"{IDX}/graphify_mcp"], writes=[],
        needs=[f"{IDX}/graphify/merged/graphify-out/graph.json",
               f"{IDX}/graphify_mcp/mcp_graphify.json"],
        note="the shared merged graphify graph is the only search surface."),
    "serena": dict(
        mcp=f"{IDX}/serena_mcp/mcp_serena.json",
        allow=[f"mcp__serena__{t}" for t in
               ("activate_project", "find_symbol", "find_referencing_symbols",
                "find_declaration", "find_implementations", "get_symbols_overview",
                "get_diagnostics_for_file", "search_for_pattern", "read_file",
                "list_dir", "find_file")],
        reads=[f"{IDX}/serena_mcp"],
        # The LSP writes its per-project cache into <repo>/.serena, so those paths must
        # be re-opened for write or every activate_project fails.
        writes=[f"{BENCH}/{r}/.serena" for r, _ in ROSTER],
        needs=[f"{IDX}/serena_mcp/mcp_serena.json"],
        note="a live language server over pre-registered serena projects."),
    "turbovec": dict(
        mcp=f"{IDX}/turbovec_mcp/mcp_turbovector.json",
        allow=[f"mcp__turbovector__{t}" for t in
               ("search", "get_file", "read_lines", "index_info")],
        reads=[f"{IDX}/turbovec", f"{IDX}/turbovec_mcp"], writes=[],
        needs=[f"{IDX}/turbovec/index/react-ecosystem.tvim",
               f"{IDX}/turbovec/index/chunks.jsonl",
               f"{IDX}/turbovec_mcp/mcp_turbovector.json"],
        note="the shared cross-repo turbovec vector index is the only search surface."),
    "plumbline": dict(
        mcp=f"{BENCH}/mcp_plumbline.json",
        allow=[f"mcp__plumbline__{t}" for t in
               ("roll_call", "blueprint", "stakeout", "manhunt", "dragnet",
                "cross_repo_lookup", "case_file", "case_notes", "cold_case",
                "collateral_damage", "evidence_locker", "file_a_complaint",
                "interrogation", "kingpin", "mugshot", "paper_trail",
                "pull_the_evidence", "read_the_fine_print", "shakedown",
                "the_receipts")],
        # plumbline is a remote HTTP server: it reads no local index, and the arm is
        # meant to be blind to the checkouts entirely. Re-open nothing.
        reads=[], writes=[], needs=[f"{BENCH}/mcp_plumbline.json"],
        blind_repos=True,
        note="a remote knowledge-graph MCP; the checkouts are NOT readable at all."),
}

DENY = ["Bash", "Read", "Write", "Edit", "Grep", "Glob", "WebFetch", "WebSearch",
        "Task", "NotebookEdit", "TodoWrite", "ReportFindings"]


def surface_of(arm):
    for s in ("graphify", "serena", "turbovec", "plumbline"):
        if s in arm:
            return s
    return "bare" if "bare" in arm else None


def sandbox(surface, cfg):
    repos = [] if cfg.get("blind_repos") else [f"{BENCH}/{r}" for r, _ in ROSTER]
    reads = repos + cfg["reads"]
    L = ["(version 1)", "(allow default)", "",
         ";; ---- benchmark tree: blind everything, then re-open only what this arm needs ----",
         ";; Later rules win, so this denies golden.json, every other arm's ranked.json /",
         ";; result.json, and the case-construction scripts.",
         f'(deny file-read* (subpath "{BENCH}"))']
    if reads:
        L.append("(allow file-read*")
        L += [f'  (subpath "{p}")' for p in reads[:-1]]
        L.append(f'  (subpath "{reads[-1]}"))')
    else:
        L.append(";; nothing is re-opened: this arm reaches code only through its MCP server.")
    L.append(f'(deny file-write* (subpath "{BENCH}"))')
    if cfg["writes"]:
        L += ["", ";; the language server writes its per-project cache under <repo>/.serena",
              "(allow file-write*"]
        L += [f'  (subpath "{p}")' for p in cfg["writes"][:-1]]
        L.append(f'  (subpath "{cfg["writes"][-1]}"))')
    L += ["", ";; ---- no session transcript is readable: not an earlier run's, not this one's ----",
          '(deny file-read* (subpath "/Users/sauravverma/.claude/projects"))',
          '(deny file-read* (subpath "/Users/sauravverma/.claude/history.jsonl"))', ""]
    return "\n".join(L)


def runsh(case, arm, surface, cfg):
    armdir = f"{CROSS}/{case}/{arm}"
    gold = f"{CROSS}/{case}/golden.json"
    cwd = f"/tmp/xrepo-runs/{case}-{surface}"
    drift = "\n".join(
        f'[ "$(git -C {BENCH}/{r} rev-parse HEAD)" = "{c}" ] || {{ echo "CHECKOUT DRIFT: {r}"; exit 1; }}'
        for r, c in ROSTER)
    needs = "\n".join(f'[ -f "{p}" ] || {{ echo "MISSING: {p}"; exit 1; }}' for p in cfg["needs"]) \
        or "# (this surface needs no local index)"
    mcpargs = (f'--mcp-config "{cfg["mcp"]}" --strict-mcp-config' if cfg["mcp"]
               else "--strict-mcp-config")
    allow = " ".join(f'"{t}"' for t in cfg["allow"])
    deny = " ".join(DENY) if surface != "bare" else "WebFetch WebSearch Task NotebookEdit"
    return f"""#!/bin/zsh
# {arm} — {case}
# {cfg['note']}
# ONE fresh isolated headless session. Isolation is kernel-enforced by sandbox-exec
# (sandbox.sb), not by convention: gold and every other arm's output are unreadable
# from inside.
#
# NOTE: --safe-mode must NOT be used; it disables MCP servers along with every other
# customization, which silently leaves the model with no tools.
set -e
ARM={armdir}
CWD={cwd}
PROFILE=$ARM/sandbox.sb
CLAUDE={CLAUDE}

# --- gate 1: every checkout on its pinned commit ---
{drift}

# --- gate 2: this surface's index must be present ---
{needs}

# --- gate 3: never re-attempt an arm that already ran ---
[ -f $ARM/raw_response.json ] && {{ echo "ALREADY RUN - refusing to re-attempt"; exit 1; }}

# --- gate 4: the sandbox must actually be blinding the gold ---
# The file must EXIST before its unreadability means anything. A stale path made this
# check pass because `cat` failed on a missing file, not on a denied one.
[ -f {gold} ] || {{ echo "NO golden.json at {gold} - gate 4 cannot prove anything"; exit 1; }}
sandbox-exec -f $PROFILE /bin/cat {gold} >/dev/null 2>&1 \\
  && {{ echo "SANDBOX LEAK: golden.json is readable"; exit 1; }}

mkdir -p $CWD
ST=$(date +%s)
( cd $CWD && sandbox-exec -f $PROFILE "$CLAUDE" -p "$(cat $ARM/run_prompt.txt)" \\
    --model claude-opus-5 \\
    --setting-sources "" --disable-slash-commands \\
    {mcpargs} \\
    --allowedTools {allow} \\
    --disallowedTools {deny} \\
    --max-budget-usd 40 \\
    --output-format json < /dev/null ) > $ARM/raw_response.json 2> $ARM/stderr.log
echo "wall_seconds=$(( $(date +%s) - ST ))" > $ARM/wall.txt
echo "arm finished: {case} {surface} $(cat $ARM/wall.txt)"
echo
echo "next: python3 {BENCH}/score_arm.py cross-repo/{case} {arm}"
"""


def main():
    wrote = skipped = backed = 0
    for case in sorted(os.listdir(CROSS)):
        cdir = os.path.join(CROSS, case)
        if not os.path.isdir(cdir) or not os.path.exists(f"{cdir}/prompt.txt"):
            continue
        for arm in sorted(os.listdir(cdir)):
            adir = os.path.join(cdir, arm)
            if not os.path.isdir(adir) or not os.path.exists(f"{adir}/run_prompt.txt"):
                continue
            s = surface_of(arm)
            if not s:
                skipped += 1
                print(f"  SKIP  {case}/{arm} (unknown surface)")
                continue
            cfg = SURFACES[s]
            for name, body in (("run.sh", runsh(case, arm, s, cfg)),
                               ("sandbox.sb", sandbox(s, cfg))):
                p = os.path.join(adir, name)
                if DRY:
                    continue
                if os.path.exists(p):
                    os.replace(p, p + ".stale.bak")
                    backed += 1
                open(p, "w").write(body)
                if name == "run.sh":
                    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            wrote += 1
    print(f"\n{wrote} arms written, {backed} existing files backed up as *.stale.bak, "
          f"{skipped} skipped")


if __name__ == "__main__":
    main()
