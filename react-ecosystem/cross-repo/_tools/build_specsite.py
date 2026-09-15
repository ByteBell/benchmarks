#!/usr/bin/env python3
"""For each selected cross-repo case, collect every gold file and every file any
arm returned, and copy that file's spec-site page JSON into <case>/specsite/.

    ./build_specsite.py                 # every hard-* case (the original default)
    ./build_specsite.py <case> [<case>] # exactly the named cases

Pages are resolved AT THE ROSTER PIN and nowhere else. A repo whose pinned commit
has no spec-site yields entries with no page and a reason — it never borrows pages
from another commit, because a page from a different revision describes different
code than the arms searched.

Pages come from two places, in order: the local knowledge tree under `GH`, and the
S3 mirror the ingest wrote (`_tools/specsite_s3.ts`, cached under `_specsite_cache/`).
The local tree only ever holds commits ingested ON THIS HOST, so it is a cache and
never the authority on what is indexed — the graph and the mirror are.

A case is rebuilt from scratch: <case>/specsite is removed and rewritten, so a
manifest always reflects the arms that have a ranked.json AT BUILD TIME. Re-run a
case after finalizing more arms or its README will understate the coverage."""
import json, os, shutil, glob, collections, subprocess, sys

CROSS = "/Users/sauravverma/programs/benchmarks/react-ecosystem/cross-repo"
GH = "/Users/sauravverma/programs/kube-package/temp/orgs/36af0f7a-fdf4-4497-9795-19d263268800/github"
ORG = "36af0f7a-fdf4-4497-9795-19d263268800"
ENV_FILE = "/Users/sauravverma/programs/kube-package/.env"
CACHE = f"{CROSS}/_specsite_cache"
FETCHER = f"{CROSS}/_tools/specsite_s3.ts"

# benchmark repo name -> (pinned commit, knowledgeId). The knowledgeId is the graph's
# opaque handle (from roll_call) and is what keys the S3 mirror; it is NOT derivable
# from the repo name, and the repo dir names on local disk are not authoritative.
ROSTER = {
    "redux":          ("3aa561f9fc13f3287e6d83764f85fe0e82437c5f", "c0c638c58b7d1844702ea4c7f14c62db"),
    "redux-toolkit":  ("b1c5130154c454ca8387f55da6122fcb507c560c", "5129210f7edc89d624231a57a3b76770"),
    "react-redux":    ("ad5d1e0816d0cb1464b25cf2853f2a7d73433f5b", "874bb764335b006f6e1f2df028475246"),
    "reselect":       ("950112a328f71d97d18fd543159d6fdefa432b38", "2d8056b4-75b0-5442-b99d-b9762703683b"),
    "redux-thunk":    ("184205d49f707c6f203269e0d39ad85824801816", "d4803d2763e3433edc5c92f7d7619c03"),
    "react":          ("3a717e42438afac81020cdec297dadb5613a4304", "2a9d1614-943b-44ad-b7cf-edda2f37ddf9"),
    "jotai":          ("5c4ca26b0db5571114be58393e17854a771f7790", "581a242e-3f4c-45db-8e84-c69cf159ac44"),
    "zustand":        ("beca84e600e4e250f6b244d22878e72948f331c7", "035d8cb2-942a-5d88-b3d5-6fadafbb3f99"),
    "db":             ("7f0fa36ff6d48b0494ed5f6a1223fd18c317e41f", "659659a1-0515-4ae1-92a9-1961c8c10abe"),
    "xyflow":         ("360f5b13e2bc6899ea06b4be1a49b068d86926cf", "6f1f2a40-aa51-4fe1-b9d2-d33ee4571368"),
    "query":          ("46d7f02f1c7b9fcd3255082cc7103e8bfa3dab76", "0c574639-1088-4233-a793-3a4397e6c431"),
    "table":          ("d08af367e11c539bb7e18c2472aa761149ef6db6", "08e0b828-ac04-4dce-8e73-eb2ceb151072"),
    "tldraw":         ("5590d14d8edd4faab7dc1177b6e1adb28876fd23", "91b23c56-1aee-5c86-a34b-c66f8970d025"),
    "redux-devtools": ("f4b4668c30ae08920c59a76cc4629c35c16ef0fa", "2d9528990f33c5f5e3ee7442fbfe04a4"),
    "router":         ("3dee5b2e9453d01a3172c73426959007b290b3fc", "328fd1db-0faf-59bd-b628-c446e782693a"),
    # Added 2026-09-15. These two are in the live v7 roster (build_prompts.py) and are gold
    # repos for xrepo-v7-6, but were missing here — so every specsite build silently reported
    # "no page" for them, which reads as an index-coverage gap when it is a roster omission.
    # Pin and knowledgeId both confirmed against roll_call; the pins equal the index tips.
    "primitives":     ("58164e06ca67dff9a68dd375d858180cfdc8ec27", "d7776710-b2ba-56bc-90f3-29821aad2d81"),
    "zod":            ("912f0f51b0ced654d0069741e7160834dca742ee", "94e564f9-74f5-573e-96ff-06cd48e2f0a0"),
    # redux-thunk is NOT in the live v7 roster and roll_call does not list it at all, so the
    # graph holds no knowledge for it and no pin can make it yield a page. Its entry is left
    # here, already equal to its checkout HEAD, purely so an older case referencing it still
    # resolves; it is dead weight for v7 and can be dropped once nothing reads it.
}

