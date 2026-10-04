import { Search } from "lucide-react";

export function CatalogSearch({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) {
  return <label className="catalog-search"><Search size={17} aria-hidden="true" />
    <input type="search" aria-label={label} placeholder={label} value={value} onChange={(event) => onChange(event.target.value)} />
  </label>;
}
