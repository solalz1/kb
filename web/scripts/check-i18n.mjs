// Every t("…") key used in src/ must have an English translation in src/i18n/en/*.ts.
// Run: node scripts/check-i18n.mjs   (part of `npm run build`)
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const SRC = new URL("../src/", import.meta.url).pathname;

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "i18n" ? [] : files(path);
    if (name === "i18n.ts") return [];
    return /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

// t("a") or t("a" + "b") or t(\n "a"\n + "b", {...}) and tServer is skipped (server text may be anything)
const CALL = /\bt\(\s*((?:"(?:[^"\\]|\\.)*"\s*(?:\+\s*)?)+)\s*[,)]/g;
const used = new Map();
for (const file of files(SRC)) {
  const code = readFileSync(file, "utf8");
  for (const m of code.matchAll(CALL)) {
    const key = [...m[1].matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((p) => JSON.parse(`"${p[1]}"`)).join("");
    if (!used.has(key)) used.set(key, file.replace(SRC, "src/"));
  }
}

const en = {};
const dir = join(SRC, "i18n", "en");
for (const name of readdirSync(dir).filter((n) => n.endsWith(".ts"))) {
  Object.assign(en, (await import(pathToFileURL(join(dir, name)).href)).default);
}

const missing = [...used].filter(([key]) => !(key in en));
const unused = Object.keys(en).filter((k) => !used.has(k));
if (unused.length) console.log(`i18n: ${unused.length} English strings not used by the code (fine for server messages)`);
if (missing.length) {
  console.error(`i18n: ${missing.length} strings without an English translation:`);
  for (const [key, file] of missing) console.error(`  ${file}: ${JSON.stringify(key)}`);
  process.exit(1);
}
console.log(`i18n: ${used.size} strings, all translated`);