# Why a requested path has no page, keyed by the scan-manifest `kind` the fetcher reports.
KIND_REASON = {
    "absent": "not present in the repo scan at the pinned commit",
    "unknown": "no scan manifest is mirrored for this commit, so the scan kind cannot be read. A file "
               "with no page AND no node in the graph was dropped as kind=oversized (the graph writers "
               "filter those out); one with a node but empty analysis prose is kind=big",
    "small": "scanned as kind=small but no spec page was generated",
    "big": "kind=big — analysed in chunks, but spec-site emits pages for kind=small only "
           "(loadFiles in @bytebell/spec-site skips every non-small manifest entry)",
    "oversized": "kind=oversized — dropped at the scan walker before analysis, so this commit has "
                 "neither a spec page nor file analysis for it",
    "page-listed-but-object-missing": "page-map lists a page but the mirrored object is missing",
}

# ---- local knowledge tree, scoped to ONE knowledge at ONE commit ---------
# Two layouts exist: the current `<kid>/<owner>/<repo>/<branchId>/<commit>/` and an
# older `<kid>/<owner>/<repo>/<commit>/` with no branchId segment. Both are matched
# so a repo ingested before the branchId split is still readable.
def local_sites(kid, commit):
    return sorted(
        glob.glob(f"{GH}/{kid}/*/*/*/{commit}/meta/spec-site")
        + glob.glob(f"{GH}/{kid}/*/*/{commit}/meta/spec-site")
    )

_local_index = {}
def local_index(kid, commit):
    """-> {relative path: page json file} for one knowledge at one commit."""
    key = (kid, commit)
    if key not in _local_index:
        out = {}
        for site in local_sites(kid, commit):
            try:
                pm = json.load(open(f"{site}/page-map.json"))
            except OSError:
                continue
            for sha, path in (pm.get("files") or {}).items():
                page = f"{site}/files/{sha}.json"
                if path not in out and os.path.exists(page):
                    out[path] = page
        _local_index[key] = out
    return _local_index[key]

# ---- per case ------------------------------------------------------------
def collect(case):
    cdir = f"{CROSS}/{case}"
    gold, runs, arms = set(), collections.defaultdict(set), {}

    g = json.load(open(f"{cdir}/golden.json"))
    for e in g["expected"]:
        for f in e["files"]:
            gold.add((e["repo"], f))

    for arm in sorted(os.listdir(cdir)):
        adir = f"{cdir}/{arm}"
        if not os.path.isdir(adir) or arm.startswith("_") or arm == "specsite":
            continue
        rj = f"{adir}/ranked.json"
        if not os.path.exists(rj):
            arms[arm] = "no ranked.json - arm not run (or run not finalized)"
            continue
        arms[arm] = "contributed"
        for blk in json.load(open(rj)):
            for f in blk.get("files", []):
                runs[(blk["repo"], f)].add(arm)
    return gold, runs, arms

def fetch_from_s3(needed):
    """needed: {repo: set(paths)} -> the fetcher's per-repo report (empty on failure)."""
    repos = []
    for repo, paths in sorted(needed.items()):
        commit, kid = ROSTER[repo]
        repos.append({"repo": repo, "knowledgeId": kid, "commit": commit, "paths": sorted(paths)})
    if not repos:
        return {}
    req = json.dumps({"org": ORG, "cacheDir": CACHE, "repos": repos})
    print(f"specsite: asking S3 for {sum(len(r['paths']) for r in repos)} page(s) "
          f"across {len(repos)} repo(s) ...", file=sys.stderr)
    try:
        proc = subprocess.run(
            ["bun", f"--env-file={ENV_FILE}", FETCHER],
            input=req, capture_output=True, text=True, timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"specsite: S3 fetch unavailable ({exc}); local pages only", file=sys.stderr)
        return {}
    if proc.returncode != 0:
        print(f"specsite: S3 fetch failed rc={proc.returncode}: {proc.stderr.strip()[:400]}", file=sys.stderr)
        return {}
    try:
        return json.loads(proc.stdout).get("repos", {})
    except json.JSONDecodeError:
        print(f"specsite: S3 fetch returned no JSON: {proc.stdout.strip()[:400]}", file=sys.stderr)
        return {}

