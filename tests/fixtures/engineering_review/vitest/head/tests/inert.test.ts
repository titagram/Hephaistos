import { describe, expect, it } from "vitest";

import { answer } from "../feature.js";

describe("inert sibling", () => {
  it("passes before and after the production revert", () => {
    expect(answer()).toBeGreaterThan(0);
  });
});
