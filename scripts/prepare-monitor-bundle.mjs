// Local-only GUI handoff. No API call, credentials, deployment or git mutation.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const source = await readFile(new URL("../infra/monitor/worker.mjs", import.meta.url), "utf8");
const config = await readFile(new URL("../infra/monitor/wrangler.toml", import.meta.url), "utf8");
for (const name of ["MONITORING_ACTIVE", "RECOVERY_ARMED"]) {
  if (!config.includes(`${name} = "false"`)) throw new Error("Bundle must use unarmed setup defaults");
}
const folder = new URL("../verification/monitor-bundle/", import.meta.url);
await mkdir(folder, { recursive: true });
await writeFile(new URL("worker.js", folder), source, "utf8");
await writeFile(new URL("configuration.txt", folder), `# Cloudflare Settings > Variables and Secrets (initial setup)
MONITORING_ACTIVE=false
RECOVERY_ARMED=false
MIN_SAFE_COMMIT=SET_TO_REVIEWED_MAIN_SHA_AFTER_MERGE
# Encrypted secrets: GITHUB_ACTIONS_TOKEN, HEALTHCHECK_PING_URL
# Do not paste secret values into this file.
# Settings > Triggers > Cron Triggers (UTC):
3,8,13,18,23,28,33,38,43,48,53,58 * * * *
`, "utf8");
await writeFile(new URL("README.txt", folder), `GUI handoff only; this command did not deploy or enable anything.
1. Open worker.js in an editor and copy it to Cloudflare's Worker code editor.
2. Use configuration.txt to set unarmed defaults; enter secrets only in Cloudflare encrypted fields.
3. Follow docs/alert-recovery-runbook.md approval units before activation.
4. Keep Healthchecks New (no pings) during setup. Never send a test success ping to start it early.
SHA256(worker.js): ${createHash("sha256").update(source).digest("hex")}
`, "utf8");
console.log(`Prepared unarmed GUI files: ${fileURLToPath(folder)}`);
