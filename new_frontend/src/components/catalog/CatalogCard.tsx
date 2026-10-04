import type { ReactNode } from "react";
import { Link } from "react-router-dom";

type CatalogCardProps = {
  title: string;
  description?: string;
  icon?: ReactNode;
  disabled?: boolean;
} & ({ to: string; onOpen?: never } | { to?: never; onOpen: () => void });

/** A compact directory entry; full information belongs in its detail panel. */
export function CatalogCard({ title, description, icon, disabled = false, ...action }: CatalogCardProps) {
  const children = <><span className="catalog-card-icon" aria-hidden="true">{icon}</span>
    <h2>{title}</h2>{description && <p>{description}</p>}</>;
  const shared = { className: "catalog-entry-card", title: [title, description].filter(Boolean).join("\n"), "aria-label": title };
  return action.to !== undefined
    ? <Link {...shared} to={action.to}>{children}</Link>
    : <button {...shared} type="button" onClick={action.onOpen} disabled={disabled}>{children}</button>;
}
