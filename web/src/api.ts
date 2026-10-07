// Client de l'API KB. Le jeton est saisi une fois (Réglages) et gardé sur l'appareil.
import { lang, t } from "./i18n";

export type Kind =
  | "tweet" | "article" | "youtube" | "video" | "audio" | "pdf"
  | "image" | "document" | "note" | "repo" | "paper";

// "main" = la veille ; "perso" = développement personnel
export type Space = "main" | "perso";

export interface ItemSummary {
  id: string;
  kind: Kind | null;
  status: "pending" | "processing" | "ready" | "error";
  error: string | null;
  title: string | null;
  source_url: string | null;
  author: string | null;
  site_name: string | null;
  published_at: string | null;
  created_at: string;
  summary: string | null;
  tags: string[];
  thumbnail: string | null;
  user_note: string | null;
  pinned: boolean;
  archived: boolean;
  space: Space;
  category: string | null;
  excerpt?: string | null;
  file_mime?: string | null;
  /** Same card in another language, e.g. { en: { title, summary, key_points, use_cases } }: see i18n.localized */
  translations?: Record<string, ItemTranslation>;
  entry_date?: string | null;   // journal notes: the day they belong to
}

export interface JournalEntry {
  id: string;
  kind: Kind | null;
  status: ItemSummary["status"];
  title: string | null;
  text: string | null;          // the user's own words (or the title of a link filed in the journal)
  source_url: string | null;
  created_at: string;
  updated_at: string;
  day: string;
}

export interface ItemTranslation { title?: string | null; summary?: string | null; key_points?: string[]; use_cases?: string[] }

export interface Entity { name: string; type: string }
export interface Action { id: number; text: string; kind: string | null; done: boolean }
export interface Link { id: string; title: string; kind: Kind; source_url: string | null; reason: string; similarity: number }

export interface ItemDetail extends ItemSummary {
  content: string | null;
  key_points: string[];
  entities: Entity[];
  use_cases: string[];
  genre: string | null;
  metadata: Record<string, any>;
  author_url: string | null;
  file_url: string | null;
  file_name: string | null;
  input_url: string | null;
  input_text?: string | null;
  links: Link[];
  actions: Action[];
  language: string | null;
}

export interface SourceCard {
  n: number;
  id: string;
  title: string | null;
  kind: Kind | null;
  author: string | null;
  source_url: string | null;
  kb_url: string;
  published_at: string | null;
  thumbnail_url: string | null;
  summary: string;
  space?: Space;
  category?: string | null;
  translations?: Record<string, ItemTranslation>;
}

export interface ModelOption {
  id: string;
  label: string;
  note: string;
  default: boolean;
}

export interface NotionStatus {
  configured: boolean;
  spaces: Space[];
  missing?: string[];
  synced?: number;
  pending?: number;
  url?: string | null;
  last_sync_at?: string | null;
  last_error?: string | null;
  last_error_at?: string | null;
  language?: string;           // language of the copy (cards, headings, columns)
  languages?: string[];        // languages the cards exist in
}

export type ChatMode = "ask" | "project" | "advice";

// --- Agent de veille (digests) ---
export type DigestKind = "daily" | "weekly";

export interface DigestEntry {
  key: string;
  section: string;
  title: string;
  summary: string;
  why: string;
  url: string;
  source: string;
  author: string | null;
  person: string | null;
  kind: string;
  published_at: string | null;
  in_kb: boolean;
  hidden?: boolean;
  links?: { discussion?: string; arxiv?: string };
}

export interface DigestProject {
  key: string;
  title: string;
  pitch: string;
  why_now: string;
  refs: string[];
  learn: string;
  plan: string[];
  deliverable: string;
  effort: string;
  difficulty: 1 | 2 | 3;
  kind: string;
}

export interface Digest {
  id: number;
  kind: DigestKind;
  title: string;
  period_start: string;
  period_end: string;
  status: "generating" | "ready" | "error";
  headline: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  content?: string | null;
  data: { entries?: DigestEntry[]; trends?: { text: string; refs: string[] }[]; projects?: DigestProject[];
          projects_pending?: boolean };
  feedback: { target: "entry" | "project"; entry_key: string; vote: number; item_id: string | null }[];
}

export interface DigestSummary {
  id: number;
  kind: DigestKind;
  title: string;
  period_start: string;
  status: Digest["status"];
  headline: string | null;
  n_entries: number;
  n_projects: number;
}

export interface Watch {
  id: number;
  kind: "person" | "feed";
  name: string;
  x_handle: string | null;
  url: string | null;
  feed_url: string | null;
  origin: "manual" | "auto" | "default" | "suggested" | "x_follow";
  status: "active" | "suggested" | "muted";
  note: string | null;
  last_ok_at: string | null;
  last_error: string | null;
}

