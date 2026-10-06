export type AuthMailAction = "signup" | "recovery" | "guest-upgrade";

const PREFIX = "pskit:auth-cooldown:v1";

async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value.trim().toLowerCase());
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function storageKey(action: AuthMailAction, email: string): Promise<string> {
  return `${PREFIX}:${action}:${await sha256(email)}`;
}

export async function readAuthCooldown(
  action: AuthMailAction,
  email: string,
  now = Date.now(),
  storage: Storage = window.sessionStorage,
): Promise<number> {
  if (!email.trim()) return 0;
  try {
    const key = await storageKey(action, email);
    const raw = storage.getItem(key);
    if (!raw) return 0;
    const parsed: unknown = JSON.parse(raw);
    const expiresAt = parsed && typeof parsed === "object" && "expiresAt" in parsed
      ? (parsed as { expiresAt?: unknown }).expiresAt : null;
    if (typeof expiresAt !== "number" || !Number.isFinite(expiresAt) || expiresAt <= now) {
      storage.removeItem(key);
      return 0;
    }
    return Math.max(1, Math.ceil((expiresAt - now) / 1000));
  } catch {
    return 0;
  }
}

export async function writeAuthCooldown(
  action: AuthMailAction,
  email: string,
  seconds: number,
  now = Date.now(),
  storage: Storage = window.sessionStorage,
): Promise<void> {
  if (!email.trim() || !Number.isSafeInteger(seconds) || seconds <= 0) return;
  try {
    const key = await storageKey(action, email);
    storage.setItem(key, JSON.stringify({ expiresAt: now + seconds * 1000 }));
  } catch {
    // Private browsing and restricted Web Crypto/storage must not crash auth UI.
  }
}
