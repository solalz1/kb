// What the pages load, by cache key (cache.ts): the same key and fetcher wherever a list is shown or fetched ahead,
// and the updates made in the cache before the API answers (pin, archive, delete…).
import { api, type ItemDetail, type ItemSummary, type Space } from "./api";
import { cache } from "./cache";

export const PAGE = 30;

/** The filters of a list of items, as the API takes them. */
export interface ItemsQuery {
  q?: string; kind?: string; tag?: string; entity?: string; space?: Space; category?: string; folder?: string; archived?: boolean;
}
export type ItemsPage = Awaited<ReturnType<typeof api.items>>;

const canonical = (q: ItemsQuery) =>
  Object.fromEntries(Object.entries(q).filter(([, v]) => v !== undefined && v !== "" && v !== false).sort(([a], [b]) => a.localeCompare(b)));

export const keys = {
  items: (q: ItemsQuery) => `items?${JSON.stringify(canonical(q))}`,
  item: (id: string) => `item:${id}`,
  archivedCount: (space?: Space) => `archived-count:${space ?? ""}`,
  resurface: (space?: Space) => `resurface:${space ?? ""}`,
  tags: (space?: Space) => `tags:${space ?? ""}`,
  entities: (space?: Space) => `entities:${space ?? ""}`,
  stats: "stats",
  categories: "categories",
  folders: "folders",
  actions: (done: boolean) => `actions:${done}`,
  digests: "digests",
  digest: (id: number) => `digest:${id}`,
  latestDigest: (kind: string) => `digest-latest:${kind}`,
  journalMonth: (month: string) => `journal-month:${month}`,
  journalDay: (day: string) => `journal-day:${day}`,
  interests: "interests",
  thinking: "thinking",
  costs: "costs",
  notion: "notion",
  models: "models",
};

// "À redécouvrir" is drawn at random among old items: it stays put rather than reshuffling after every change, and
// the tags and people only move slowly.
for (const prefix of ["resurface:", "tags:", "entities:", "models"]) cache.steady(prefix);

/** A list's first page — or as many items as it already shows, so a refresh doesn't drop the pages loaded since. */
export function fetchItems(q: ItemsQuery): Promise<ItemsPage> {
  const shown = cache.peek<ItemsPage>(keys.items(q))?.items.length ?? 0;
  return api.items({ ...q, limit: Math.min(100, Math.max(PAGE, shown)), offset: 0 });
}

/** The next page of a list, added to it. */
export async function fetchMore(q: ItemsQuery): Promise<void> {
  const key = keys.items(q);
  const offset = cache.peek<ItemsPage>(key)?.items.length ?? 0;
  const res = await api.items({ ...q, limit: PAGE, offset });
  cache.update<ItemsPage>(key, (p) => {
    const seen = new Set(p.items.map((i) => i.id));
    return { ...p, total: res.total, items: [...p.items, ...res.items.filter((i) => !seen.has(i.id))] };
  });
}

/** What a list already knows of an item: its page opens with it while the rest loads. */
export function summaryOf(id: string): ItemSummary | undefined {
  return cache.find<ItemsPage, ItemSummary>("items?", (p) => p.items.find((i) => i.id === id))
    ?? cache.find<ItemSummary[], ItemSummary>("resurface:", (l) => l.find((i) => i.id === id));
}

/** The same change on the item's page and in every list it's in, before the API has answered. */
export function patchItem(id: string, patch: Partial<ItemDetail>): void {
  cache.update<ItemDetail>(keys.item(id), (it) => ({ ...it, ...patch }));
  cache.updateAll<ItemsPage>("items?", (p) => (p.items.some((i) => i.id === id)
    ? { ...p, items: p.items.map((i) => (i.id === id ? { ...i, ...patch } : i)) } : p));
}

/** Out of every list (archived, deleted). Returns how to put it back where it was in `key`'s list. */
export function dropItem(id: string, key?: string): () => void {
  const list = key ? cache.peek<ItemsPage>(key) : undefined;
  const index = list ? list.items.findIndex((i) => i.id === id) : -1;
  const item = index >= 0 ? list!.items[index] : undefined;
  cache.updateAll<ItemsPage>("items?", (p) => (p.items.some((i) => i.id === id)
    ? { ...p, items: p.items.filter((i) => i.id !== id), total: Math.max(0, p.total - 1) } : p));
  cache.updateAll<ItemSummary[]>("resurface:", (l) => (l.some((i) => i.id === id) ? l.filter((i) => i.id !== id) : l));
  return () => {
    if (!key || !item) return;
    cache.update<ItemsPage>(key, (p) => (p.items.some((i) => i.id === item.id) ? p
      : { ...p, total: p.total + 1, items: [...p.items.slice(0, index), item, ...p.items.slice(index)] }));
  };
}

/** Deleted for good: gone from the lists and its page forgotten. */
export function forgetItem(id: string): void {
  dropItem(id);
  cache.remove(keys.item(id));
}

/** Each tab's first screen, fetched once the app is idle: switching tabs then shows them at once. */
export function prefetchTabs(): void {
  cache.prefetch(keys.items({ space: "main" }), () => fetchItems({ space: "main" }));
  cache.prefetch(keys.items({ space: "perso" }), () => fetchItems({ space: "perso" }));
  cache.prefetch(keys.resurface("main"), () => api.resurface("main"));
  cache.prefetch(keys.resurface("perso"), () => api.resurface("perso"));
  cache.prefetch(keys.latestDigest("daily"), () => api.latestDigest("daily"));
  cache.prefetch(keys.digests, () => api.digests());
  cache.prefetch(keys.folders, api.folders);
  cache.prefetch(keys.categories, api.categories);
  cache.prefetch(keys.models, api.models);
}
