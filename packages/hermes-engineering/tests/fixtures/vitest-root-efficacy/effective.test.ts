import { expect, it } from "vitest";

import { featureValue } from "./feature.js";

it("requires the changed root-package behavior", () => {
  expect(featureValue()).toBe(2);
});
