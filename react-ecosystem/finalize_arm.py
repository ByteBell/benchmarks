#!/usr/bin/env python3
"""Post-process one arm's raw_response.json into ranked.json / agent_answer.json /
commands.log / cost.json.

Split out of run_plumbline_arm.sh so a run whose QUERY succeeded but whose
post-processing failed can be finalized from the saved response instead of being
re-bought: on 2026-09-09 an f66fffd1 arm cost $5.09, answered correctly, and was
lost to a regex bug here.

    ./finalize_arm.py <case_dir> <arm_name>
"""
import json, sys, re, os, collections

case, arm = sys.argv[1], sys.argv[2]
d = json.load(open(f'{case}/{arm}/raw_response.json'))
u = d.get('usage', {}) or {}
res = d.get('result', '') or ''

if d.get('is_error') or 'rate limit' in res.lower() or 'session limit' in res.lower():
    sys.exit(f'FAIL: run errored or hit a limit — no ranked.json written.\n  {res[:200]}')
den = d.get('permission_denials') or []
if den:
    sys.exit(f'FAIL: {len(den)} permission denial(s) — the arm reached for a tool it was '
             f'not given, so this is not a pure {arm} measurement. First: {den[0]}')
cr = u.get('cache_read_input_tokens') or 0
cc = u.get('cache_creation_input_tokens') or 0
# Caching is no longer fatal. This used to sys.exit, which meant a cached run was paid
# for and then thrown away — the finalizer refusing the very thing the runner had just
# bought. What matters is that the record says which condition a run was measured under,
# because a warm run's COST is not comparable to a cold one's (measured 2026-09-14:
# $2.36-$2.71 warm vs $12.24 cold for the same plumbline case shape). Recall is unaffected.
cache_mode = 'cold' if not (cr or cc) else 'warm'
if cache_mode == 'warm':
    print(f'note: warm run (cache_read={cr}, cache_creation={cc}) — cost is NOT '
          f'comparable to a cold run; recall is.', file=sys.stderr)


def extract(res):
    """-> (answer_object_or_None, ranked_list_or_None).

    The prompt mandates a JSON object {contract, defect_location, answer}. Scan for the
    first '{' that decodes to one, then fall back to a bare array for the older arms whose
    prompt asked for that. Never regex for [...]: prose legitimately contains brackets
    ("user.profiles[0].organization"), and a greedy match starts THERE, not at the answer.
    """
    s = res.strip()
    if s.startswith('```'):
        s = re.sub(r'^```[a-zA-Z]*\n?', '', s)
        s = re.sub(r'```\s*$', '', s).strip()
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch == '{':
            try: obj, _ = dec.raw_decode(s[i:])
            except ValueError: continue
            if isinstance(obj, dict) and isinstance(obj.get('answer'), list):
                return obj, obj['answer']

    def arrays():
        for i, ch in enumerate(s):
            if ch != '[':
                continue
            try: arr, _ = dec.raw_decode(s[i:])
            except ValueError: continue
            if arr:
                yield arr

    # Cross-repo: [{"repo": ..., "files": [...]}]. Kept in THAT shape — flattening
    # drops the repo qualification the scorer joins gold on, and skipping this case
    # is worse than failing: the scan then reaches the first inner "files" array and
    # returns one repo's paths as if they were the whole answer. That silently turned
    # a 13-path/4-repo answer into 4 tldraw paths and scored it 0.0 on 2026-09-09.
    #
    # This shape gets its own FULL pass before the flat-string shape below. When both
    # passes shared one loop, a prose bracket earlier in the message won on position
    # alone: on 2026-09-13 a PHASE 2 epilogue quoting `"context_filter": ["call"]` was
    # returned as the whole answer (ranked.json == ["call"], 1 path) while the real
    # 4-repo array sat further down the same string.
    for arr in arrays():
        if all(isinstance(x, dict) and 'repo' in x and 'files' in x for x in arr):
            return None, arr
    # Single-repo: a flat list of paths, not the [0] of an index expression in prose and
    # not a quoted tool argument. Every entry must look like a repo-relative path, which
    # is what separates an answer from prose that merely contains a JSON-shaped bracket.
    for arr in arrays():
        if all(isinstance(x, str) and ('/' in x or re.search(r'\.[A-Za-z0-9]{1,5}$', x))
               for x in arr):
            return None, arr
    return None, None


def _assistant_texts(session_id):
    """Every assistant text block of this run's transcript, newest first."""
    out = []
    if not session_id:
        return out
    for root, _, fs in os.walk(os.path.expanduser('~/.claude/projects')):
        if f'{session_id}.jsonl' not in fs:
            continue
        for line in open(os.path.join(root, f'{session_id}.jsonl')):
            try: r = json.loads(line)
            except Exception: continue
            m = r.get('message') or {}
            if m.get('role') != 'assistant':
                continue
            c = m.get('content')
            if isinstance(c, list):
                out += [b['text'] for b in c
                        if isinstance(b, dict) and b.get('type') == 'text' and b.get('text')]
    return out[::-1]


obj, ranked = extract(res)
# The two-phase prompts end PHASE 1 with the answer array and then keep talking: PHASE 2
# reports the gold comparison, which a gold-blind arm cannot do, so its refusal — not the
# answer — is what `claude -p` returns as `result`. The answer is still in the transcript,
# so fall back to it rather than discarding a run that was already paid for. Newest first,
# so a later restatement of the answer still beats an earlier draft.
if ranked is None:
    for t in _assistant_texts(d.get('session_id')):
        obj, ranked = extract(t)
        if ranked is not None:
            print(f'note: answer recovered from transcript, not the final message', file=sys.stderr)
            break
if ranked is None:
    sys.exit('FAIL: no answer object or path array in the final message or transcript')
