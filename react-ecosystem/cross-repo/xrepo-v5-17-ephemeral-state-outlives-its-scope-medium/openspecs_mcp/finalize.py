#!/usr/bin/env python3
"""Finalize the openspecs_mcp arm: ranked.json, cost.json, result.json.

GOLD-BLIND BY CONSTRUCTION. golden.json is read here and its paths are written to
result.json on disk, but NOTHING derived from gold is ever printed. stdout carries
aggregate metrics only, so running this cannot contaminate an agent's context for
this case. Do not add a print of hits/missed_gold/ranked_detail.
"""
import collections
import glob
import json
import os
import re
import subprocess
import sys

CASE = "xrepo-v5-17-ephemeral-state-outlives-its-scope"
BENCH = "/Users/sauravverma/programs/benchmarks/react-ecosystem"
CDIR = f"{BENCH}/cross-repo/{CASE}"
ARM_NAME = "openspecs_mcp"
ARM = f"{CDIR}/{ARM_NAME}"
# Matches the cap the prompt states (build_prompts.CAP, raised 40 -> 100 on
# 2026-09-07). The baseline attempt and the claude_code_opus5 arm both ran under
# the old 40 cap, so the prf block below also reports a 40-path cut: that cut is
# the apples-to-apples comparison, full_list is the current regime.
CAP = 100

# claude-opus-5[1m] list price, USD per Mtok (same table the sibling arms used)
PRICE = {"input": 5.0, "output": 25.0, "cache_write": 10.0, "cache_read": 0.5}

ROSTER = ["redux", "redux-toolkit", "react-redux", "reselect", "redux-thunk",
          "react", "jotai", "zustand", "db", "xyflow", "query", "table",
          "tldraw", "redux-devtools"]

MCP_PREFIX = "mcp__openspecs-index__"
# ToolSearch loads MCP tool SCHEMAS and has no read access to anything; it is a
# schema loader, not a search surface, so it does not break surface purity.
SCHEMA_LOADERS = {"ToolSearch"}


def extract(text):
    """Pull the [{repo, files}] answer out of the final message, tolerating fences."""
    if not text:
        return []
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M).strip()
    cands = [t] + [m.group(0) for m in re.finditer(r"\[.*\]", t, flags=re.S)]
    for c in cands:
        try:
            obj = json.loads(c)
        except Exception:
            continue
        if (isinstance(obj, list) and obj and all(
                isinstance(x, dict) and "repo" in x and "files" in x for x in obj)):
            return obj
    return []


def flatten(answer):
    """Ranked list of 'repo::path', in the order the model presented them."""
    out = []
    for blk in answer:
        r = blk.get("repo")
        for f in blk.get("files") or []:
            key = f"{r}::{f}"
            if key not in out:
                out.append(key)
    return out


def exists_at_pin(pins, key):
    """Scoring-side check only. The ARM had no filesystem; the harness does, and a
    path the graph returned still has to resolve in the real tree at the pin."""
    repo, path = key.split("::", 1)
    if repo not in pins:
        return False
    rc = subprocess.run(["git", "-C", f"{BENCH}/{repo}", "cat-file", "-e",
                         f"{pins[repo]}:{path}"], capture_output=True).returncode
    return rc == 0


def transcript(session_id):
    hits = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl"))
    return hits[0] if hits else None


