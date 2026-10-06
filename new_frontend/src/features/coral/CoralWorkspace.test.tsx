import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

it("keeps CORAL out of the legacy tool page implementation", () => {
  const source = readFileSync(resolve("src/features/mono/ToolPages.tsx"), "utf-8");

  expect(source).not.toContain('../coral/');
  expect(source).not.toContain("coralCapabilityId");
  expect(source).not.toContain("showCoral");
  expect(source).not.toContain('selectedName === "coral.generate_rna"');
});

it("routes the coral slug through the generic published product path", () => {
  const source = readFileSync(resolve("src/features/mono/MonoWorkspace.tsx"), "utf-8");

  expect(source).not.toContain('pathname === "/tools/coral" ? "coral.generate_rna"');
  expect(source).not.toContain('["pdb", "structure", "coral", "runs"]');
});
