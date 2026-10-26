import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
const config = readFileSync(resolve(import.meta.dirname, "../../vite.config.ts"), "utf8");
describe("workbench development proxy", () => {
  it("exposes the experiment API and saved reports", () => {
    expect(config).toContain('"/api"');
    expect(config).toContain('"/runs": apiProxy');
  });
  it("does not route retired conversation and model settings services", () => {
    expect(config).not.toContain('"/sessions"');
    expect(config).not.toContain('"/settings/llm"');
    expect(config).not.toContain('"/channels"');
  });
});