SELECTED = sys.argv[1:]
for c in SELECTED:
    if not os.path.isdir(f"{CROSS}/{c}"):
        sys.exit(f"no such case: {c}")
    if not os.path.exists(f"{CROSS}/{c}/golden.json"):
        sys.exit(f"{c} has no golden.json - nothing to build a spec-site against")

def selected(case):
    return case in SELECTED if SELECTED else case.startswith("hard-")

cases = [c for c in sorted(os.listdir(CROSS)) if selected(c) and os.path.isdir(f"{CROSS}/{c}")]

# One pass over every selected case first, so the S3 round-trip is made once for the
# union of everything they need rather than once per case.
per_case, wanted = {}, collections.defaultdict(set)
for case in cases:
    gold, runs, arms = collect(case)
    per_case[case] = (gold, runs, arms)
    for repo, path in gold | set(runs):
        if repo in ROSTER and path not in local_index(ROSTER[repo][1], ROSTER[repo][0]):
            wanted[repo].add(path)

s3 = fetch_from_s3(wanted)

SUM = []
for case in cases:
    gold, runs, arms = per_case[case]
    cdir = f"{CROSS}/{case}"
    out = f"{cdir}/specsite"
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)

    manifest, copied, missing, from_s3 = [], 0, 0, 0
    for repo, path in sorted(gold | set(runs)):
        entry = {
            "repo": repo, "path": path,
            "role": "gold+run" if (repo, path) in gold and (repo, path) in runs
                    else "gold" if (repo, path) in gold else "run",
            "named_by_arms": sorted(runs.get((repo, path), [])),
        }
        if repo not in ROSTER:
            entry.update(commit=None, specsite=None, source=None,
                         reason="repo is not in the roster - no pinned commit to resolve against")
            manifest.append(entry)
            missing += 1
            continue

        commit, kid = ROSTER[repo]
        entry["commit"] = commit
        rep = s3.get(repo, {})
        src = local_index(kid, commit).get(path)
        source = "local" if src else None
        if src is None:
            sha = (rep.get("resolved") or {}).get(path)
            if sha:
                cached = f"{CACHE}/{kid}/{commit}/files/{sha}.json"
                if os.path.exists(cached):
                    src, source = cached, "s3"

        if src:
            dest = f"{out}/{repo}/{path}.json"
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            entry.update(specsite=os.path.relpath(dest, out), source=source,
                         sha=os.path.basename(src)[:-5])
            copied += 1
            from_s3 += source == "s3"
        else:
            kind = (rep.get("kinds") or {}).get(path)
            reason = (KIND_REASON.get(kind) if kind else None) or rep.get("error") \
                or "no spec-site available for the pinned commit (local tree and S3 mirror both empty)"
            entry.update(specsite=None, source=None, reason=reason)
            if kind:
                entry["scan_kind"] = kind
            missing += 1
        manifest.append(entry)

    json.dump({
        "case": case,
        "gold_files": len(gold),
        "run_files": len(runs),
        "union": len(manifest),
        "spec_pages_copied": copied,
        "spec_pages_from_s3": from_s3,
        "spec_pages_missing": missing,
        "arms": arms,
        "pinned_commits": {r: ROSTER[r][0] for r in sorted({e["repo"] for e in manifest} & set(ROSTER))},
        "sources": {"local": GH, "s3_cache": CACHE},
        "entries": manifest,
    }, open(f"{out}/MANIFEST.json", "w"), indent=1)

    with open(f"{out}/README.md", "w") as fh:
        fh.write(f"""# spec-site pages for `{case}`

One JSON spec page per file, laid out as `<repo>/<path in repo>.json`. The set of
files is the union of

* the {len(gold)} gold files in `../golden.json`, and
* the {len(runs)} files any arm returned in its `ranked.json`.

Every page is taken at that repo's ROSTER PIN — the same commit the arms searched.
A repo whose pinned commit has no page for a file gets no page at all; pages are
never borrowed from another commit.

`MANIFEST.json` says, for each file, whether it is gold, run-returned or both,
which arms named it, the pinned commit, where the page came from (`local` tree or
`s3` mirror), and - when no page was copied - why, including the scan-manifest
`kind` when that is what explains it. {copied} of {len(manifest)} files have a page
({from_s3} fetched from the mirror); {missing} do not.

Arms in this case:
""")
        for a, st in sorted(arms.items()):
            fh.write(f"* `{a}` - {st}\n")
    SUM.append((case, len(gold), len(runs), len(manifest), copied, from_s3, missing))

print(f"{'case':58} {'gold':>5} {'run':>5} {'union':>6} {'copied':>7} {'s3':>5} {'missing':>8}")
for r in SUM:
    print(f"{r[0]:58} {r[1]:5} {r[2]:5} {r[3]:6} {r[4]:7} {r[5]:5} {r[6]:8}")
