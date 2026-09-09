#!/usr/bin/env python3
"""Write <case>/<arm>/run_prompt.txt for every cross-repo case and tool surface.

One master per case (<case>/prompt.txt) describes the surface generically; this swaps
that bullet for the real one, swaps the path-verification bullet for the variant the
surface can actually honour, and appends DELIVERABLES. Same substitution
build_mcp_arm.py performs, extended past graphify/serena and covering prompts only —
run.sh, sandbox.sb and the launch gates stay that script's job.

Surface text is lifted verbatim from arms that already ran, not rewritten, so a new
prompt cannot drift from the one a recorded result was produced under.

An arm holding any answer is skipped: its prompt is the record of what was asked.
"""
import os, sys, glob

CROSS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORCE = "--force" in sys.argv

OLD_SURFACE = """- Use the search surface this arm gives you, and nothing else. Whatever that
  surface is, every path you name must have been found through it: a run that
  reaches the answer by another route is a void measurement, not a faster one."""

OLD_VERIFY = """- Every path must exist in that repository AT THE PINNED COMMIT. These working
  trees are NOT clean: they carry untracked files that are not part of the commit
  and are not part of this task. Confirm every path against the commit itself
  (git cat-file -e <commit>:<path>) and drop anything that does not resolve."""

# For a surface with no shell: `git cat-file` is not available to it, and telling an
# arm to run a command it cannot run spends turns on a permission denial.
NO_SHELL_VERIFY = """- Every path must exist in that repository AT THE PINNED COMMIT. These working
  trees are NOT clean: they carry untracked files that are not part of the commit
  and are not part of this task. You have no Bash and cannot run `git cat-file`
  yourself - the harness has already gated every checkout to the pinned commit
  before launching you (see CHECKOUT below), so trust what the tool surface shows
  you and drop anything it does not confirm exists."""

INDEX_VERIFY = """- Every path must exist in that repository AT THE PINNED COMMIT. Your index was
  built from exactly those commits, so a path it returned is a path that exists;
  a path you assembled by hand, completed from memory, or guessed from a directory
  layout is not. Name only paths a tool call actually returned to you."""

DELIVERABLES = """

DELIVERABLES: the harness captures this run for you - it records the full transcript,
every tool call, and the cost, and it writes ranked.json from your final message. You
have no write tools; do not attempt to create files. Your only job is the ranked answer.
result.json is NOT yours to produce and nothing you emit lands in it: after the run the
scorer writes it from ranked.json against the held-out gold set, carrying recall,
precision, F1, MRR and hits alongside ranked_detail, found, missed_gold and retriever."""

BARE = """- Use your filesystem tools - Bash, Read, Grep, Glob - against the checkouts
  above. That is your whole surface: no code-index or knowledge-graph server, no
  network, no WebSearch or WebFetch, no subagents. Every path you name must have
  been found by reading these trees. A run that reaches the answer by another
  route is a void measurement, not a faster one."""

GRAPHIFY = """- Use the graphify knowledge-graph MCP tools - query_graph, get_node,
  get_neighbors, get_community, god_nodes, graph_stats, shortest_path - against a
  single merged graph spanning the repositories below. That is your whole
  surface: no Bash, Read, Grep, Glob, filesystem access, network, or subagents, and
  --strict-mcp-config keeps every other MCP server out. Every path you name must
  have been found by querying this graph. A run that reaches the answer by another
  route is a void measurement, not a faster one.
- The graph was built by AST extraction only (--code-only --no-cluster): the
  clustering-derived tools (get_community, god_nodes) reflect no clustering pass
  and may be sparse or unhelpful. Treat query_graph, get_neighbors and
  shortest_path as your primary tools. Every node carries a `repo` field and a
  `source_file` field - source_file is the exact repo-relative path to report,
  and repo tells you which object in your final answer it belongs to."""

SERENA = """- Use the serena MCP tools - find_symbol, find_referencing_symbols,
  find_declaration, find_implementations, get_symbols_overview,
  get_diagnostics_for_file, search_for_pattern, read_file, list_dir, find_file -
  against a live language server. That is your whole surface: no Bash, Read, Grep,
  Glob, filesystem access outside these tools, network, or subagents, and
  --strict-mcp-config keeps every other MCP server out. Every path you name must
  have been found through these tools. A run that reaches the answer by another
  route is a void measurement, not a faster one.
- Every repository in the roster is a pre-registered serena project, one per
  repository, named exactly as in the table below (e.g. "redux-thunk"). Only one
  project is active at a time: call activate_project with the repo name before you
  can query that repository's tools, and call it again to switch to the next
  repository. You must activate and search every repository in the roster in turn -
  do not stop after the first one or two that match."""

TURBOVEC = """- Use the turbovector MCP server and nothing else. Its tools - search, get_file,
  read_lines, index_info - are your whole surface: no filesystem tools, no Bash,
  Read, Grep or Glob, no other MCP server, no network, no WebSearch or WebFetch,
  no subagents. The index already spans every repository in the roster, so one
  search reaches all of them; you do not need to, and cannot, walk the checkouts.
  Every path you name must have come back from a turbovector call. A run that
  reaches the answer by another route is a void measurement, not a faster one."""

PLUMBLINE = """- You may ONLY use the plumbline MCP tools. You have no filesystem, shell, or search access.
- Only the repository directories listed below are readable. Anything else under
  the benchmark root is blocked at the kernel level and will fail with
  "Operation not permitted"; that is expected and is not a fault to work around.
  Do not spend turns probing outside them."""

# arm folder name -> (surface bullet, verification bullet)
ARMS = {
    "claudecli_opus5_bare":           (BARE,      OLD_VERIFY),      # has Bash: keep git cat-file
    "claudecli_opus5_mcp_graphify":   (GRAPHIFY,  NO_SHELL_VERIFY),
    "claudecli_opus5_mcp_serena":     (SERENA,    NO_SHELL_VERIFY),
    "claudecli_opus5_mcp_turbovec":   (TURBOVEC,  INDEX_VERIFY),
    "claudecli_opus5_mcp_plumbline":  (PLUMBLINE, NO_SHELL_VERIFY),
}

RUN_EVIDENCE = ("run_prompt.txt", "ranked.json", "result.json", "raw_response.json")

wrote, skipped = [], []
cases = sorted(d for d in glob.glob(f"{CROSS}/*/prompt.txt"))
for master_p in cases:
    case = os.path.dirname(master_p)
    master = open(master_p).read()
    assert OLD_SURFACE in master, f"{case}: no generic surface bullet"
    assert OLD_VERIFY in master, f"{case}: no verification bullet"
    for arm, (surface, verify) in ARMS.items():
        d = os.path.join(case, arm)
        if not FORCE and any(os.path.exists(os.path.join(d, f)) for f in RUN_EVIDENCE):
            skipped.append(f"{os.path.basename(case)}/{arm}")
            continue
        text = master.replace(OLD_SURFACE, surface).replace(OLD_VERIFY, verify) + DELIVERABLES
        assert OLD_SURFACE not in text and surface in text, f"{case}/{arm}: surface swap failed"
        assert verify in text, f"{case}/{arm}: verify swap failed"
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "run_prompt.txt"), "w").write(text)
        wrote.append(f"{os.path.basename(case)}/{arm}")

for w in wrote:
    print("wrote   ", w)
print(f"\n{len(wrote)} written, {len(skipped)} skipped (already hold a prompt or an answer)")
