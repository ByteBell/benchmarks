#!/usr/bin/env python3
"""Regenerate every retrieval prompt: repo + commit named, RELATIVE paths, required
artifacts listed, list cap from bench_config. Arm folders are discovered, never
invented."""
import json, glob, os, sys

ROOT = '/Users/sauravverma/programs/benchmarks/react-ecosystem'
sys.path.insert(0, ROOT)
from bench_config import RANKED_LIST_CAP

REPO = 'calcom/cal.com'
MAXN = RANKED_LIST_CAP
STD = ['claudecli_opus5_bare', 'claudecli_opus5_mcp_graphify', 'claudecli_opus5_mcp_serena']
RULES = [
 ('graphify', "You may ONLY use the graphify MCP tools. You have no filesystem, shell, or search access. Use the graph: search concepts, traverse relationships, inspect neighbors, follow reverse dependencies."),
 ('serena',   "You may ONLY use the serena MCP tools. You have no shell and no Claude Code filesystem tools. Use the language server: find symbols, find references to them, follow implementations and declarations."),
 ('openspecs',"You may ONLY use the openspecs-index MCP tools. You have no filesystem, shell, or search access. Search the knowledge graph, then confirm any claim about what the code does against the verbatim source the graph returns."),
 # `plumbline` matches no other key, so without this entry it fell through to the
 # bare-filesystem rule and told the arm to Grep the checkout.
 # Deliberately as bare as the sibling rules, and it names no tool. Which tool the
 # arm reaches for first is the thing under measurement: an arm that answers with
 # `shakedown` regex and never calls `stakeout` is a finding about the tool
 # descriptions, not a prompt defect to be written around here. Steering it from
 # the prompt would make a win unattributable — retriever or instructions?
 # Validity is enforced after the fact by the surface gate in score_arm.py.
 ('plumbline', "You may ONLY use the plumbline MCP tools. You have no filesystem, shell, or search access."),
 # Same fall-through hazard as plumbline had, and it went unnoticed longer because the
 # turbovec prompts in the tree were written by hand and looked right. Without this key
 # `rules_for` reaches the '' fallback and emits "use Read, Grep, Glob" — a bare-arm
 # prompt wearing a turbovec label, which scores as a turbovec finding.
 ('turbovec', "You may ONLY use the turbovector MCP tools (search, get_file, read_lines, index_info). You have no filesystem, shell, or grep access. `search` is the only discovery mechanism: describe the code you want in natural language, or paste an identifier or signature; then use get_file / read_lines to inspect what a hit points at."),
 ('',         "Use your filesystem tools (Read, Grep, Glob) to search the checkout. You have no shell and no network."),
]

TPL = """You are performing an information-retrieval task against a codebase.

REPOSITORY:  {repo}
COMMIT:      {sha}
CHECKOUT:    {rel_repo}
             All paths in this prompt are relative to the benchmark root
             ({root}). The checkout is the repository at exactly the
             commit above - do not use any other revision, and do not use outside
             knowledge of the project's later history.

QUERY (a symptom-level bug report):
{query}

TASK: Identify which files in this repository, as it stands at the commit above, would have to
change to fix the issue described.

RULES:
- {rules}
- Do not stop at the first plausible file. The fix likely spans multiple files.
- Every path you return must be relative to the checkout root ({rel_repo}), exactly as it appears
  at this commit: no leading slash, no absolute prefix, no path that does not exist there.
  Example of the required form: packages/features/bookings/lib/handleNewBooking.ts

DELIVERABLES: the harness captures this run for you - it records the full transcript
(output.log), every tool call (commands.log) and the cost (cost.json) from the session
transcript after the run ends. Do NOT compute your own cost, and do not call /session-analysis
or any shell helper to do it: those tools are not on your surface, the attempt is recorded as a
permission denial, and a denial DISQUALIFIES the whole run no matter how good the answer was.
The harness also writes ranked.json from your final message.
Your only job is the ranked list.

FINAL MESSAGE: output ONLY a JSON object, no prose and no markdown fence, in exactly
this shape - the harness saves it verbatim as agent_answer.json:

{{
  "contract": "<one paragraph stating the rule the defect breaks, phrased so it applies
                to every file you name - not a restatement of the symptom>",
  "defect_location": {{
    "repo": "{repo}",
    "summary": "<where the defect is and why it breaks the contract, citing
                 <path>:<line-range> for every claim you make>"
  }},
  "answer": ["packages/foo/bar.ts", "packages/foo/baz.tsx"]
}}

"answer" is the ranked list the harness extracts into ranked.json: checkout-relative paths, most
likely first, maximum {maxn}. "contract" and "defect_location" are graded alongside it,
so state them from what your tools actually showed you - not from what you expect a
codebase like this to contain.
"""

def rules_for(arm):
    return next(r for k, r in RULES if k in arm)

# The cases live under cal.com.processed/. This globbed cal.com/ — which holds one
# stray checkout and no case.json at all — so the script matched zero cases and
# "rewrote 0 prompts across 0 cases" read as success.
CASES_DIR = 'cal.com.processed'

# An arm that has already produced an answer is FROZEN: its prompt is the record of
# what was actually asked, and a rewrite silently desyncs it from the result.json
# beside it. 57 arms were in that state when this guard was added.
RUN_EVIDENCE = ('result.json', 'raw_response.json', 'ranked.json')
FORCE = '--force' in sys.argv

n = skipped = 0
cases = sorted(glob.glob(f'{ROOT}/{CASES_DIR}/*/case.json'))
for cp in cases:
    case = os.path.dirname(cp)
    c = json.load(open(cp))
    sha = c['index_at']
    rel_case = os.path.relpath(case, ROOT)
    # discover real arm folders; only fall back to the standard three if none exist
    arms = sorted(os.path.basename(d) for d in glob.glob(f'{case}/*')
                  if os.path.isdir(d) and os.path.basename(d) != 'repo'
                  and any(os.path.exists(f'{d}/{f}') for f in
                          ('retrieval_prompt.txt', 'ranked.json', 'result.json', 'raw_response.json')))
    if not arms:
        arms = STD
    for arm in arms:
        if not FORCE and any(os.path.exists(f'{case}/{arm}/{f}') for f in RUN_EVIDENCE):
            skipped += 1
            continue
        os.makedirs(f'{case}/{arm}', exist_ok=True)
        open(f'{case}/{arm}/retrieval_prompt.txt', 'w').write(TPL.format(
            repo=REPO, sha=sha, root=ROOT, query=c['query'], rules=rules_for(arm),
            rel_repo=f'{rel_case}/repo', rel_arm=f'{rel_case}/{arm}', maxn=MAXN))
        n += 1
    spec_p = f'{ROOT}/cal.com.unprocessed/prompts/{sha}.json'
    if os.path.exists(spec_p):
        s = json.load(open(spec_p))
        s['repo_checkout'] = f'{rel_case}/repo'
        s['output_location'] = rel_case
        s['artifacts_dir'] = f'{rel_case}/<copilot_name>_<model_name>[_mcp]   (create it; see arm_folder_naming)'
        s['paths_are_relative_to'] = ROOT
        json.dump(s, open(spec_p, 'w'), indent=1)
print(f'rewrote {n} prompts across {len(cases)} cases; skipped {skipped} already-run arm(s) (--force to override)')
