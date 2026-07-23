import { describe, expect, it } from "vitest";

import { fixtureAnswer } from "./__fixtures__/answer.js";

describe("inert", () => {
  it("does not exercise the production change", () => {
    expect(fixtureAnswer).toBe(42);
  });
});
