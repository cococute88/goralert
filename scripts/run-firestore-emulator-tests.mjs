import { spawnSync } from "node:child_process";

const python = process.platform === "win32" ? "python" : "python3";
const commands = [
  [python, ["-m", "pytest", "alert_engine/tests/test_firestore_emulator.py", "-q"]],
  [process.execPath, ["--test", "tests/firestore-emulator-rules.test.mjs"]],
];

for (const [command, args] of commands) {
  const result = spawnSync(command, args, { stdio: "inherit", env: process.env });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
