import { ViewerAutoPreset } from "molstar/lib/apps/viewer/presets";
import { MAQualityAssessment } from "molstar/lib/extensions/model-archive/quality-assessment/behavior";
import { SbNcbrPartialCharges } from "molstar/lib/extensions/sb-ncbr/partial-charges/behavior";
import { createPluginUI } from "molstar/lib/mol-plugin-ui";
import { renderReact18 } from "molstar/lib/mol-plugin-ui/react18";
import { DefaultPluginUISpec } from "molstar/lib/mol-plugin-ui/spec";
import { PluginConfig } from "molstar/lib/mol-plugin/config";
import { PluginSpec } from "molstar/lib/mol-plugin/spec";
import { Color } from "molstar/lib/mol-util/color";

// Import the base plugin, not the full Viewer extension registry (which includes
// MP4 encoding, geometry export, stories and remote service integrations).
export async function createStructureViewer(target: HTMLElement, theme: "dark" | "light") {
  const defaults = DefaultPluginUISpec();
  const plugin = await createPluginUI({
    target,
    render: renderReact18,
    spec: {
      ...defaults,
      behaviors: [
        ...defaults.behaviors,
        PluginSpec.Behavior(MAQualityAssessment),
        PluginSpec.Behavior(SbNcbrPartialCharges),
      ],
      layout: { initial: { isExpanded: false, showControls: false } },
      components: {
        ...defaults.components,
        controls: { top: "none", bottom: "none", left: "none" },
        remoteState: "none",
      },
      config: [
        [PluginConfig.Viewport.ShowExpand, false],
        [PluginConfig.Viewport.ShowToggleFullscreen, false],
        [PluginConfig.Viewport.ShowSelectionMode, false],
        [PluginConfig.Viewport.ShowAnimation, false],
        [PluginConfig.Download.DefaultPdbProvider, "rcsb"],
        [PluginConfig.Structure.DefaultRepresentationPreset, ViewerAutoPreset.id],
      ],
    },
    onBeforeUIRender: context => {
      context.builders.structure.representation.registerPreset(ViewerAutoPreset);
    },
  });
  plugin.canvas3d?.setProps({ renderer: { backgroundColor: Color(theme === "dark" ? 0x202020 : 0xf8f8f8) } });
  return plugin;
}
