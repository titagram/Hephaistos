import { describe, expect, it } from "vitest";

import { answer } from "../feature.js";

describe("effective test", () => {
  it("guards the production change", () => {
    expect(answer()).toBe(2);
  });
});