def audit(session_id):
    """Tool histogram, bytes read back, and any reach for the filesystem."""
    p = transcript(session_id)
    if not p:
        return {}, 0, None, {}, 0
    calls = collections.Counter()
    chars = 0
    fs_reach = collections.Counter()
    denied = 0
    with open(p) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            for b in (d.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    calls[b["name"]] += 1
                    blob = json.dumps(b.get("input") or {})
                    # Any mention of the benchmark root in a tool INPUT is a reach
                    # for a tree this arm cannot see. The kernel refuses it; this
                    # only measures what was ATTEMPTED.
                    for m in re.finditer(re.escape(BENCH) + r"/([\w.\-]*)", blob):
                        fs_reach[m.group(1) or "<benchmark-root-listing>"] += 1
                elif b.get("type") == "tool_result":
                    c = b.get("content")
                    txt = c if isinstance(c, str) else json.dumps(c)
                    chars += len(txt)
                    denied += txt.count("Operation not permitted")
    return dict(calls), chars // 4, p, dict(fs_reach), denied


def coverage_audit(session_id, coverage, named_repos):
    """Which reachable roster repos actually received a scoped tool call.

    Reads the tool-call ARGUMENTS out of the transcript, not the model's own
    account of what it searched - the failure this arm was built to catch was a
    final message claiming four repositories "returned no file" when no call had
    ever named them. A self-reported ledger cannot detect that; this can.
    """
    p = transcript(session_id)
    if not p or not coverage:
        return {}, [], []
    kid_to_repo = {v["knowledgeId"]: r for r, v in coverage["coverage"].items()
                   if v.get("knowledgeId")}
    queried = set()
    with open(p) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            for b in (d.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                blob = json.dumps(b.get("input") or {})
                for kid, repo in kid_to_repo.items():
                    if kid in blob:
                        queried.add(repo)
    reachable = [r for r, v in coverage["coverage"].items()
                 if v.get("pinned_commit_indexed")]
    per_repo = {r: {"queried": r in queried, "in_answer": r in named_repos}
                for r in sorted(reachable)}
    unverified_exclusions = sorted(
        r for r in reachable if r not in queried and r not in named_repos)
    included_without_query = sorted(
        r for r in reachable if r in named_repos and r not in queried)
    return per_repo, unverified_exclusions, included_without_query


def prf(ranked, gset, k=None):
    cut = ranked if k is None else ranked[:k]
    if not cut or not gset:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "hits": 0,
                "retrieved": len(cut), "gold_size": len(gset)}
    hits = len(set(cut) & gset)
    p = hits / len(cut)
    r = hits / len(gset)
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "hits": hits, "retrieved": len(cut), "gold_size": len(gset)}


