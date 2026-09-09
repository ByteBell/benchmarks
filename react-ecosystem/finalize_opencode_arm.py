#!/usr/bin/env python3
"""Extract ranked.json / commands.log / cost.json from an opencode run.

    finalize_opencode_arm.py <case_dir> <arm_name> <model>

Separate from finalize_arm.py because the envelopes differ. `claude -p
--output-format json` returns ONE object with {result, session_id, total_cost_usd,
num_turns}. `opencode run --format json` emits a stream of events, so the answer has
to be recovered from the last assistant text rather than read from a field.

Both shapes are accepted: if the file parses as a single object carrying `result`,
that is used directly; otherwise every line is scanned and the last assistant text
block wins. An unrecognised shape is a hard failure — it must never silently produce
an empty ranked.json that then scores as a bad run.
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def texts_and_tools(path):
    """(list of assistant text blobs, ordered tool calls) from either envelope."""
    raw = open(path).read().strip()
    if not raw:
        sys.exit('FAIL: raw_response.json is empty — the run produced nothing')
    # Single-object (claude-style) envelope.
    try:
        o = json.loads(raw)
        if isinstance(o, dict) and 'result' in o:
            if o.get('is_error'):
                sys.exit(f'FAIL: run errored — {str(o.get("result"))[:200]}')
            return [o['result']], []
    except json.JSONDecodeError:
        pass
    # Event-stream envelope: one JSON value per line.
    texts, tools = [], []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        for part in _walk(e):
            if part[0] == 'text':
                texts.append(part[1])
            else:
                tools.append(part[1])
    if not texts and not tools:
        sys.exit('FAIL: could not parse opencode output — neither a result object nor '
                 'recognisable events. Inspect raw_response.json and extend _walk().')
    return texts, tools


def _walk(e):
    """Yield ('text', str) and ('tool', {name,input}) from one event, shape-agnostically."""
    if isinstance(e, dict):
        t = e.get('type') or ''
        if t in ('text', 'text-delta') and isinstance(e.get('text'), str):
            yield ('text', e['text'])
        if e.get('role') == 'assistant' and isinstance(e.get('content'), str):
            yield ('text', e['content'])
        if 'tool' in t and (e.get('tool') or e.get('name')):
            yield ('tool', {'name': e.get('tool') or e.get('name'),
                            'input': e.get('input') or e.get('args') or {}})
        for v in e.values():
            if isinstance(v, (dict, list)):
                yield from _walk(v)
    elif isinstance(e, list):
        for v in e:
            yield from _walk(v)


def extract_answer(texts):
    """Last JSON array, or the `answer` of the last JSON object, in the assistant text."""
    dec = json.JSONDecoder()
    for blob in reversed(texts):
        s = blob.strip()
        s = re.sub(r'^```(?:json)?|```$', '', s, flags=re.M).strip()
        for i, ch in enumerate(s):
            if ch not in '[{':
                continue
            try:
                val, _ = dec.raw_decode(s[i:])
            except ValueError:
                continue
            if isinstance(val, list) and val and all(isinstance(x, str) for x in val):
                return val
            if isinstance(val, dict) and isinstance(val.get('answer'), list):
                return val['answer']
    return None


def main(case, arm, model):
    d = os.path.join(ROOT, case, arm)
    texts, tools = texts_and_tools(os.path.join(d, 'raw_response.json'))
    ans = extract_answer(texts)
    if not ans:
        sys.exit('FAIL: no ranked list found in the final message — not writing an '
                 'empty ranked.json, which would score as a bad run rather than a '
                 'broken harness. Check raw_response.json.')
    json.dump(ans, open(os.path.join(d, 'ranked.json'), 'w'), indent=1)

    with open(os.path.join(d, 'commands.log'), 'w') as fh:
        for i, t in enumerate(tools, 1):
            fh.write(f"{i:02d}. {t['name']} {json.dumps(t['input'])[:400]}\n")

    json.dump({
        'session_id': None, 'arm': arm, 'model': model,
        'usd': None, 'duration_ms': None, 'num_turns': None,
        'prompt_caching': 'UNKNOWN - opencode does not report cache counters',
        'tokens': {'input': None, 'output': None, 'cache_read': None, 'cache_creation': None},
        'tools_called': {t['name']: sum(1 for x in tools if x['name'] == t['name'])
                         for t in tools},
        'not_comparable_reason': [
            'opencode has no --allowedTools/--disallowedTools: its built-in Read, Grep, '
            'Bash and Write were available alongside the MCP surface',
            'MCP comes from the global ~/.config/opencode/opencode.jsonc, so the surface '
            'is not pinned per-run the way every claude arm pins it',
            'no cost or cache counters are reported, so USD and cache_read/cache_creation '
            'are unattributable and MUST NOT be compared against headless claude arms',
        ],
    }, open(os.path.join(d, 'cost.json'), 'w'), indent=1)
    print(f'ranked.json: {len(ans)} paths · commands.log: {len(tools)} calls · cost.json written')


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
