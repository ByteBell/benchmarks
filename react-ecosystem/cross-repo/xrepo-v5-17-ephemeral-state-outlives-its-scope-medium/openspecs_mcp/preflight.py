#!/usr/bin/env python3
"""Gate 1 for the openspecs_mcp arm: the index must be LIVE and must actually
carry the pinned roster.

roll_call reports only each repository's NEWEST indexed commit, so a row naming a
different commit is not evidence the pinned one is missing. This probes the file
tier at the pinned commit for every roster repo and prints a coverage table.

Exit 0 = launch. Exit 1 = the server is unreachable or the graph carries NONE of
the roster; either makes the run void rather than merely handicapped. Repos that
are individually uncovered are reported and recorded, not fatal - the arm is told
in its prompt to declare them.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

ARM = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(f"{ARM}/mcp_openspecs.json"))
URL = CFG["mcpServers"]["openspecs-index"]["url"]

ROSTER = [
    ("redux",          "3aa561f9fc13f3287e6d83764f85fe0e82437c5f"),
    ("redux-toolkit",  "b1c5130154c454ca8387f55da6122fcb507c560c"),
    ("react-redux",    "ad5d1e0816d0cb1464b25cf2853f2a7d73433f5b"),
    ("reselect",       "8d87c27b75f55883629be75d1eae1c27832d907a"),
    ("redux-thunk",    "184205d49f707c6f203269e0d39ad85824801816"),
    ("react",          "3a717e42438afac81020cdec297dadb5613a4304"),
    ("jotai",          "5c4ca26b0db5571114be58393e17854a771f7790"),
    ("zustand",        "beca84e600e4e250f6b244d22878e72948f331c7"),
    ("db",             "7f0fa36ff6d48b0494ed5f6a1223fd18c317e41f"),
    ("xyflow",         "360f5b13e2bc6899ea06b4be1a49b068d86926cf"),
    ("query",          "46d7f02f1c7b9fcd3255082cc7103e8bfa3dab76"),
    ("table",          "d08af367e11c539bb7e18c2472aa761149ef6db6"),
    ("tldraw",         "5590d14d8edd4faab7dc1177b6e1adb28876fd23"),
    ("redux-devtools", "f4b4668c30ae08920c59a76cc4629c35c16ef0fa"),
]

# A deliberately generic probe: it must not encode anything about THIS case.
PROBE_QUERY = "module"


def post(body, sid=None):
    h = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    if sid:
        h["mcp-session-id"] = sid
    req = urllib.request.Request(URL, json.dumps(body).encode(), h)
    r = urllib.request.urlopen(req, timeout=300)
    return r.read().decode(), r.headers.get("mcp-session-id")


def parse(raw):
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return json.loads(raw) if raw.strip() else {}


def call(sid, name, args):
    raw, _ = post({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                   "params": {"name": name, "arguments": args}}, sid)
    d = parse(raw)
    if "error" in d:
        return "ERROR " + json.dumps(d["error"])
    return "".join(c.get("text", "") for c in d["result"]["content"])


def main():
    try:
        raw, sid = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18",
                                    "capabilities": {},
                                    "clientInfo": {"name": "preflight",
                                                   "version": "1"}}})
        post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    except (urllib.error.URLError, OSError) as e:
        print(f"MCP UNREACHABLE at {URL.split('?')[0]}: {e}")
        return 1

    rows = [json.loads(b) for b in
            re.findall(r'\{\s*"knowledgeId".*?\n\}', call(sid, "roll_call", {}), re.S)]
    by_name = {r["name"]: r for r in rows}

    cov, covered = {}, 0
    print(f"{'repo':<16}{'knowledgeId':<40}{'pinned commit indexed':<24}newest indexed")
    for repo, pin in ROSTER:
        r = by_name.get(repo)
        if not r:
            cov[repo] = {"in_graph": False, "pinned_commit_indexed": False,
                         "knowledgeId": None, "newest_indexed_commit": None}
            print(f"{repo:<16}{'-':<40}{'ABSENT FROM GRAPH':<24}-")
            continue
        out = call(sid, "stakeout", {"knowledgeId": r["knowledgeId"],
                                     "commitHash": pin, "query": PROBE_QUERY})
        m = re.search(r"stakeout — (\d+) file", out)
        hit = bool(m) and int(m.group(1)) > 0
        covered += hit
        cov[repo] = {"in_graph": True, "pinned_commit_indexed": hit,
                     "knowledgeId": r["knowledgeId"],
                     "newest_indexed_commit": r.get("lastIndexedCommit"),
                     "indexed_commit_count": r.get("indexedCommitCount"),
                     "last_indexed_at": r.get("lastIndexedAt")}
        print(f'{repo:<16}{r["knowledgeId"]:<40}'
              f'{("yes" if hit else "NO"):<24}{r.get("lastIndexedCommit")}')

    json.dump({"mcp_url": URL.split("?")[0], "probe_query": PROBE_QUERY,
               "repos_covered_at_pin": covered, "roster_size": len(ROSTER),
               "coverage": cov},
              open(f"{ARM}/index_coverage.json", "w"), indent=1)
    print(f"\ncoverage: {covered}/{len(ROSTER)} roster repos indexed at the pinned commit "
          f"-> {ARM}/index_coverage.json")
    if covered == 0:
        print("FATAL: the graph carries none of the roster at the pinned commits.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
