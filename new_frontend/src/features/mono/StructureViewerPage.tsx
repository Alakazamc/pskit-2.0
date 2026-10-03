import { Atom, RotateCcw, Upload } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { useLanguage } from "../../i18n/LanguageProvider";
import type { StructureSource } from "./MolstarCanvas";

const MolstarCanvas = lazy(() => import("./MolstarCanvas"));

function fileFormat(name: string): "pdb" | "mmcif" | null {
  if (/\.(pdb|ent)$/i.test(name)) return "pdb";
  if (/\.(cif|mmcif)$/i.test(name)) return "mmcif";
  return null;
}

export function StructureViewerPage({ theme = "dark" }: { theme?: "dark" | "light" }) {
  const { t } = useLanguage();
  const [params, setParams] = useSearchParams();
  const queryId = params.get("pdb") ?? "";
  const [input, setInput] = useState(queryId);
  const [fileSource, setFileSource] = useState<StructureSource | null>(null);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => setInput(queryId), [queryId]);
  const pdbId = /^[A-Za-z0-9]{4}$/.test(queryId) ? queryId.toUpperCase() : null;
  const pdbSource = useMemo<StructureSource | null>(
    () => pdbId ? { type: "pdb", id: pdbId } : null, [pdbId],
  );
  const source = fileSource ?? pdbSource;

  const openId = (event: FormEvent) => {
    event.preventDefault();
    const id = input.trim().toUpperCase();
    if (!/^[A-Z0-9]{4}$/.test(id)) {
      setError(t("tools.viewerInvalidId"));
      return;
    }
    setError("");
    setFileSource(null);
    setParams({ pdb: id });
  };

  const openFile = (file?: File) => {
    if (!file) return;
    const format = fileFormat(file.name);
    if (!format) {
      setError(t("tools.viewerInvalidFile"));
      return;
    }
    setError("");
    setFileSource({ type: "file", file, format });
    setParams({});
  };

  const clear = () => {
    setError("");
    setFileSource(null);
    setInput("");
    setParams({});
  };

  return <div className="mono-structure-page">
    <div className="mono-structure-toolbar">
      <form className="mono-structure-id-form" onSubmit={openId}>
        <label htmlFor="structure-pdb-id">{t("tools.viewerPdbId")}</label>
        <input id="structure-pdb-id" value={input} onChange={(event) => setInput(event.target.value)} placeholder="1A9N" maxLength={12} autoComplete="off" spellCheck={false} />
        <button className="mono-button primary" type="submit">{t("tools.viewerOpen")}</button>
      </form>
      <div className="mono-structure-toolbar-actions">
        <input ref={fileInput} className="mono-structure-file-input" type="file" accept=".pdb,.ent,.cif,.mmcif" aria-label={t("tools.viewerChooseFile")} tabIndex={-1} onChange={(event) => { openFile(event.target.files?.[0]); event.target.value = ""; }} />
        <button className="mono-button" type="button" onClick={() => fileInput.current?.click()}><Upload size={15} />{t("tools.viewerChooseFile")}</button>
        {source && <button className="mono-button mono-structure-clear" type="button" onClick={clear}><RotateCcw size={15} />{t("tools.viewerClear")}</button>}
      </div>
    </div>
    {error && <p className="mono-structure-error" role="alert">{error}</p>}
    <div className="mono-structure-stage" role="region" aria-label={t("tools.viewerTitle")}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => { event.preventDefault(); openFile(event.dataTransfer.files[0]); }}>
      {source ? <Suspense fallback={<div className="mono-structure-status" role="status">{t("tools.viewerLoading")}</div>}><MolstarCanvas source={source} theme={theme} /></Suspense>
        : <div className="mono-structure-empty"><Atom size={30} strokeWidth={1.5} /><p>{t("tools.viewerEmpty")}</p></div>}
    </div>
  </div>;
}
