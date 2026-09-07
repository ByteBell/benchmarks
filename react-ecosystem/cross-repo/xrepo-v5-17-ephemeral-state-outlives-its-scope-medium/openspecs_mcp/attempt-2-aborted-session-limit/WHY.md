Aborted 2026-09-07 11:43 — NOT a fault in the arm or the corrected prompt.

  terminal_reason : api_error
  result          : "You've hit your session limit · resets 12:30pm (Asia/Calcutta)"
  reached         : 36 turns, 35 tool calls, 5,065,814 input tokens, $25.88
  produced        : no final JSON answer — killed mid-run

What it DID prove before dying: the coverage fix works. All 13/13 reachable
roster repos received a scoped call (baseline: 9/13), including react and
zustand, the two gold repos the baseline never queried at all. manhunt went
from 0 calls to 8 — the CLEARED>=2-attempts rule firing as designed.

What it also exposed: cost. 5.07M input tokens vs the baseline's 1.60M (3.2x)
for 36 turns vs 23. With DISABLE_PROMPT_CACHING=1 every turn re-sends the whole
accumulated context at full price, so cost grows quadratically in turn count.
The corrected prompt's floors (>=2 scoped calls per repo + mandatory file opens)
add turns, and each stakeout result is a 25-30 file listing that stays in
context. Projected completion cost was ~$30+, vs baseline $8.42 and the
filesystem arm's $19.86.
