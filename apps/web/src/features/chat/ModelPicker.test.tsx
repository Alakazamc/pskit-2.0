import { expect, it } from "vitest";
import { modelLabel, providerLabel } from "./ModelPicker";

it("uses authoritative labels for complete LiteLLM aliases", () => {
  const option = { id: "team/deployment/model", display_name: "Lab reasoning", provider_label: "Lab gateway", supports_images: false, reasoning_levels: [] };
  expect(modelLabel(option)).toBe("Lab reasoning"); expect(providerLabel(option)).toBe("Lab gateway");
});

it("uses the entire legacy alias and never guesses its provider", () => {
  const option = { id: "team/deployment/model", supports_images: false, reasoning_levels: [] };
  expect(modelLabel(option)).toBe("team/deployment/model"); expect(providerLabel(option)).toBe("");
});