def main():
    env = json.load(open(f"{ARM}/raw_response.json"))
    if env.get("is_error") or env.get("subtype") not in (None, "success"):
        print(f"RUN ERRORED: subtype={env.get('subtype')} - not scoring")
        return 1

    g = json.load(open(f"{CDIR}/golden.json"))
    pins = {r: c for r, c in
            (l.split() for l in open(f"{ARM}/run_prompt.txt")
             if l.startswith("  ") and len(l.split()) == 2
             and len(l.split()[1]) == 40)}

    answer = extract(env.get("result", ""))
    ranked = flatten(answer)[:CAP]
    json.dump(answer, open(f"{ARM}/ranked.json", "w"), indent=1)

    sid = env.get("session_id")
    u = env.get("usage", {}) or {}
    tok = {"input": u.get("input_tokens", 0),
           "output": u.get("output_tokens", 0),
           "cache_creation": u.get("cache_creation_input_tokens", 0),
           "cache_read": u.get("cache_read_input_tokens", 0)}
    list_usd = round((tok["input"] * PRICE["input"] + tok["output"] * PRICE["output"]
                      + tok["cache_creation"] * PRICE["cache_write"]
                      + tok["cache_read"] * PRICE["cache_read"]) / 1e6, 6)
    tools, read_tokens, tpath, fs_reach, denied = audit(sid)
    wall = None
    if os.path.exists(f"{ARM}/wall.txt"):
        wall = int(open(f"{ARM}/wall.txt").read().strip().split("=")[1])

    mcp_calls = {k: v for k, v in tools.items() if k.startswith(MCP_PREFIX)}
    loaders = {k: v for k, v in tools.items() if k in SCHEMA_LOADERS}
    disallowed = {k: v for k, v in tools.items()
                  if not k.startswith(MCP_PREFIX) and k not in SCHEMA_LOADERS}

    coverage = None
    if os.path.exists(f"{ARM}/index_coverage.json"):
        coverage = json.load(open(f"{ARM}/index_coverage.json"))

    answered_repos = {k.split("::", 1)[0] for k in ranked}
    per_repo_cov, unverified_exclusions, included_without_query = coverage_audit(
        sid, coverage, answered_repos)

    cost = {
        "session_id": sid,
        "arm": ARM_NAME,
        "case": CASE,
        "model": env.get("modelUsage") and list(env["modelUsage"]) or "claude-opus-5",
        "launch": ("fresh isolated `claude -p` under sandbox-exec, "
                   "DISABLE_PROMPT_CACHING=1, --safe-mode, --strict-mcp-config "
                   "with openspecs-index only, MCP tools allowlisted and every "
                   "filesystem/network/subagent tool denied"),
        "price_per_mtok_usd": PRICE,
        "usd_per_query_cli": env.get("total_cost_usd"),
        "usd_per_query_list_price": list_usd,
        "duration_ms": env.get("duration_ms"),
        "wall_seconds": wall,
        "num_turns": env.get("num_turns"),
        "tokens": tok,
        "tokens_retrieved": read_tokens,
        "cache_clean": tok["cache_read"] == 0 and tok["cache_creation"] == 0,
        "tool_calls": tools,
        "mcp_tool_calls": mcp_calls,
        "schema_loader_calls": loaders,
        "disallowed_tool_calls": disallowed,
        "surface_pure": not disallowed,
        "surface_pure_note": (
            "Arm surface is Opus 5 with the openspecs-index MCP server and nothing "
            "else. The fourteen checkouts are denied by the sandbox, so a "
            "filesystem tool would have returned EPERM even had one been allowed; "
            "the tool allowlist means none was. ToolSearch is excluded from the "
            "purity test: it loads MCP tool SCHEMAS and has no read access to "
            "anything. Any other non-MCP tool counts as a surface violation."),
        "index_coverage": coverage,
        "coverage_audit": {
            "per_repo": per_repo_cov,
            "unverified_exclusions": unverified_exclusions,
            "included_without_query": included_without_query,
            "note": ("Derived from tool-call ARGUMENTS in the transcript, not from "
                     "the model's own account of what it searched. "
                     "unverified_exclusions are reachable roster repos that got no "
                     "scoped call AND are absent from the answer - the exact bug "
                     "this arm's first run had (react and zustand, both gold, were "
                     "never queried while the final message claimed they returned "
                     "nothing). It must be empty. included_without_query is the "
                     "inverse check: a repo named in the answer that was never "
                     "queried would mean paths invented rather than retrieved."),
        },
        "sandbox": {
            "mechanism": "sandbox-exec (macOS seatbelt), profile pinned at sandbox.sb",
            "gold_readable": False,
            "checkouts_readable": False,
            "other_arms_readable": False,
            "transcripts_readable": False,
            "filesystem_paths_attempted": fs_reach,
            "reads_refused_by_kernel": denied,
            "contamination_possible": False,
            "note": ("The benchmark tree is denied wholesale with NO re-allow, so "
                     "golden.json, DONOTREADTHISFOLDER/, every sibling arm's "
                     "results AND all fourteen repository checkouts fail with "
                     "EPERM. run.sh gates on that: it refuses to launch unless a "
                     "sandboxed cat of golden.json and a sandboxed ls of each of "
                     "the fourteen repos both fail. ~/.claude/projects is denied "
                     "too, so no earlier session transcript - nor this run's own - "
                     "is readable. cwd was a fresh empty directory outside the "
                     "benchmark tree and --safe-mode dropped CLAUDE.md, skills, "
                     "plugins, hooks and auto-memory. Network is open because the "
                     "MCP server is the surface and speaks HTTP; purity is "
                     "enforced by the tool allowlist and audited here."),
        },
        "transcript": tpath,
        "prompt_caching": "DISABLED (DISABLE_PROMPT_CACHING=1) - cold-cache figure",
    }
    json.dump(cost, open(f"{ARM}/cost.json", "w"), indent=1)

    # ---- scoring (gold read here; never printed) ----
    gold = [f"{e['repo']}::{f}" for e in g["expected"] for f in e["files"]]
    gset = set(gold)
    gold_repos = {e["repo"] for e in g["expected"]}
    named_repos = answered_repos
    hits = [k for k in ranked if k in gset]
    invalid = [k for k in ranked if not exists_at_pin(pins, k)]
    detail = [{"rank": i + 1, "repo": k.split("::", 1)[0],
               "path": k.split("::", 1)[1], "gold": k in gset,
               "exists_at_pin": k not in invalid} for i, k in enumerate(ranked)]
    first = next((i + 1 for i, k in enumerate(ranked) if k in gset), None)

    def rec(k):
        return round(len(set(ranked[:k]) & gset) / len(gset), 4) if gset else None

    metrics = {
        "recall@10": rec(10), "recall@20": rec(20), "recall@40": rec(40),
        "precision@5": round(len(set(ranked[:5]) & gset) / min(5, len(ranked)), 4) if ranked else 0.0,
        "precision@20": round(len(set(ranked[:20]) & gset) / min(20, len(ranked)), 4) if ranked else 0.0,
        "MRR": round(1 / first, 4) if first else 0.0,
        "first_hit_rank": first,
        "hits_at_20": f"{len(set(ranked[:20]) & gset)}/{len(gset)}",
        "repo_recall": round(len(gold_repos & named_repos) / len(gold_repos), 4),
        "repos_found": f"{len(gold_repos & named_repos)}/{len(gold_repos)}",
        "repos_found_at_40": f"{len(gold_repos & repos_at_40)}/{len(gold_repos)}",
        "repos_named": len(named_repos),
        "invalid_path_rate": round(len(invalid) / len(ranked), 4) if ranked else 0.0,
    }
    prf_block = {"full_list": prf(ranked, gset), "@5": prf(ranked, gset, 5),
                 "@10": prf(ranked, gset, 10), "@20": prf(ranked, gset, 20),
                 "@40": prf(ranked, gset, 40)}
    # Repo-level coverage at the old 40-path cut, so repos_found can be compared
    # against runs that were themselves capped at 40.
    repos_at_40 = {k.split("::", 1)[0] for k in ranked[:40]}

    # Gold repos the index could not serve at the pinned commit: a ceiling on
    # recall that belongs to the index, not to the model.
    unreachable = []
    if coverage:
        unreachable = sorted(r for r in gold_repos
                             if not (coverage["coverage"].get(r) or {})
                             .get("pinned_commit_indexed"))
    gold_out_of_index = [k for k in gold if k.split("::", 1)[0] in set(unreachable)]

    result = {
        "arm": ARM_NAME,
        "case_id": g["id"],
        "case_question": g["question"],
        "retrieved": len(ranked),
        "gold_counts": g.get("counts"),
        "gold_size": len(gset),
        "gold_repo_count": len(gold_repos),
        "metrics": metrics,
        "prf": prf_block,
        "hits": hits,
        "ranked_detail": detail,
        "invalid_paths": invalid,
        "missed_gold": [k for k in gold if k not in set(ranked)],
        "index_ceiling": {
            "gold_repos_not_indexed_at_pin": unreachable,
            "gold_paths_out_of_index": gold_out_of_index,
            "reachable_gold_size": len(gset) - len(gold_out_of_index),
            "max_attainable_recall": round(
                (len(gset) - len(gold_out_of_index)) / len(gset), 4) if gset else None,
            "note": ("Gold living in a repository the graph does not carry at the "
                     "pinned commit is unreachable BY THIS ARM'S SURFACE. It is "
                     "still counted in recall against the full gold set - the "
                     "headline metrics are unadjusted - but max_attainable_recall "
                     "states the ceiling the index imposed."),
        },
        "retriever": {
            "surface": ("Opus 5 + openspecs-index knowledge-graph MCP. No "
                        "filesystem, no checkout, no WebSearch/WebFetch, no "
                        "subagents."),
            "repo_pins": pins,
            "checkout_verified": ("N/A - this arm has no checkout. run.sh gates "
                                  "instead on preflight.py, which probes the file "
                                  "tier at every pinned commit and writes "
                                  "index_coverage.json before launch."),
            "isolation": ("kernel-enforced by sandbox-exec: gold, the answer vault, "
                          "every sibling arm AND all fourteen checkouts are "
                          "unreadable; no prior-run transcript is readable; fresh "
                          "cwd outside the benchmark tree; --safe-mode"),
            "surface_pure": cost["surface_pure"],
        },
        "cost": cost,
        "comparable": bool(cost["cache_clean"] and cost["surface_pure"]),
    }
    if not result["comparable"]:
        result["not_comparable_reason"] = "; ".join(filter(None, [
            "" if cost["cache_clean"] else "cache not cold",
            f"disallowed tools used: {disallowed}" if disallowed else ""]))
    json.dump(result, open(f"{ARM}/result.json", "w"), indent=1)

    print(json.dumps({
        "case": CASE, "arm": ARM_NAME, "retrieved": len(ranked),
        "gold_size": len(gset), "metrics": metrics, "prf": prf_block,
        "usd_cli": cost["usd_per_query_cli"], "usd_list": list_usd,
        "wall_s": wall, "turns": cost["num_turns"], "tokens": tok,
        "tokens_read": read_tokens, "cache_clean": cost["cache_clean"],
        "surface_pure": cost["surface_pure"], "tool_calls": tools,
        "filesystem_paths_attempted": fs_reach,
        "reads_refused_by_kernel": denied,
        "index_repos_covered_at_pin": (coverage or {}).get("repos_covered_at_pin"),
        "repos_queried": sorted(r for r, v in per_repo_cov.items() if v["queried"]),
        "repos_never_queried": sorted(
            r for r, v in per_repo_cov.items() if not v["queried"]),
        "unverified_exclusions": unverified_exclusions,
        "included_without_query": included_without_query,
        "max_attainable_recall": result["index_ceiling"]["max_attainable_recall"],
        "comparable": result["comparable"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
