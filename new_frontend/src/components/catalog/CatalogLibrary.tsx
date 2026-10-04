import { Database, Sparkles } from "lucide-react";
import { useState } from "react";
import type { CatalogItem } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { CatalogCard } from "./CatalogCard";
import { CatalogSearch } from "./CatalogSearch";
import { DetailPanel } from "./DetailPanel";

/** Public catalog metadata. Editing requires an owner-authorized write API. */
export function CatalogLibrary({ items, kind, loading, error }: {
  items: CatalogItem[]; kind: "skills" | "resources"; loading?: boolean; error?: boolean;
}) {
  const { t } = useLanguage();
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = items.find((item) => item.id === selectedId);
  const query = search.trim().toLocaleLowerCase();
  const filtered = items.filter((item) => `${item.name} ${item.description ?? ""}`.toLocaleLowerCase().includes(query));
  return <>
    <CatalogSearch value={search} onChange={setSearch} label={t(kind === "skills" ? "catalog.searchSkills" : "catalog.searchResources")} />
    {loading && <p role="status">{t("workspace.loadingCatalog")}</p>}
    {error && <p className="mono-form-error" role="alert">{t("workspace.catalogLoadFailed")}</p>}
    <div className="catalog-entry-grid">{filtered.map((item) => <CatalogCard key={item.id}
      title={item.name} description={item.description} icon={kind === "skills" ? <Sparkles /> : <Database />}
      onOpen={() => setSelectedId(item.id)} />)}</div>
    {!loading && !error && !filtered.length && <p className="mono-muted">{t(query ? "catalog.noMatches" : kind === "skills" ? "workspace.noSkills" : "workspace.noResources")}</p>}
    <DetailPanel open={!!selected} onOpenChange={(open) => { if (!open) setSelectedId(null); }} title={selected?.name ?? ""}>
      {selected && <dl className="catalog-info"><dt>{t("catalog.description")}</dt><dd>{selected.description || "—"}</dd>
        <dt>{t("catalog.identifier")}</dt><dd>{selected.id}</dd>
        {selected.version != null && <><dt>{t("catalog.version")}</dt><dd>{selected.version}</dd></>}
      </dl>}
    </DetailPanel>
  </>;
}