export interface Interests {
  profile: { summary?: string; topics?: { name: string; weight: number }[]; avoid?: string[]; level?: string;
             computed_at?: string };
  text: string;
  people: Watch[];
  feeds: Watch[];
  schedule: { enabled: boolean; hour: number; timezone: string; email: boolean; x: boolean };
  x_follow: XFollow;
}

/** The user's X account, whose follows join the digest's people (backend/app/digest/following.py). */
export interface XFollow {
  configured: boolean;
  available: boolean;          // X_BEARER_TOKEN is set
  username: string | null;
  following_count: number | null;
  import_cost: number;         // USD, to import every account followed so far
  imported_at: string | null;
  last_sync_at: string | null;
  last_added: string[];
  added_total: number;
  last_error: string | null;
}

export interface TodoAction extends Action {
  item_id: string;
  item_title: string | null;
  item_kind: Kind | null;
  source_url: string | null;
  created_at: string;
}

const TOKEN_KEY = "kb_token";
const BASE_KEY = "kb_api_base";

function read(key: string): string {
  try { return localStorage.getItem(key) ?? ""; } catch { return ""; }
}
function write(key: string, value: string) {
  try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); } catch { /* stockage indisponible */ }
}

export const auth = {
  get token() { return read(TOKEN_KEY); },
  set token(v: string) { write(TOKEN_KEY, v.trim()); },
  get base() { return read(BASE_KEY) || (import.meta.env.VITE_API_URL as string | undefined) || ""; },
  set base(v: string) { write(BASE_KEY, v.trim().replace(/\/$/, "")); },
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${auth.token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`${auth.base}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* corps non JSON */ }
    if (res.status === 401) window.dispatchEvent(new Event("kb:unauthorized"));
    throw new ApiError(res.status, t(String(detail)));
  }
  return res.json() as Promise<T>;
}

export type IngestResult = { ok: boolean; message: string; id: string; items: { id: string; duplicate: boolean }[] };

/** Files go through XMLHttpRequest, the only way to follow the upload: a 25 MB PDF can take a minute. */
function upload(form: FormData, onProgress: (fraction: number) => void): Promise<IngestResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${auth.base}/api/ingest`);
    xhr.setRequestHeader("Authorization", `Bearer ${auth.token}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let body: { detail?: string } & Partial<IngestResult> = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* corps non JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as IngestResult);
      if (xhr.status === 401) window.dispatchEvent(new Event("kb:unauthorized"));
      reject(new ApiError(xhr.status, t(body.detail ?? (xhr.statusText || `Erreur ${xhr.status}`))));
    };
    xhr.onerror = () => reject(new ApiError(0, t("Envoi interrompu : vérifie ta connexion et garde l'app ouverte pendant l'envoi.")));
    xhr.ontimeout = xhr.onerror;
    xhr.send(form);
  });
}

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== undefined && v !== null && v !== "" && p.set(k, String(v)));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export interface ItemPatch {
  title: string;
  user_note: string;
  tags: string[];
  pinned: boolean;
  archived: boolean;
  space: Space;
  category: string | null;
  content: string;
  entry_date: string;
}

export const api = {
  health: () => request<{ ok: boolean }>("/api/health"),
  items: (params: { q?: string; kind?: string; tag?: string; entity?: string; space?: Space; category?: string;
                    limit?: number; offset?: number; archived?: boolean }) =>
    request<{ items: ItemSummary[]; total: number; search: boolean }>(`/api/items${qs(params)}`),
  item: (id: string) => request<ItemDetail>(`/api/items/${id}`),
  patch: (id: string, body: Partial<ItemPatch>) =>
    request<{ ok: boolean; requeued?: boolean }>(`/api/items/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  createNote: (body: { content: string; title?: string; space: Space; category?: string | null; tags?: string[];
                        entry_date?: string }) =>
    request<{ ok: boolean; id: string }>("/api/notes", { method: "POST", body: JSON.stringify(body) }),
  remove: (id: string) => request<{ ok: boolean }>(`/api/items/${id}`, { method: "DELETE" }),
  journalMonth: (month: string) =>
    request<{ month: string; today: string; days: Record<string, number> }>(`/api/journal${qs({ month })}`),
  journalDay: (day: string) => request<{ date: string; entries: JournalEntry[] }>(`/api/journal/${day}`),
  reprocess: (id: string) => request<{ ok: boolean }>(`/api/items/${id}/reprocess`, { method: "POST" }),
  ingest: (body: { url?: string; text?: string; note?: string; space?: Space; category?: string }) =>
    request<IngestResult>("/api/ingest", { method: "POST", body: JSON.stringify(body) }),
  uploadFiles: upload,
  tags: (space?: Space) => request<{ tag: string; count: number }[]>(`/api/tags${qs({ space })}`),
  entities: (space?: Space) =>
    request<{ name: string; type: string; count: number }[]>(`/api/entities${qs({ limit: 60, space })}`),
  categories: () => request<{ id: string; label: string; plural: string; count: number }[]>("/api/categories"),
  actions: (includeDone = false) => request<TodoAction[]>(`/api/actions${qs({ include_done: includeDone })}`),
  setAction: (id: number, done: boolean) =>
    request<{ ok: boolean }>(`/api/actions/${id}`, { method: "PATCH", body: JSON.stringify({ done }) }),
  resurface: (space?: Space) => request<ItemSummary[]>(`/api/resurface${qs({ count: 4, space })}`),
  models: () => request<ModelOption[]>("/api/models"),
  stats: () => request<{ total: number; this_week: number; open_actions: number; by_kind: { kind: Kind; n: number }[];
                         by_status: Record<string, number>; by_space: Partial<Record<Space, number>> }>("/api/stats"),
  notion: () => request<NotionStatus>("/api/notion"),
  digests: (kind?: DigestKind) => request<DigestSummary[]>(`/api/digests${qs({ kind, limit: 40 })}`),
  digest: (id: number) => request<Digest>(`/api/digests/${id}`),
  latestDigest: (kind: DigestKind) => request<Digest | null>(`/api/digests/latest${qs({ kind })}`),
  generateDigest: (kind: DigestKind) =>
    request<{ ok: boolean; id: number }>("/api/digests/generate", { method: "POST", body: JSON.stringify({ kind }) }),
  moreProjects: (id: number) => request<{ ok: boolean }>(`/api/digests/${id}/projects`, { method: "POST" }),
  regenerateDigest: (id: number) => request<{ ok: boolean; id: number }>(`/api/digests/${id}/regenerate`, { method: "POST" }),
  digestFeedback: (id: number, body: { target: "entry" | "project"; key: string; vote: number }) =>
    request<{ ok: boolean; item_id?: string | null }>(`/api/digests/${id}/feedback`, { method: "POST", body: JSON.stringify(body) }),
  interests: () => request<Interests>("/api/interests"),
  setInterests: (text: string) => request<{ ok: boolean }>("/api/interests", { method: "PUT", body: JSON.stringify({ text }) }),
  refreshInterests: () => request<Interests["profile"]>("/api/interests/refresh", { method: "POST" }),
  addWatch: (body: { name?: string; x_handle?: string; url?: string; note?: string }) =>
    request<Watch>("/api/watch", { method: "POST", body: JSON.stringify(body) }),
  patchWatch: (id: number, body: { status?: Watch["status"]; name?: string }) =>
    request<Watch>(`/api/watch/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  removeWatch: (id: number) => request<{ ok: boolean }>(`/api/watch/${id}`, { method: "DELETE" }),
  linkX: (username: string) => request<XFollow>("/api/x-follow", { method: "PUT", body: JSON.stringify({ username }) }),
  syncX: () => request<XFollow>("/api/x-follow/sync", { method: "POST" }),
  importX: () => request<XFollow>("/api/x-follow/import", { method: "POST" }),
  notionSync: () => request<{ ok: boolean }>("/api/notion/sync", { method: "POST" }),
  notionLanguage: (language: string) =>
    request<NotionStatus>("/api/notion/language", { method: "PUT", body: JSON.stringify({ language }) }),
  exportZip: async (files = false, language: string = lang) => {
    const res = await fetch(`${auth.base}/api/export${qs({ files: files || undefined, lang: language })}`,
                            { headers: { Authorization: `Bearer ${auth.token}` } });
    if (!res.ok) throw new ApiError(res.status, t("Export impossible"));
    return res.blob();
  },
};

export type ChatEvent =
  | { type: "status"; text: string }
  | { type: "plan"; summary: string; queries: string[] }
  | { type: "sources"; sources: SourceCard[]; query?: string }
  | { type: "delta"; text: string }
  | { type: "error"; text: string }
  | { type: "done" };

export async function streamChat(
  body: { messages: { role: "user" | "assistant"; content: string }[]; mode: ChatMode; kinds?: string[]; model?: string },
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
) {
  const res = await fetch(`${auth.base}/api/chat`, {
    method: "POST",
    headers: { Authorization: `Bearer ${auth.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, lang }),   // Claude answers in the interface language
    signal,
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, t("Le chat ne répond pas"));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }
}
