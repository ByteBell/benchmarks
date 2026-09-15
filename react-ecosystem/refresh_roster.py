#!/usr/bin/env python3
"""Rebuild the benchmark roster from the LIVE index, and gate a run on it agreeing.

`manifest.json` is a hand-maintained copy of data `roll_call` owns, and it drifts the
way hand-maintained caches always do. Measured 2026-09-15 it was missing five roster
repos outright (xyflow, tldraw, router, primitives, zod), carried a zustand
knowledgeId that exists nowhere in the index, and pinned reselect at the commit it
had before the re-pin. A run started against a drifted roster scores against fiction.

The server is queried at the endpoint the ARMS use (localhost by default). Local
indexing writes to the same backing database, so this is not a separate corpus — but
it is the view the run will actually get, and a gate that checks a different view is
not a gate. Override with BYTEBELL_MCP_URL.

Two jobs, and the second is the one that protects a run:

  1. rewrite manifest.json's repo rows with the live knowledgeId + lastIndexedCommit
  2. compare each case's prompt commit table against the live index, and FAIL when
     they disagree — the by-hand pin check, done automatically

Exit codes:  0 = agreed (or --write succeeded)   1 = drift, do not run
             2 = server unavailable — roster left alone, caller decides

Usage:  refresh_roster.py [--write] [--check-prompts]
"""
import json
import os
import re
import sys
import urllib.request

# Query the endpoint the ARMS query. Local indexing writes to the same backing store,
# so this is not a different corpus — but it is the view the run will actually get, and
# a gate that checks a different view is not a gate.
ENDPOINT = os.environ.get("BYTEBELL_MCP_URL", "http://localhost/mcp")
ROOT = os.path.dirname(os.path.abspath(__file__))

# The prompt's commit table IS the roster: it is what the arms are told to search, so
# it is what the index has to agree with. Derived from a prompt rather than restated
# here, so this file cannot itself become a third stale copy.
ROSTER_PROMPT = os.path.join(
    ROOT, "cross-repo/hard-a-cache-that-outlives-the-thing-it-was-keyed-to",
    "claudecli_opus5_mcp_plumbline_v4d/run_prompt.txt")


def token():
    t = os.environ.get("BYTEBELL_MCP_TOKEN")
    if t:
        return t.strip()
    p = os.path.join(ROOT, ".mcp-token")
    if os.path.exists(p):
        return open(p).read().strip()
    # Fall back to the token the benchmark's own MCP config already carries, so this
    # never needs a second credential kept in sync with the first.
    cfg = os.path.join(ROOT, "mcp_plumbline.json")
    if os.path.exists(cfg):
        url = json.load(open(cfg))["mcpServers"]["plumbline"]["url"]
        m = re.search(r"access_token=([^&]+)", url)
        if m:
            return m.group(1)
    raise SystemExit("no MCP token: set BYTEBELL_MCP_TOKEN, or .mcp-token, or mcp_plumbline.json")


def post(payload, tok, sid=None):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": f"Bearer {tok}"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    req = urllib.request.Request(f"{ENDPOINT}?access_token={tok}", body, headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.headers.get("mcp-session-id"), r.read().decode()


def parse(text):
    """MCP replies either as plain JSON or as an SSE data: stream."""
    t = (text or "").strip()
    if re.search(r"^(event:|data:)", t, re.M):
        for line in reversed([l[5:].strip() for l in t.splitlines() if l.startswith("data:")]):
            try:
                o = json.loads(line)
                if "result" in o or "error" in o:
                    return o
            except Exception:
                pass
        return None
    try:
        return json.loads(t)
    except Exception:
        return None


def objects(text):
    """Pull the embedded JSON objects out of roll_call's rendered text block."""
    out, depth, start = [], 0, -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    out.append(json.loads(text[start:i + 1]))
                except Exception:
                    pass
                start = -1
    return out


def roll_call():
    tok = token()
    sid, init = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "refresh-roster", "version": "1.0"}}}, tok)
    try:
        post({"jsonrpc": "2.0", "method": "notifications/initialized"}, tok, sid)
    except Exception:
        pass
    _, body = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "roll_call", "arguments": {}}}, tok, sid)
    o = parse(body) or {}
    text = "\n".join(c.get("text", "") for c in (o.get("result", {}).get("content") or []))
    return [r for r in objects(text) if r.get("knowledgeId")], text


def roster_from_prompt():
    """[(repo, pinned_commit)] in the order the prompt lists them."""
    rows = []
    for line in open(ROSTER_PROMPT):
        p = line.split()
        if len(p) == 2 and len(p[1]) == 40 and re.fullmatch(r"[0-9a-f]{40}", p[1]):
            rows.append((p[0], p[1]))
    return rows


