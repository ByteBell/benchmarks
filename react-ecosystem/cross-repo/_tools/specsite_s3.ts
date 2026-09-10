#!/usr/bin/env bun
/**
 * Fetch spec-site pages for PINNED commits out of the S3 mirror into a local cache.
 *
 * `build_specsite.py` drives this: it sends the repos + the file paths it needs, and gets back, per
 * repo, the sha of every path that has a page plus the scan-manifest `kind` of every path that does
 * not. The kind is what makes a miss explainable — a file with no page is either absent from the
 * scan, `big` (analysed in chunks, but `loadFiles` in @bytebell/spec-site emits pages for `small`
 * entries only), or `oversized` (dropped at the walker before analysis).
 *
 * The branchId segment of the key is DISCOVERED, never assumed: repos on `main` and on `master`
 * hash to different ids, so we list the knowledge's children and pick the one that actually holds
 * the pinned commit.
 *
 * Reads a request JSON on stdin, writes a report JSON on stdout. Needs the AWS_* creds and
 * SPEC_SITE_S3_BUCKET from kube-package/.env — invoke it with `bun --env-file=<that>/.env`.
 *
 * The bucket is S3 Express One Zone (`--x-s3`), which needs the AWS SDK's CreateSession handling;
 * Bun's built-in S3 client cannot talk to it. Rather than vendor a node_modules tree into this
 * Python toolbox, we import the SDK the ingestion engine already has on disk.
 */
const SDK_PATH =
  process.env.SPECSITE_SDK_PATH ??
  "/Users/sauravverma/programs/kube-package/services/ingestion-engine/repo/node_modules/@aws-sdk/client-s3";

const { S3Client, GetObjectCommand, HeadObjectCommand, ListObjectsV2Command } = await import(SDK_PATH);

import { mkdir, writeFile, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

interface RepoRequest {
  /** Benchmark repo name, e.g. "redux-toolkit". Echoed back as the report key. */
  repo: string;
  knowledgeId: string;
  /** The roster pin. Never substituted. */
  commit: string;
  /** Repo-relative paths the caller needs a page for. */
  paths: string[];
}
interface Request {
  org: string;
  cacheDir: string;
  repos: RepoRequest[];
}

const req: Request = JSON.parse(await new Response(Bun.stdin.stream()).text());
const BUCKET = (process.env.SPEC_SITE_S3_BUCKET ?? process.env.S3_FILES_BUCKET ?? "").replace(/^s3:\/\//, "");
if (BUCKET.length === 0) {
  console.error("specsite_s3: SPEC_SITE_S3_BUCKET is not set — load kube-package/.env");
  process.exit(2);
}
const s3 = new S3Client({ region: process.env.AWS_REGION ?? process.env.AWS_DEFAULT_REGION ?? "us-east-1" });

async function getText(key: string): Promise<string | null> {
  try {
    const r = await s3.send(new GetObjectCommand({ Bucket: BUCKET, Key: key }));
    return await r.Body.transformToString();
  } catch {
    return null;
  }
}

async function exists(key: string): Promise<boolean> {
  try {
    await s3.send(new HeadObjectCommand({ Bucket: BUCKET, Key: key }));
    return true;
  } catch {
    return false;
  }
}

/** The branchId dirs under one knowledge. A repo can have more than one (a re-register, a rename). */
async function branchIds(org: string, kid: string): Promise<string[]> {
  const out: string[] = [];
  let token: string | undefined;
  do {
    const r = await s3.send(
      new ListObjectsV2Command({ Bucket: BUCKET, Prefix: `${org}/${kid}/`, Delimiter: "/", ContinuationToken: token }),
    );
    for (const p of r.CommonPrefixes ?? []) {
      const seg = (p.Prefix ?? "").slice(`${org}/${kid}/`.length).replace(/\/$/, "");
      if (seg.length > 0) out.push(seg);
    }
    token = r.IsTruncated ? r.NextContinuationToken : undefined;
  } while (token !== undefined);
  return out;
}

/** Resolve the branchId whose tree actually carries this commit's spec-site. */
async function branchFor(org: string, kid: string, commit: string): Promise<string | null> {
  for (const br of await branchIds(org, kid)) {
    if (await exists(`${org}/${kid}/${br}/${commit}/meta/spec-site/page-map.json`)) return br;
  }
  return null;
}

/** Read a cached JSON artifact, falling back to S3 and caching what it finds. */
async function cachedJson<T>(cacheFile: string, key: string): Promise<T | null> {
  if (existsSync(cacheFile)) {
    try {
      return JSON.parse(await readFile(cacheFile, "utf8")) as T;
    } catch {
      /* corrupt cache entry — refetch below */
    }
  }
  const txt = await getText(key);
  if (txt === null) return null;
  await mkdir(path.dirname(cacheFile), { recursive: true });
  await writeFile(cacheFile, txt, "utf8");
  return JSON.parse(txt) as T;
}

async function pool<T>(items: T[], limit: number, fn: (item: T) => Promise<void>): Promise<void> {
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, async () => {
      for (let i = next++; i < items.length; i = next++) await fn(items[i] as T);
    }),
  );
}

