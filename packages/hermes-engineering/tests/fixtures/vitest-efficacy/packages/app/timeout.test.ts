import { describe, expect, it } from "vitest";

describe("timeout", () => {
  it("outlives the probe deadline", async () => {
    await new Promise((resolve) => setTimeout(resolve, 5_000));
    expect(true).toBe(true);
  });
});