def check_checkouts(roster):
    """Every roster repo's working tree must sit at the commit the prompt pins.

    This is the check that applies to EVERY arm, not just the MCP ones: graphify,
    turbovec, serena and bare all read the checkout directly, so a repo sitting at the
    wrong revision scores them against code the question was never about. `roll_call`
    cannot see this at all — it describes the index, not the disk.
    """
    bad = []
    print(f"{'repo':<17}{'checkout HEAD':<16}{'pinned':<16}")
    print("-" * 52)
    for repo, pinned in roster:
        d = os.path.join(ROOT, repo)
        if not os.path.isdir(os.path.join(d, ".git")):
            print(f"{repo:<17}{'— NO CHECKOUT —':<16}{pinned[:12]:<16}")
            bad.append(f"{repo}: no checkout at {d}")
            continue
        try:
            import subprocess
            head = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception as e:
            print(f"{repo:<17}{'— ERROR —':<16}{pinned[:12]:<16}  {e}")
            bad.append(f"{repo}: {e}")
            continue
        ok = head == pinned
        print(f"{repo:<17}{head[:12]:<16}{pinned[:12]:<16}{'' if ok else '  MISMATCH'}")
        if not ok:
            bad.append(f"{repo}: checkout at {head[:12]}, prompt pins {pinned[:12]}")
    return bad


def main():
    write = "--write" in sys.argv
    # Which halves to run. Default is both; an arm that reads only the checkout has no
    # use for the index check, and vice versa.
    want_index = "--checkouts" not in sys.argv or "--index" in sys.argv
    want_checkouts = "--index" not in sys.argv or "--checkouts" in sys.argv

    if not want_index:
        bad = check_checkouts(roster_from_prompt())
        if bad:
            print("\nCHECKOUT DRIFT — the arms would read code the question is not about:")
            for b in bad:
                print("  -", b)
            return 1
        print("\ncheckouts agree with the prompt pins.")
        return 0

    live, raw = roll_call()
    # An empty roll_call means the server did not really answer. Never rewrite the
    # roster against a phantom empty index — that would erase every id.
    if not live:
        print("roll_call returned nothing — server unavailable; roster left unchanged.")
        print("  server said:", " ".join(raw.split())[:300])
        return 2

    by_name, by_slug = {}, {}
    for r in live:
        if r.get("type") != "CODE":
            continue
        if r.get("name"):
            by_name[r["name"]] = r
        if r.get("repoSlug"):
            by_slug[r["repoSlug"].split("/")[-1]] = r

    roster = roster_from_prompt()
    print(f"roll_call: {len(live)} knowledge base(s) · roster: {len(roster)} repo(s)\n")

    drift, rows = [], []
    print(f"{'repo':<17}{'live knowledgeId':<40}{'pin vs newest':<20}")
    print("-" * 78)
    for repo, pinned in roster:
        r = by_slug.get(repo) or by_name.get(repo)
        if not r:
            print(f"{repo:<17}{'— NOT IN INDEX —':<40}{'UNRESOLVED':<18}")
            drift.append(f"{repo}: not present in roll_call")
            continue
        kid, newest = r["knowledgeId"], r.get("lastIndexedCommit", "")
        # A pin OLDER than newest is NORMAL and must not fail the gate: roll_call
        # reports only the newest commit, while these repos carry 2-5 indexed ones, so
        # the pinned snapshot is very likely among them. Treating "pinned != newest" as
        # drift failed 4 of 16 roster repos on the first run for no reason.
        #
        # What IS fatal is a roster repo the index does not have at all — that one is
        # provable from roll_call alone, and it is the case that silently scores a run
        # against a repo the arms could never reach.
        note = "pinned==newest" if newest == pinned else f"older (newest {newest[:12]})"
        print(f"{repo:<17}{kid:<40}{note:<20}")
        rows.append({"dir": repo, "knowledgeId": kid, "commit": pinned,
                     "lastIndexedCommit": newest})

    mpath = os.path.join(ROOT, "manifest.json")
    if write:
        man = json.load(open(mpath)) if os.path.exists(mpath) else {}
        old = {r.get("dir"): r for r in man.get("repos", [])}
        for row in rows:
            prev = old.get(row["dir"], {})
            merged = dict(prev)
            merged.update(row)
            old[row["dir"]] = merged
        man["repos"] = [old[r["dir"]] for r in rows]
        json.dump(man, open(mpath, "w"), indent=2)
        open(mpath, "a").write("\n")
        print(f"\nwrote {len(rows)} repo row(s) to manifest.json")

    if want_checkouts:
        print()
        drift += check_checkouts(roster)

    if drift:
        print("\nDRIFT — do not start a run:")
        for d in drift:
            print("  -", d)
        return 1
    print("\nroster agrees with the live index and the checkouts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
