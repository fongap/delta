const fs = require("fs");
const path = require("path");

function walk(dir, acc = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (!["node_modules", "dist", ".vite"].includes(e.name)) walk(p, acc);
    } else if (/\.(tsx|ts)$/.test(e.name) && !p.includes("i18n")) acc.push(p);
  }
  return acc;
}

const files = walk(".");
const zh = fs.readFileSync("i18n/zh.ts", "utf8");
const zhKeys = new Set([...zh.matchAll(/"([\w.]+)":/g)].map((m) => m[1]));

const used = new Map();
for (const f of files) {
  const s = fs.readFileSync(f, "utf8");
  const re = /(?:^|[^\w])t\(\s*"([\w.]+)"/g;
  let m;
  while ((m = re.exec(s))) {
    const k = m[1];
    if (!used.has(k)) used.set(k, []);
    used.get(k).push(f.replace(/\\/g, "/").replace("./", ""));
  }
}

const missing = [...used.keys()].filter((k) => !zhKeys.has(k)).sort();
const en = fs.readFileSync("i18n/en.ts", "utf8");
const enKeys = new Set([...en.matchAll(/"([\w.]+)":/g)].map((m) => m[1]));
const missingEn = [...used.keys()].filter((k) => !enKeys.has(k)).sort();
console.log("USED KEYS NOT IN en.ts (also absent from en):");
for (const k of missingEn) console.log(`  ${k}  <-  ${used.get(k).join(", ")}`);
console.log("");
console.log("USED KEYS NOT IN zh.ts (fall back to English):");
for (const k of missing) console.log(`  ${k}  <-  ${used.get(k).join(", ")}`);

