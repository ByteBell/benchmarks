#!/usr/bin/env python3
"""Write <case>/claudecli_opus5_mcp_gitnexus/run_prompt.txt for one cal.com case.

Built from the turbovec arm prompt of an already-run case, with the commit, checkout
root, case question and surface bullet swapped. Taking a template that a scored run
actually used keeps a new arm's prompt from drifting from the rest of the series.
"""
import json, os, re, sys

ROOT = '~/programs/benchmarks/react-ecosystem'
SRC_SHA = 'f66fffd13b0bb1828248bc89c687e23a7481a40a'
SRC = f'{ROOT}/cal.com.processed/{SRC_SHA}/claudecli_opus5_mcp_turbovec/run_prompt.txt'

SURFACE = """- You may ONLY use the gitnexus MCP tools. You have no filesystem, shell, or grep
  access. The repository is indexed as a code knowledge graph: `query` searches it for
  execution flows related to a concept, `context` gives a 360-degree view of one symbol
  (its callers, callees and the processes it takes part in), `impact` gives the blast
  radius of changing a symbol, `trace` finds the shortest path between two symbols, and
  `cypher` runs a raw query against the graph. `route_map`, `tool_map`, `shape_check`
  and `api_impact` cover HTTP routes and their consumers. `list_repos` tells you what is
  indexed. Every path you name must have come back from one of these calls."""

OLD = re.compile(r'- You may ONLY use the turbovector MCP tools.*?(?=\n- Do not stop at)', re.S)


def main(sha):
    case = f'{ROOT}/cal.com.processed/{sha}'
    q = json.load(open(f'{case}/case.json'))['query']
    t = open(SRC).read().replace(SRC_SHA, sha)
    t = t.replace(f'cal.com/{sha}/repo', f'cal.com.processed/{sha}/repo')
    t = OLD.sub(SURFACE + "\n", t)
    t = re.sub(r'(QUERY \(a symptom-level bug report\):\n).*?(\n\nTASK:)',
               lambda m: m.group(1) + q + m.group(2), t, flags=re.S)
    assert 'turbovector' not in t, 'turbovec surface survived'
    assert 'gitnexus MCP tools' in t, 'gitnexus surface missing'
    assert SRC_SHA not in t and sha in t, 'commit swap failed'
    assert q.split('.')[0] in t, 'query swap failed'
    co = f'cal.com.processed/{sha}/repo'
    assert co in t and os.path.isdir(f'{ROOT}/{co}'), 'checkout line wrong'
    d = f'{case}/claudecli_opus5_mcp_gitnexus'
    os.makedirs(d, exist_ok=True)
    open(f'{d}/run_prompt.txt', 'w').write(t)
    print(f'wrote {sha[:8]} run_prompt.txt')


if __name__ == '__main__':
    main(sys.argv[1])
