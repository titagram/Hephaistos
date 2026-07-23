import { writeFileSync } from "node:fs";

setTimeout(() => writeFileSync("timeout-survived", "bad"), 600);
