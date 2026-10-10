// An in-memory cache of what the API sent, so that going back to a page shows it at once (then refreshes it quietly)
// instead of loading it again. It lives in memory only: reloading the app empties it and everything comes fresh.
//
// Every change sent to the API (api.ts) marks the whole cache as to be checked again: the pages on screen refresh at
// once, the others when they open, still showing what they had meanwhile. A request that started before a change was
// sent may bring back what was there before it, so its answer is dropped when the page already shows something (an
// update made by hand, say): the refresh that follows the change brings the truth.
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

type Entry = { data: unknown; at: number };     // at: when it arrived; 0 = to be checked again

const store = new Map<string, Entry>();         // oldest written first: what goes when the cache is full
const inflight = new Map<string, { promise: Promise<unknown>; epoch: number; id: number }>();
const latest = new Map<string, number>();       // the last request started for each key
const shownKeys = new Map<string, number>();    // keys a page on screen uses (never evicted)
const listeners = new Set<() => void>();
const steady: string[] = [];                    // key prefixes a change doesn't touch (they age out by themselves)
const changes = new Map<number, number>();      // changes on their way to the API, and when each started
let epoch = 0;                                  // moves on whenever a change starts or ends
let requests = 0;

// A long change (a 25 MB upload, sorting every folder) stops holding back refreshes after this long.
const CHANGE_HOLD_MS = 8_000;
// At most this many values, and this many whole items (their full text can be long): the oldest unused go first.
const MAX_ENTRIES = 160;
const MAX_ITEMS = 30;

const emit = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
const holding = () => [...changes.values()].some((t) => Date.now() - t < CHANGE_HOLD_MS);

function write(key: string, entry: Entry) {
  store.delete(key);
  store.set(key, entry);
}

/** Drops the oldest values nobody shows when the cache grows past its limits. */
function trim() {
  const evict = (keep: (key: string) => boolean, max: number) => {
    let count = [...store.keys()].filter(keep).length;
    for (const key of store.keys()) {
      if (count <= max) break;
      if (keep(key) && !shownKeys.has(key)) { store.delete(key); count--; }
    }
  };
  evict((k) => k.startsWith("item:"), MAX_ITEMS);
  evict(() => true, MAX_ENTRIES);
}

export const cache = {
  peek<T>(key: string): T | undefined {
    return store.get(key)?.data as T | undefined;
  },
  has(key: string): boolean {
    return store.has(key);
  },
  fresh(key: string, maxAge: number): boolean {
    const e = store.get(key);
    return Boolean(e && e.at > 0 && Date.now() - e.at < maxAge);
  },
  set<T>(key: string, data: T): void {
    write(key, { data, at: Date.now() });
    trim();
    emit();
  },
  /** Changes a cached value in place (an optimistic update): its age is kept. */
  update<T>(key: string, fn: (prev: T) => T): void {
    const e = store.get(key);
    if (!e) return;
    const data = fn(e.data as T);
    if (data === e.data) return;
    store.set(key, { data, at: e.at });
    emit();
  },
  /** The same change on every value whose key starts with `prefix` (return `prev` to leave one as it is). */
  updateAll<T>(prefix: string, fn: (prev: T, key: string) => T): void {
    let changed = false;
    for (const [key, e] of store) {
      if (!key.startsWith(prefix)) continue;
      const data = fn(e.data as T, key);
      if (data === e.data) continue;
      store.set(key, { data, at: e.at });
      changed = true;
    }
    if (changed) emit();
  },
  /** The first cached value under `prefix` that `pick` finds something in. */
  find<T, R>(prefix: string, pick: (data: T) => R | undefined): R | undefined {
    for (const [key, e] of store) {
      if (!key.startsWith(prefix)) continue;
      const found = pick(e.data as T);
      if (found !== undefined) return found;
    }
    return undefined;
  },
  /** Forgets one value (a page still showing it loads it again). */
  remove(key: string): void {
    if (store.delete(key)) emit();
  },
  /** Forget everything (signing out, a page that crashed on what it was given). */
  clear(): void {
    store.clear();
    inflight.clear();
    latest.clear();
    epoch++;
    emit();
  },
  /** Keys under `prefix` aren't marked stale by changes: they refresh when older than their page's maxAge. */
  steady(prefix: string): void {
    if (!steady.includes(prefix)) steady.push(prefix);
  },
  /** Everything is to be checked again: pages on screen refresh now, the others when they open. */
  staleAll(): void {
    for (const [key, e] of store) {
      if (!steady.some((p) => key.startsWith(p))) store.set(key, { data: e.data, at: 0 });
    }
    emit();
  },
  /** A change is being sent to the API (api.ts). Returns what to hand to changeDone. */
  changeStarted(): number {
    const id = ++requests;
    changes.set(id, Date.now());
    epoch++;
    return id;
  },
  /** …and it's done, or failed: everything is checked again. */
  changeDone(id: number): void {
    changes.delete(id);
    epoch++;
    cache.staleAll();
  },
  /** True while a change is on its way: a refresh would only be dropped, the one after the change will do. */
  busy(): boolean {
    return holding();
  },
  /** One request at a time per key; the answer is cached. */
  load<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
    const running = inflight.get(key);
    if (running && running.epoch === epoch) return running.promise as Promise<T>;
    const id = ++requests;
    const started = epoch;
    latest.set(key, id);
    const promise = fetcher()
      .then((data) => {
        const current = started === epoch && !holding();
        if (latest.get(key) === id && (current || !store.has(key))) {
          write(key, { data, at: current ? Date.now() : 0 });
          trim();
          emit();
        }
        return data;
      })
      .finally(() => { if (inflight.get(key)?.id === id) inflight.delete(key); });
    inflight.set(key, { promise, epoch: started, id });
    return promise;
  },
  /** Fetches ahead (the other tabs once the app is idle); failures are ignored. */
  prefetch<T>(key: string, fetcher: () => Promise<T>, maxAge = 60_000): void {
    if (cache.fresh(key, maxAge) || inflight.has(key)) return;
    cache.load(key, fetcher).catch(() => {});
  },
};

