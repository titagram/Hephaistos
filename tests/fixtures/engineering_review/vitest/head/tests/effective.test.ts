import { describe, expect, it } from "vitest";

import { answer } from "../feature.js";

describe("effective test", () => {
  it("guards the production change", () => {
    expect(process.env.OPENAI_API_KEY).toBeUndefined();
    expect(process.env.REVIEW_E2E_SENTINEL).toBeUndefined();
    expect(answer()).toBe(2);
  });
});