interface RepoReport {
  branchId: string | null;
  /** path -> sha, for the requested paths that have a page at the pinned commit. */
  resolved: Record<string, string>;
  /** path -> scan-manifest kind ("small" | "big" | "oversized"), for requested paths with no page. */
  kinds: Record<string, string>;
  /** Total pages in the commit's spec-site, for sanity-checking against the manifest. */
  pageCount: number;
  error: string | null;
}

const report: Record<string, RepoReport> = {};

for (const r of req.repos) {
  const out: RepoReport = { branchId: null, resolved: {}, kinds: {}, pageCount: 0, error: null };
  report[r.repo] = out;

  const br = await branchFor(req.org, r.knowledgeId, r.commit);
  if (br === null) {
    out.error = `no spec-site in S3 for ${r.repo} at the pinned commit ${r.commit.slice(0, 12)}`;
    continue;
  }
  out.branchId = br;

  const base = `${req.org}/${r.knowledgeId}/${br}/${r.commit}/meta`;
  const cache = path.join(req.cacheDir, r.knowledgeId, r.commit);

  const pageMap = await cachedJson<{ files?: Record<string, string> }>(path.join(cache, "page-map.json"), `${base}/spec-site/page-map.json`);
  if (pageMap === null) {
    out.error = `page-map.json unreadable for ${r.repo}@${r.commit.slice(0, 12)}`;
    continue;
  }
  const byPath = new Map<string, string>();
  for (const [sha, p] of Object.entries(pageMap.files ?? {})) byPath.set(p, sha);
  out.pageCount = byPath.size;

  // Only consulted to explain misses, so a missing manifest is not fatal — but the two cases must
  // stay distinguishable. Some commits mirrored `meta/spec-site/` and nothing else, and calling
  // every miss there "absent" would assert the file was never scanned when we simply cannot see.
  const manifest = await cachedJson<{ entries?: { relativePath: string; kind?: string }[] }>(
    path.join(cache, "scan-manifest.json"),
    `${base}/scan-manifest.json`,
  );
  const haveManifest = manifest !== null;
  const kindOf = new Map<string, string>();
  for (const e of manifest?.entries ?? []) kindOf.set(e.relativePath, e.kind ?? "small");

  const wanted: { p: string; sha: string }[] = [];
  for (const p of r.paths) {
    const sha = byPath.get(p);
    if (sha === undefined) {
      out.kinds[p] = kindOf.get(p) ?? (haveManifest ? "absent" : "unknown");
      continue;
    }
    out.resolved[p] = sha;
    if (!existsSync(path.join(cache, "files", `${sha}.json`))) wanted.push({ p, sha });
  }

  await mkdir(path.join(cache, "files"), { recursive: true });
  const failed: string[] = [];
  await pool(wanted, 16, async ({ p, sha }) => {
    const txt = await getText(`${base}/spec-site/files/${sha}.json`);
    if (txt === null) {
      failed.push(p);
      return;
    }
    await writeFile(path.join(cache, "files", `${sha}.json`), txt, "utf8");
  });
  for (const p of failed) {
    delete out.resolved[p];
    out.kinds[p] = "page-listed-but-object-missing";
  }
}

console.log(JSON.stringify({ bucket: BUCKET, repos: report }));