export interface Query<T> {
  data: T | undefined;
  error: string;
  /** nothing to show yet */
  loading: boolean;
  /** `data` belongs to the previous key (with `keep`), while this one loads */
  previous: boolean;
  refresh: () => Promise<void>;
}

/**
 * The cached value at once, loaded when missing or older than `maxAge` (0: on every visit), refreshed in the
 * background when a change marks it stale or something forgets it, and every `poll` ms while `poll` is set.
 * `key` null: nothing to load. `keep`: while a new key loads, keep showing the previous key's value (a search being
 * typed, a filter).
 */
export function useQuery<T>(key: string | null, fetcher: () => Promise<T>,
                            { maxAge = 30_000, poll = 0, keep = false }: { maxAge?: number; poll?: number; keep?: boolean } = {}): Query<T> {
  const entry = useSyncExternalStore(subscribe, () => (key ? store.get(key) : undefined));
  const [failed, setFailed] = useState<{ key: string; message: string } | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const shown = useRef<T | undefined>(undefined);
  const had = useRef<string | null>(null);         // the key whose value this page already showed

  const refresh = useCallback(async () => {
    if (!key) return;
    try {
      await cache.load(key, () => fetcherRef.current());
      setFailed((f) => (f?.key === key ? null : f));
    } catch (e) {
      setFailed({ key, message: (e as Error).message });
    }
  }, [key]);

  // what a page shows is never evicted
  useEffect(() => {
    if (!key) return;
    shownKeys.set(key, (shownKeys.get(key) ?? 0) + 1);
    return () => {
      const n = (shownKeys.get(key) ?? 1) - 1;
      if (n > 0) shownKeys.set(key, n); else shownKeys.delete(key);
    };
  }, [key]);
  // on opening, or when the key changes: load what is missing or too old
  useEffect(() => {
    if (key && !cache.fresh(key, maxAge)) refresh();
  }, [key, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  // marked stale while on screen, or forgotten (the cache cleared): load it again
  useEffect(() => {
    if (!key) return;
    if (entry ? entry.at === 0 && !cache.busy() : had.current === key) refresh();
    had.current = entry ? key : null;
  }, [key, entry, refresh]);
  useEffect(() => {
    if (!poll || !key) return;
    const timer = setInterval(() => { if (!cache.busy()) refresh(); }, poll);
    return () => clearInterval(timer);
  }, [poll, key, refresh]);

  const error = failed && failed.key === key ? failed.message : "";
  if (entry) shown.current = entry.data as T;
  const previous = !entry && keep && shown.current !== undefined && !error;
  return {
    data: entry ? entry.data as T : previous ? shown.current : undefined,
    error,
    loading: Boolean(key) && !entry && !error,
    previous,
    refresh,
  };
}
