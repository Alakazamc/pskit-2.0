import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ChevronUp, LogOut, Settings2, UserRound } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ResearchApi, UserIdentity } from "../../api/types";
import { UserAvatar } from "../../components/UserAvatar";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { useLanguage } from "../../i18n/LanguageProvider";

export function AccountMenu({ api, user, compact = false, onNavigate, onLogout }: {
  api: ResearchApi; user: UserIdentity; compact?: boolean;
  onNavigate: () => void; onLogout: () => void;
}) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const [open, setOpen] = useState(false);
  const navigating = useRef(false);
  const followLink = () => {
    navigating.current = true;
    setOpen(false);
    onNavigate();
  };
  return <DropdownMenu.Root open={open} onOpenChange={setOpen}>
    <DropdownMenu.Trigger asChild><button type="button" className={`account-menu-trigger ${compact ? "compact" : ""}`} aria-label={t("account.open", { name: user.name })}>
      <UserAvatar api={api} user={user} />
      {!compact && <><span className="account-menu-identity"><b>{user.name}</b><small>{t("mono.personalSpace")}</small></span><ChevronUp size={15} aria-hidden="true" /></>}
    </button></DropdownMenu.Trigger>
    <DropdownMenu.Portal container={portalContainer}>
      <DropdownMenu.Content className="account-menu-panel" side="top" align="start" sideOffset={8} collisionPadding={12} aria-label={t("account.menu")} aria-labelledby={undefined} onCloseAutoFocus={(event) => {
        if (navigating.current) { event.preventDefault(); navigating.current = false; }
      }}>
        <DropdownMenu.Label className="account-menu-heading"><UserAvatar api={api} user={user} /><span><b>{user.name}</b><small>{user.email || t("profile.guest")}</small></span></DropdownMenu.Label>
        <DropdownMenu.Separator className="account-menu-separator" />
        <DropdownMenu.Item asChild><Link to="/settings#profile" onClick={followLink}><UserRound size={17} aria-hidden="true" />{t("profile.title")}</Link></DropdownMenu.Item>
        <DropdownMenu.Item asChild><Link to="/settings" onClick={followLink}><Settings2 size={17} aria-hidden="true" />{t("mono.settings")}</Link></DropdownMenu.Item>
        <DropdownMenu.Separator className="account-menu-separator" />
        <DropdownMenu.Item onSelect={onLogout}><LogOut size={17} aria-hidden="true" />{t("mono.signOut")}</DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>;
}
