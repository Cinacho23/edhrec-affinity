/* global process */

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Vite base path:
 *
 * Local development:
 *   /
 *
 * GitHub Pages project site:
 *   /repo-name/
 *
 * Example:
 *   https://YOUR-USERNAME.github.io/edhrec-affinity/
 * needs:
 *   /edhrec-affinity/
 */
function getBasePath() {
  if (process.env.VITE_BASE_PATH) {
    return process.env.VITE_BASE_PATH;
  }

  const repository = process.env.GITHUB_REPOSITORY;
  const isGitHubActions = process.env.GITHUB_ACTIONS === "true";

  if (isGitHubActions && repository) {
    const repoName = repository.split("/")[1];
    return `/${repoName}/`;
  }

  return "/";
}

export default defineConfig({
  plugins: [react()],
  base: getBasePath(),
  build: {
    rollupOptions: {
      checks: {
        pluginTimings: false,
      },
    },
  },
});
