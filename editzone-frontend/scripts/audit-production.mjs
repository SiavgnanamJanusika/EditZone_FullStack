import { spawnSync } from "node:child_process";

// This SPA does not enable React Router's RSC framework mode or server actions.
// Keep this exception narrow so every other high/critical advisory still fails CI.
const allowedAdvisories = new Set(["GHSA-qwww-vcr4-c8h2"]);
const audit = spawnSync("npm", ["audit", "--omit=dev", "--json"], {
  encoding: "utf8",
  shell: false,
});

if (!audit.stdout) {
  process.stderr.write(audit.stderr || "npm audit produced no report\n");
  process.exit(1);
}

let report;
try {
  report = JSON.parse(audit.stdout);
} catch {
  process.stderr.write(audit.stdout);
  process.stderr.write(audit.stderr || "Unable to parse npm audit report\n");
  process.exit(1);
}

const vulnerabilities = Object.values(report.vulnerabilities || {});
const blocked = vulnerabilities.filter((vulnerability) => {
  if (!["high", "critical"].includes(vulnerability.severity)) return false;

  const advisories = (vulnerability.via || []).filter(
    (entry) => typeof entry === "object",
  );
  if (advisories.length > 0) {
    return advisories.some(
      (advisory) => !allowedAdvisories.has(advisory.url?.split("/").pop()),
    );
  }

  // react-router-dom is only a transitive reflection of the allowed advisory.
  return !(
    vulnerability.name === "react-router-dom" &&
    (vulnerability.via || []).every((name) => name === "react-router")
  );
});

if (blocked.length > 0) {
  process.stderr.write(`${JSON.stringify(blocked, null, 2)}\n`);
  process.exit(1);
}

if (vulnerabilities.length > 0) {
  console.warn(
    "Allowed GHSA-qwww-vcr4-c8h2: this client is SPA-only and does not use RSC mode or server actions.",
  );
}
console.log("No applicable high or critical production dependency vulnerabilities.");
