#!/usr/bin/env python3
"""Build a turbovec (TurboQuant) vector index over ONE cal.com checkout at ONE commit.

    python3 build_index_percommit.py <case_dir>            # e.g. cal.com.processed/<sha>

Same recipe as the cross-repo build (build_index.py): every tracked text source file
is cut into overlapping line windows, each window embedded with
sentence-transformers/all-MiniLM-L6-v2 (384-d, normalized -> dot == cosine) and stored
in a turbovec IdMapIndex. Chunk text lives in chunks.jsonl so the MCP server never
touches the checkout at request time.

Differences from the cross-repo build, both deliberate:

  * ONE repo, so chunks carry no `repo` field and the embedded text is prefixed with
    the bare repo-relative path. The matching server exposes tools WITHOUT a `repo`
    argument — which is the shape the original per-commit cal.com arms actually
    called (`read_lines {path, start, end}`), and reproducing it is the point.
  * The commit is read from the case's own case.json (`index_at`) and asserted
    against the checkout's HEAD, so an index can never be built from a tree that has
    drifted off the pin the case is scored at.

Only `git ls-files` output is indexed. The cal.com working trees carry untracked
synthetic distractor files; indexing the working tree instead of the tracked set
would quietly pull those into the arena.

Writes into <case_dir>/_turbovec/:  index.tvim, chunks.jsonl, meta.json,
mcp_turbovector.json (a ready-to-use MCP_CONFIG for run_turbovec_arm.sh).
"""
import json, subprocess, sys, time
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer
from turbovec import IdMapIndex

BENCH = Path("/Users/sauravverma/programs/benchmarks/react-ecosystem")
HERE = Path(__file__).resolve().parent
PYTHON = "/Users/sauravverma/.venvs/turbovec/bin/python"

# Identical to the cross-repo build so the two indexes stay comparable.
EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".md", ".mdx",
        ".prisma", ".sql", ".css", ".scss", ".yml", ".yaml", ".sh", ".env"}
MAX_BYTES = 400_000
WIN, STRIDE = 20, 15
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
BIT_WIDTH = 4


def tracked_files(root: Path):
    out = subprocess.run(["git", "ls-files"], cwd=root,
                         capture_output=True, text=True).stdout
    for rel in out.splitlines():
        p = root / rel
        if p.suffix.lower() not in EXTS:
            continue
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        if sz == 0 or sz > MAX_BYTES:
            continue
        yield rel, p


def chunks(root: Path):
    for rel, p in tracked_files(root):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        if not lines:
            continue
        for s in range(0, len(lines), STRIDE):
            body = lines[s:s + WIN]
            if any(l.strip() for l in body):
                yield {"path": rel, "start": s + 1,
                       "end": min(s + WIN, len(lines)), "text": "\n".join(body)}
            if s + WIN >= len(lines):
                break


def main(case_arg: str):
    case = (BENCH / case_arg).resolve() if not Path(case_arg).is_absolute() else Path(case_arg)
    root = case / "repo"
    if not root.is_dir():
        sys.exit(f"FAIL: no checkout at {root}")

    meta_case = json.loads((case / "case.json").read_text())
    pin = meta_case["index_at"]
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    if head != pin:
        sys.exit(f"FAIL: CHECKOUT DRIFT — {root} at {head}, case pins {pin}")

    out = case / "_turbovec"
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    cs = list(chunks(root))
    nfiles = len({c["path"] for c in cs})
    print(f"{len(cs)} chunks from {nfiles} tracked files ({time.time()-t0:.1f}s)", flush=True)
    if not cs:
        sys.exit("FAIL: no chunks — is the checkout a git repo with tracked files?")

    model = SentenceTransformer(MODEL_NAME)
    print(f"model loaded, max_seq_length={model.max_seq_length}", flush=True)
    texts = [f"{c['path']}\n{c['text']}" for c in cs]
    vecs = model.encode(texts, batch_size=256, normalize_embeddings=True,
                        show_progress_bar=True, convert_to_numpy=True)
    vecs = np.asarray(vecs, dtype=np.float32)
    print(f"embedded {vecs.shape} in {time.time()-t0:.1f}s", flush=True)

    idx = IdMapIndex(dim=vecs.shape[1], bit_width=BIT_WIDTH)
    idx.add_with_ids(vecs, np.arange(len(cs), dtype=np.uint64))
    idx.write(str(out / "index.tvim"))

    with open(out / "chunks.jsonl", "w") as f:
        for i, c in enumerate(cs):
            f.write(json.dumps({"id": i, **c}) + "\n")

    json.dump({"repo": meta_case.get("repo", "calcom/cal.com"), "commit": pin,
               "case_id": meta_case.get("id"), "chunks": len(cs), "files": nfiles,
               "dim": int(vecs.shape[1]), "bit_width": BIT_WIDTH, "model": MODEL_NAME,
               "window": WIN, "stride": STRIDE,
               "build_seconds": round(time.time() - t0, 1)},
              open(out / "meta.json", "w"), indent=2)

    # An MCP_CONFIG pinned to THIS index. Written next to the index so an arm can
    # never be pointed at the cross-repo server by accident — that server indexes
    # fourteen sibling repos and contains no cal.com at all, so it would answer
    # every query with confident, entirely unrelated hits.
    json.dump({"mcpServers": {"turbovector": {
        "type": "stdio", "command": PYTHON,
        "args": [str(HERE / "turbovector_mcp_percommit.py")],
        "env": {"TURBOVEC_INDEX": str(out)}}}},
        open(out / "mcp_turbovector.json", "w"), indent=1)

    print(f"index written to {out} in {time.time()-t0:.1f}s", flush=True)
    print(f"run with:  MCP_CONFIG={out}/mcp_turbovector.json "
          f"./run_turbovec_arm.sh {case_arg} <arm_name>", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
