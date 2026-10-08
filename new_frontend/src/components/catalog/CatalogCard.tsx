import type { ReactNode } from "react";
import { Link } from "react-router-dom";

type CatalogCardProps = {
  title: string;
  description?: string;
  icon?: ReactNode;
  disabled?: boolean;
  meta?: string;
  size?: "compact" | "large";
} & ({ to: string; onOpen?: never } | { to?: never; onOpen: () => void });

/** A compact directory entry; full information belongs in its detail panel. */
export function CatalogCard({ title, description, icon, disabled = false, meta, size = "compact", ...action }: CatalogCardProps) {
  const children = <><span className="catalog-card-icon" aria-hidden="true">{icon}</span>
    <h2>{title}</h2>{description && <p>{description}</p>}{meta && <span className="catalog-card-meta">{meta}</span>}</>;
  const shared = { className: `catalog-entry-card${size === "large" ? " catalog-entry-card-large" : ""}`, title: [title, description].filter(Boolean).join("\n"), "aria-label": title };
  return action.to !== undefined
    ? <Link {...shared} to={action.to}>{children}</Link>
    : <button {...shared} type="button" onClick={action.onOpen} disabled={disabled}>{children}</button>;
}
