import assert from "node:assert/strict";
import { log } from "node:console";
import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
import { resolve } from "node:path";
import process from "node:process";
import { gzipSync } from "node:zlib";

const dist = resolve(process.argv[2] ?? fileURLToPath(new URL("../dist", import.meta.url)));
const manifest = JSON.parse(readFileSync(resolve(dist, ".vite/manifest.json"), "utf8"));
const entries = Object.keys(manifest).filter(key => manifest[key].isEntry);
const viewer = Object.keys(manifest).find(key => manifest[key].name === "MolstarCanvas");
assert(entries.length, "The build manifest must contain an application entry");
assert(viewer, "The structure viewer must remain a separate lazy entry");
assert(Object.values(manifest).some(chunk => chunk.dynamicImports?.includes(viewer)),
  "The structure viewer must load through a dynamic import");

function importsOf(keys) {
  const visited = new Set();
  function visit(key) {
    if (visited.has(key)) return;
    assert(manifest[key], `Missing manifest import: ${key}`);
    visited.add(key);
    for (const dependency of manifest[key].imports ?? []) visit(dependency);
  }
  for (const key of keys) visit(key);
  return visited;
}

const initial = importsOf(entries);
const chunks = Object.keys(manifest).filter(key =>
  key === viewer || manifest[key].name?.startsWith("molstar-"),
);
let largest = 0;
for (const key of chunks) {
  const { file } = manifest[key];
  assert(!initial.has(key), `Molstar must not load with the initial application: ${file}`);
  const size = readFileSync(resolve(dist, file)).length;
  assert(size <= 500_000, `Molstar chunk ${file} is ${size} bytes; limit is 500,000`);
  largest = Math.max(largest, size);
}

const lazyFiles = [...importsOf([viewer])].filter(key => !initial.has(key));
const gzipBytes = lazyFiles.reduce((total, key) =>
  total + gzipSync(readFileSync(resolve(dist, manifest[key].file))).length, 0,
);
assert(gzipBytes <= 1_048_576, `Structure viewer lazy JS is ${gzipBytes} gzip bytes; limit is 1 MiB`);
log(`Molstar bundle verified: ${chunks.length} chunks, largest ${largest} bytes, lazy JS ${gzipBytes} gzip bytes, no initial imports.`);
