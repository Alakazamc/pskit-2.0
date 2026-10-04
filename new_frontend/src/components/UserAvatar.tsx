import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { ResearchApi, UserIdentity } from "../api/types";

function AvatarImage({ blob }: { blob: Blob }) {
  const [source, setSource] = useState<string>();
  useEffect(() => {
    const url = URL.createObjectURL(blob);
    setSource(url);
    return () => URL.revokeObjectURL(url);
  }, [blob]);
  return source ? <img src={source} alt="" /> : null;
}

export function UserAvatar({ api, user, large = false }: { api: ResearchApi; user: UserIdentity; large?: boolean }) {
  const avatar = useQuery({
    queryKey: ["avatar", user.id, user.avatar_revision], queryFn: api.getAvatar,
    enabled: Boolean(user.avatar_revision), staleTime: Infinity, retry: false,
  });
  return <span className={`user-avatar ${large ? "large" : ""}`} aria-hidden="true">
    {user.avatar_revision && avatar.data ? <AvatarImage blob={avatar.data} /> : user.name.slice(0, 1).toUpperCase()}
  </span>;
}
