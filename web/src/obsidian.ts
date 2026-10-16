// Links that open the wiki in the Obsidian app (obsidian:// URIs): the vault, one page's note, or Obsidian's search.
// The vault is found by its name, which can differ from one device to the next: kept on this device only.

const KEY = "kb_obsidian_vault";
export const DEFAULT_VAULT = "KB-Wiki";     // the zip's folder, and the clone's in SETUP.md (backend: obsidian.VAULT)

export function vaultName(): string {
  try { return localStorage.getItem(KEY)?.trim() || DEFAULT_VAULT; } catch { return DEFAULT_VAULT; }
}

export function setVaultName(name: string): void {
  try {
    const v = name.trim();
    if (v && v !== DEFAULT_VAULT) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY);
  } catch { /* storage unavailable: the default name */ }
}

const uri = (action: string, params: Record<string, string>) =>
  `obsidian://${action}?` + Object.entries({ vault: vaultName(), ...params })
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");

/** The vault at its index, or a note by its path in the vault (without .md). */
export const obsidianOpen = (file = "index") => uri("open", { file });

/** Obsidian's search, already filled in when there is a query. */
export const obsidianSearch = (query = "") => uri("search", query.trim() ? { query: query.trim() } : {});
