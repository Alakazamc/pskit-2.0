import { useEffect, useRef, useState } from "react";
import type { PluginUIContext } from "molstar/lib/mol-plugin-ui/context";
import { Asset } from "molstar/lib/mol-util/assets";
import darkThemeUrl from "molstar/build/viewer/theme/dark.css?url";
import lightThemeUrl from "molstar/build/viewer/theme/light.css?url";
import { useLanguage } from "../../i18n/LanguageProvider";
import { createStructureViewer } from "./createStructureViewer";

export type StructureSource =
  | { type: "pdb"; id: string }
  | { type: "file"; file: File; format: "pdb" | "mmcif" };

export default function MolstarCanvas({ source, theme }: {
  source: StructureSource;
  theme: "dark" | "light";
}) {
  const { t } = useLanguage();
  const host = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error" | "webgl-error">("loading");

  useEffect(() => {
    let cancelled = false;
    let viewer: PluginUIContext | null = null;
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = theme === "dark" ? darkThemeUrl : lightThemeUrl;
    document.head.appendChild(stylesheet);
    setStatus("loading");
    const load = async () => {
      await new Promise<void>((resolve, reject) => {
        stylesheet.onload = () => resolve();
        stylesheet.onerror = () => reject(new Error("Mol* stylesheet could not load"));
      });
      const content = source.type === "file" ? await source.file.text() : null;
      if (cancelled || !host.current) return;
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
      if (!context) {
        setStatus("webgl-error");
        return;
      }
      context.getExtension("WEBGL_lose_context")?.loseContext();
      viewer = await createStructureViewer(host.current, theme);
      if (cancelled) {
        viewer.dispose();
        return;
      }
      const data = source.type === "pdb"
        ? await viewer.builders.data.download({
          url: Asset.Url(`https://models.rcsb.org/${source.id.toUpperCase()}.bcif`),
          isBinary: true,
          label: `RCSB PDB: ${source.id.toUpperCase()} (bcif)`,
        }, { state: { isGhost: true } })
        : await viewer.builders.data.rawData({ data: content!, label: source.file.name });
      const trajectory = await viewer.builders.structure.parseTrajectory(data, source.type === "pdb" ? "mmcif" : source.format);
      await viewer.builders.structure.hierarchy.applyPreset(trajectory, "default");
      if (!cancelled) setStatus("ready");
    };
    void load().catch(() => { if (!cancelled) setStatus("error"); });
    return () => { cancelled = true; viewer?.dispose(); stylesheet.remove(); };
  }, [source, theme]);

  return <div className="mono-structure-viewer">
    <div ref={host} className="mono-structure-viewer-host" />
    {status === "loading" && <div className="mono-structure-status" role="status">{t("tools.viewerLoading")}</div>}
    {status === "error" && <div className="mono-structure-status error" role="alert">{t("tools.viewerLoadFailed")}</div>}
    {status === "webgl-error" && <div className="mono-structure-status error" role="alert">{t("tools.viewerWebglUnavailable")}</div>}
  </div>;
}
