#!/usr/bin/env python3
"""Score one retrieval arm against gold. Usage: score_arm.py <case_dir> <arm_name>

Tolerates both gold schemas: the split-bearing one (gold_core/gold_propagation)
and the verified one that carries `gold` only. Split metrics are emitted as null
when the gold file does not define a split.
"""
import json, sys, os, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_config import RANKED_LIST_CAP, SCORED_KS

# An arm's own result.json self-report is not evidence about what it ran: the
# 2026-09-09 plumbline run on f66fffd1 reported `stakeout: 2, shakedown: 3` when
# commands.log showed `stakeout: 0, shakedown: 5`. Count the tools from the log.
POST_RUN_MARKER = 'post-run only'
TOOL_LINE = re.compile(r'^\s*\d+\.\s+(\S+)')

# Tools an arm must actually call for its retriever to have been under test at all.
# plumbline's enrichment and symbol-lead paths live ONLY in stakeout; an arm that
# reaches for shakedown (regex over the corpus) and never calls stakeout has run a
# grep, and scores like the bare arm for that reason and not for any graph reason.
REQUIRED_TOOLS = {'plumbline': ['mcp__plumbline__stakeout']}


def tool_counts(arm_dir):
    """Tool-name -> call count from commands.log, retrieval surface only.

    Returns None when there is no log, which is itself disqualifying: a run whose
    tool calls were never recorded cannot be told apart from one that made none.
    """
    path = os.path.join(arm_dir, 'commands.log')
    if not os.path.exists(path):
        return None
    counts = {}
    for line in open(path):
        if POST_RUN_MARKER in line:
            break  # scoring/bookkeeping calls are not part of the surface
        m = TOOL_LINE.match(line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return counts


def surface_check(arm, arm_dir):
    """Did this arm exercise the retriever it is named for? Reasons, or []."""
    required = next((t for k, t in REQUIRED_TOOLS.items() if k in arm), None)
    if not required:
        return {'tool_calls': tool_counts(arm_dir), 'not_comparable': []}
    counts = tool_counts(arm_dir)
    if counts is None:
        return {'tool_calls': None,
                'not_comparable': ['no commands.log — tool surface unverifiable']}
    missing = [t for t in required if not counts.get(t)]
    return {
        'tool_calls': counts,
        'not_comparable': [
            f'{t} never called — the {arm.split("_")[-1]} retriever was not exercised'
            for t in missing],
    }

# The fold probe.
# ---------------
# A gold file that shares no vocabulary with the query is unreachable by search at
# any ranking quality — the only way in is an edge out of a file the run already
# ranked. So the question that separates "folded and missed" from "never folded" is
# whether `collateral_damage` was ever seeded on the arm's OWN top hits.
#
# commands.log records CALLS, not results, so this measures the attempt and not its
# yield. That is still the discriminating bit: a run that never seeded its rank-1
# hit cannot have reached anything one hop past it, whatever the graph holds.
CD_TOOL_SUFFIX = 'collateral_damage'
ARG_LINE = re.compile(r'^\s*\d+\.\s+(\S+)\s+(\{.*\})\s*$')
FOLD_TOP_N = 5
# `lens` omitted means the tool's default, which is all seven — not none. Recording
# it as the full set stops a broad fold being scored as a narrow one.
ALL_LENSES = ('imports', 'dependencies', 'contracts', 'packages', 'surfaces',
              'types', 'keywords')


def tool_calls_with_args(arm_dir):
    """[(tool, args)] from commands.log, retrieval surface only. None when no log."""
    path = os.path.join(arm_dir, 'commands.log')
    if not os.path.exists(path):
        return None
    calls = []
    for line in open(path):
        if POST_RUN_MARKER in line:
            break
        m = ARG_LINE.match(line)
        if not m:
            continue
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            args = {}  # a malformed arg blob still counts as a call to that tool
        calls.append((m.group(1), args))
    return calls


def fold_probe(arm, arm_dir, ranked):
    """Did the run walk edges out of the files it itself ranked highest?

    Paths are compared by suffix: a cross-repo answer is repo-qualified while a
    `collateral_damage` seed is always repo-relative.
    """
    if 'plumbline' not in arm:
        return None  # the arm has no such tool; absence here is not a failure
    calls = tool_calls_with_args(arm_dir)
    if calls is None:
        return {'note': 'no commands.log — fold unverifiable'}

    seeds, keyword_seeds, lenses = [], [], set()
    for tool, args in calls:
        if not tool.endswith(CD_TOOL_SUFFIX):
            continue
        if args.get('relativePath'):
            seeds.append(args['relativePath'])
        if args.get('keyword'):
            keyword_seeds.append(args['keyword'])
        lens = args.get('lens')
        lenses.update(ALL_LENSES if lens is None
                      else (lens if isinstance(lens, list) else [lens]))

    def same(a, b):
        return a == b or a.endswith('/' + b) or b.endswith('/' + a)

    def rank_of(path):
        return next((i for i, p in enumerate(ranked, 1) if same(p, path)), None)

    top = ranked[:FOLD_TOP_N]
    seeded = [p for p in top if any(same(p, s) for s in seeds)]
    return {
        'cd_calls': len(seeds) + len(keyword_seeds),
        'cd_seeds': seeds,
        'cd_keyword_seeds': keyword_seeds,
        # Where each seed landed in the arm's own answer. `null` means the run
        # folded a file it did not consider good enough to report.
        'seed_ranks': {s: rank_of(s) for s in seeds},
        'lenses_used': sorted(lenses),
        'used_dependencies_lens': 'dependencies' in lenses,
        f'top{FOLD_TOP_N}_seeded': len(seeded),
        f'top{FOLD_TOP_N}_unseeded': [p for p in top if p not in seeded],
    }


def load_gold(case_dir):
    """The two case families name and shape their gold differently.

    Single-repo (cal.com): `gold.json`, with `gold` as a flat path list.
    Cross-repo:            `golden.json`, with `expected` as [{repo, files}] — paths
                           are repo-relative, so they are only unique once qualified
                           by repo. Both normalize to a set of comparable strings.
    """
    for name in ('gold.json', 'golden.json'):
        p = os.path.join(case_dir, name)
        if os.path.exists(p):
            g = json.load(open(p))
            if g.get('gold') is not None:
                return g, name, set(g['gold']), False
            if g.get('expected') is not None:
                paths = {f"{r['repo']}/{f}" for r in g['expected'] for f in r.get('files', [])}
                return g, name, paths, True
            raise SystemExit(f'{name} has neither `gold` nor `expected`')
    raise SystemExit(f'no gold.json or golden.json in {case_dir}')


def flatten_ranked(raw, cross_repo):
    """Cross-repo answers arrive as [{repo, files}]; single-repo as a flat array.

    Qualifying with the repo is what makes a cross-repo path comparable to gold —
    two repos can hold the same relative path, and an unqualified match would score
    a file the arm never actually named.
    """
    if cross_repo and raw and isinstance(raw[0], dict):
        return [f"{e['repo']}/{f}" for e in raw for f in e.get('files', [])]
    return raw


def main(case_dir, arm):
    gold, gold_file, G, cross_repo = load_gold(case_dir)
    raw = flatten_ranked(json.load(open(os.path.join(case_dir, arm, 'ranked.json'))),
                         cross_repo)
    # Truncate to the shared cap before scoring — see bench_config.
    ranked = raw[:RANKED_LIST_CAP]
    C = set(gold.get('gold_core') or [])
    P = set(gold.get('gold_propagation') or [])
    def rec(sub, k):
        return round(len(set(ranked[:k]) & sub) / len(sub), 3) if sub else None
    hits = [i for i, p in enumerate(ranked, 1) if p in G]
    split = lambda p: ('core' if p in C else 'propagation') if (C or P) else None
    arm_dir = os.path.join(case_dir, arm)
    surface = surface_check(arm, arm_dir)
    fold = fold_probe(arm, arm_dir, ranked)
    return {
        'arm': arm, 'case_id': gold['id'], 'index_at': gold.get('index_at'),
        'gold_file': gold_file, 'case_family': 'cross-repo' if cross_repo else 'single-repo',
        # Read this BEFORE the metrics. A run listed here produced numbers, but
        # they are not numbers about the retriever the arm name claims.
        'surface': surface, 'comparable': not surface['not_comparable'],
        # Did the run walk out of its own top hits? A gold file sharing no
        # vocabulary with the query is reachable ONLY that way, so a low
        # `top5_seeded` separates "folded and missed" from "never folded".
        'fold': fold,
        'retrieved': len(ranked), 'returned_before_cap': len(raw),
        'list_cap': RANKED_LIST_CAP, 'gold_counts': gold.get('counts'),
        'gold_schema': 'split' if (C or P) else 'verified-no-split',
        'metrics': {
            **{f'recall@{k}': rec(G, k) for k in SCORED_KS},
            'precision@5': round(len(set(ranked[:5]) & G) / 5, 3),
            'MRR': round(1 / hits[0], 3) if hits else 0.0,
            'core_recall@10': rec(C, 10), 'propagation_recall@20': rec(P, 20),
        },
        # A k above the arm's own list length is full-list recall relabelled: it
        # measures how many paths the arm chose to emit, not what it could reach.
        'degenerate_ks': [k for k in SCORED_KS if len(ranked) < k],
        'hits': len(hits),
        'ranked_detail': [
            {'rank': i, 'path': p, 'verdict': 'hit' if p in G else 'miss',
             'split': split(p) if p in G else None}
            for i, p in enumerate(ranked, 1)],
        'missed_gold': sorted(G - set(ranked)),
    }

if __name__ == '__main__':
    print(json.dumps(main(sys.argv[1], sys.argv[2]), indent=1))
