import assert from "node:assert/strict";
import test from "node:test";
import { refreshedDataBaseUrl, siteDataUrl } from "./siteDataUrl.js";

test("local and GitHub project data paths retain their site prefix", () => {
  assert.equal(siteDataUrl("commanders/index.json"), "/data/latest/commanders/index.json");
  assert.equal(siteDataUrl("/tags/tokens.json", { basePath: "/edhrec-affinity/" }), "/edhrec-affinity/data/latest/tags/tokens.json");
});

test("every logical resource can use the same pinned cross-origin snapshot", () => {
  const dataBaseUrl = "https://abc12345-edhrec-affinity-data.example.workers.dev/data/latest/";
  for (const path of ["site_manifest.json", "build-styles/index.json", "build-styles/commander.json", "tags/tokens.json"]) {
    assert.equal(siteDataUrl(path, { basePath: "/edhrec-affinity/", dataBaseUrl }), `${dataBaseUrl}${path}`);
  }
});

test("open Discovery tabs can follow a newly published snapshot of the same Worker", () => {
  const current = "https://abcdef01-edhrec-affinity-data.example.workers.dev/data/latest";
  const next = "https://12345678-edhrec-affinity-data.example.workers.dev/data/latest";
  assert.equal(refreshedDataBaseUrl(current, next + "/"), next);
  for (const invalid of [undefined, "", "https://unrelated.example/data/latest", next.replace("example", "another-account"),
    next.replace("https:", "http:"), next + "?token=unexpected", next.replace("/data/latest", "/other"),
    next.replace("https://", "https://user:password@")]) {
    assert.equal(refreshedDataBaseUrl(current, invalid), current);
  }
  assert.equal(refreshedDataBaseUrl("", next), "");
});
