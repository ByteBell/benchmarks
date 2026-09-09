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
if cr or cc:
    sys.exit(f'FAIL: cache was read (cache_read={cr}, cache_creation={cc}).')


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
    for i, ch in enumerate(s):
        if ch == '[':
            try: arr, _ = dec.raw_decode(s[i:])
            except ValueError: continue
            if not arr:
                continue
            # Cross-repo: [{"repo": ..., "files": [...]}]. Kept in THAT shape — flattening
            # drops the repo qualification the scorer joins gold on, and skipping this case
            # is worse than failing: the scan then reaches the first inner "files" array and
            # returns one repo's paths as if they were the whole answer. That silently turned
            # a 13-path/4-repo answer into 4 tldraw paths and scored it 0.0 on 2026-09-09.
            if all(isinstance(x, dict) and 'repo' in x and 'files' in x for x in arr):
                return None, arr
            # Single-repo: a flat list of paths, not the [0] of an index expression in prose
            if all(isinstance(x, str) for x in arr):
                return None, arr
    return None, None


obj, ranked = extract(res)
if ranked is None:
    sys.exit('FAIL: no answer object or path array in the final message')
if obj is not None:
    json.dump(obj, open(f'{case}/{arm}/agent_answer.json', 'w'), indent=1)
json.dump(ranked, open(f'{case}/{arm}/ranked.json', 'w'), indent=1)

# commands.log is what the scorer's surface gate reads. Built from the session
# transcript, never from the agent's account of what it called: on 2026-09-09 an arm
# self-reported `stakeout: 2, shakedown: 3` having actually run `0` and `6`.
sid = d.get('session_id')
calls, counts = [], collections.Counter()
for root, _, fs in os.walk(os.path.expanduser('~/.claude/projects')):
    if f'{sid}.jsonl' in fs:
        for line in open(os.path.join(root, f'{sid}.jsonl')):
            try: r = json.loads(line)
            except Exception: continue
            ct = (r.get('message') or {}).get('content')
            if isinstance(ct, list):
                for b in ct:
                    if isinstance(b, dict) and b.get('type') == 'tool_use':
                        counts[b['name']] += 1
                        calls.append((b['name'], json.dumps(b.get('input', {}))[:400]))
with open(f'{case}/{arm}/commands.log', 'w') as fh:
    for i, (name, inp) in enumerate(calls, 1):
        fh.write(f'{i:02d}. {name} {inp}\n')
json.dump({'session_id': sid, 'usd_per_query_cli': d.get('total_cost_usd'),
           'duration_ms': d.get('duration_ms'), 'num_turns': d.get('num_turns'),
           'prompt_caching': 'DISABLED (cache_read=0, cache_creation=0 asserted)',
           'tokens': {'input': u.get('input_tokens'), 'cache_read': cr,
                      'cache_creation': cc, 'output': u.get('output_tokens')},
           'tools_called': dict(counts)},
          open(f'{case}/{arm}/cost.json', 'w'), indent=2)

print(json.dumps({'arm': arm, 'session': sid, 'usd': d.get('total_cost_usd'),
                  'turns': d.get('num_turns'), 'cache_read': cr, 'paths': len(ranked),
                  'tools': dict(counts)}, indent=1))