if obj is not None:
    json.dump(obj, open(f'{case}/{arm}/agent_answer.json', 'w'), indent=1)
json.dump(ranked, open(f'{case}/{arm}/ranked.json', 'w'), indent=1)

# commands.log is what the scorer's surface gate reads. Built from the session
# transcript, never from the agent's account of what it called: on 2026-09-09 an arm
# self-reported `stakeout: 2, shakedown: 3` having actually run `0` and `6`.


def _trim(inp, cap=400):
    """Serialize a tool's input, bounded, WITHOUT breaking the JSON.

    score_arm.py's fold probe json.loads() this blob to recover `relativePath` and
    `lens`. A blind `json.dumps(inp)[:cap]` cuts mid-string, the parse fails, the probe
    falls back to `args = {}`, and the call still counts in tool_counts while
    contributing no seed. Every collateral_damage call carries a long `reason`, so in
    practice EVERY fold read as `cd_calls: 0` — a run that folded correctly was scored
    as one that never folded. Trim the long free-text values instead and re-serialize,
    so the object always parses.
    """
    out, blob = dict(inp), json.dumps(inp)
    if len(blob) <= cap:
        return blob
    if isinstance(out.get('reason'), str):
        out['reason'] = out['reason'][:80] + '...'
    for k, v in list(out.items()):
        if isinstance(v, list) and len(v) > 6:
            out[k] = v[:6] + [f'...+{len(v) - 6} more']
        elif isinstance(v, str) and len(v) > 200 and k != 'relativePath':
            out[k] = v[:200] + '...'
    blob = json.dumps(out)
    # Still oversized: drop free text rather than emit an unparseable line.
    if len(blob) > cap:
        out.pop('reason', None)
        blob = json.dumps(out)
    return blob
# Stakeout's query enrichment fails OPEN: when its LLM provider errors the tool
# still returns rows, silently degraded to literal-only matching, and says so in
# ONE line of the result header. Measured 2026-09-15, that flip moved a gold file
# (redux-toolkit combineSlices.ts) from absent-from-15-rows to rank 1 on a
# byte-identical query. It did NOT visibly move run-level recall — degraded arms
# score mid-distribution among healthy ones on the same case — so this is recorded
# as a RUN CONDITION to filter by, not as an explanation for a bad score. Nothing
# else in the artifacts preserves it, because commands.log holds calls and not
# results.
ENRICH_DOWN = 'enrichment=UNAVAILABLE'
ENRICH_UP = ('enriched terms=', 'enriched paths=', 'enriched modules=')
STAKEOUT = 'mcp__plumbline__stakeout'


def _result_text(content):
    """Flatten a tool_result's content to searchable text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(b.get('text', '') for b in content
                         if isinstance(b, dict) and b.get('type') == 'text')
    return ''


sid = d.get('session_id')
calls, counts = [], collections.Counter()
# tool_use_id -> tool name, so a result can be attributed to the call that made it.
tool_by_id, enrich = {}, []
for root, _, fs in os.walk(os.path.expanduser('~/.claude/projects')):
    if f'{sid}.jsonl' in fs:
        for line in open(os.path.join(root, f'{sid}.jsonl')):
            try: r = json.loads(line)
            except Exception: continue
            ct = (r.get('message') or {}).get('content')
            if isinstance(ct, list):
                for b in ct:
                    if not isinstance(b, dict):
                        continue
                    if b.get('type') == 'tool_use':
                        counts[b['name']] += 1
                        calls.append((b['name'], _trim(b.get('input', {}))))
                        tool_by_id[b.get('id')] = b.get('name')
                    elif b.get('type') == 'tool_result':
                        if tool_by_id.get(b.get('tool_use_id')) != STAKEOUT:
                            continue
                        t = _result_text(b.get('content'))
                        if ENRICH_DOWN in t:
                            enrich.append('down')
                        elif any(m in t for m in ENRICH_UP):
                            enrich.append('up')
                        else:
                            # A pathContains-only scan never enriches; not a failure.
                            enrich.append('unknown')

_up, _down = enrich.count('up'), enrich.count('down')
enrichment = {
    'up': _up, 'down': _down, 'unknown': enrich.count('unknown'),
    'stakeout_calls': len(enrich),
    'verdict': ('NO_STAKEOUT' if not enrich else
                'FLAPPED' if _up and _down else      # mixed WITHIN one run
                'DEGRADED' if _down else
                'HEALTHY' if _up else 'INDETERMINATE'),
}
with open(f'{case}/{arm}/commands.log', 'w') as fh:
    for i, (name, inp) in enumerate(calls, 1):
        fh.write(f'{i:02d}. {name} {inp}\n')
json.dump({'session_id': sid, 'usd_per_query_cli': d.get('total_cost_usd'),
           'duration_ms': d.get('duration_ms'), 'num_turns': d.get('num_turns'),
           'prompt_caching': ('DISABLED (cache_read=0, cache_creation=0)' if cache_mode == 'cold'
                              else f'ENABLED (cache_read={cr}, cache_creation={cc})'),
           'cache_mode': cache_mode,
           'cost_comparable_to_cold_runs': cache_mode == 'cold',
           'tokens': {'input': u.get('input_tokens'), 'cache_read': cr,
                      'cache_creation': cc, 'output': u.get('output_tokens')},
           'tools_called': dict(counts),
           'enrichment': enrichment},
          open(f'{case}/{arm}/cost.json', 'w'), indent=2)

print(json.dumps({'arm': arm, 'session': sid, 'usd': d.get('total_cost_usd'),
                  'turns': d.get('num_turns'), 'cache_read': cr, 'paths': len(ranked),
                  'tools': dict(counts),
                  'enrichment': enrichment['verdict']}, indent=1))
