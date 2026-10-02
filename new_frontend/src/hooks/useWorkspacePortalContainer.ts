import { useEffect, useState } from "react";

export function useWorkspacePortalContainer() {
  const [container, setContainer] = useState<HTMLElement>();

  useEffect(() => {
    setContainer(document.querySelector<HTMLElement>(".mono-app") ?? undefined);
  }, []);

  return container;
}
