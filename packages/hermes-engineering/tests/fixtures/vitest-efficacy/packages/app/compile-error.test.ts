import { describe, expect, it } from "vitest";

import { headOnlyValue } from "./src/head-only.js";

describe("compile error after revert", () => {
  it("uses a symbol added by the change", () => {
    expect(headOnlyValue).toBe(3);
  });
});
