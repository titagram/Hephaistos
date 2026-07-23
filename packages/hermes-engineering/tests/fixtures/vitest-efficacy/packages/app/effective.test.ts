import { describe, expect, it } from "vitest";

import { featureValue } from "./src/feature.js";

describe("effective", () => {
  it("requires the changed behavior", () => {
    expect(featureValue()).toBe(2);
  });
});
