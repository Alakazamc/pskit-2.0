type MarkdownModule = { default: typeof import("./MarkdownContent")["MarkdownContent"] };

let pending: Promise<MarkdownModule> | undefined;
let loaded: MarkdownModule | undefined;

/** Share the lazy chunk with history preparation without loading it in Tools. */
export function loadMarkdownContent(): Promise<MarkdownModule> {
  pending ??= import("./MarkdownContent")
    .then((module) => { loaded = { default: module.MarkdownContent }; return loaded; })
    .catch((error) => { pending = undefined; throw error; });
  return pending;
}

export function isMarkdownContentReady(): boolean {
  return loaded !== undefined;
}

export function getLoadedMarkdownContent(): MarkdownModule["default"] | undefined {
  return loaded?.default;
}
