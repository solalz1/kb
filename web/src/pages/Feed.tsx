import { Archive, ArchiveRestore, ArrowLeft, CalendarDays, Compass, ListChecks, NotebookPen, Pin, PinOff, Search, Settings as Cog, Shuffle, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ItemSummary, type Kind, type Space } from "../api";
import { Fiche } from "../components/Fiche";
import { SwipeRow, type SwipeAction, type SwipeSide } from "../components/Swipe";
import { t } from "../i18n";
import { FILTER_ORDER, KINDS } from "../kinds";
import { CATEGORIES, categoryOf } from "../perso";

const PAGE = 30;
const UNDO_MS = 5000;     // how long the undo button stays, and how long a deletion waits before it happens

type Toast = { id: number; message: string; undo?: () => void };

/** `pending`: open actions, shown as a badge on the phone's Feed head (the sidebar shows it on desktop). */
export default function Feed({ space, pending = 0 }: { space: Space; pending?: number }) {
  const perso = space === "perso";
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") || "";
  const category = params.get("category") || "";
  const tag = params.get("tag") || "";
  const entity = params.get("entity") || "";
  const archived = params.get("archived") === "1";
  const [q, setQ] = useState(params.get("q") || "");
  const [query, setQuery] = useState(q);
  const [items, setItems] = useState<ItemSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [searching, setSearching] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [resurface, setResurface] = useState<ItemSummary[]>([]);
  const [tags, setTags] = useState<{ tag: string; count: number }[]>([]);
  const [entities, setEntities] = useState<{ name: string; count: number }[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [archivedCount, setArchivedCount] = useState(0);
  const [opened, setOpened] = useState<{ id: string; side: SwipeSide } | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const reqId = useRef(0);
  const itemsRef = useRef(items);
  itemsRef.current = items;

  // recherche « au fil de la frappe », avec un léger délai
  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);

  const load = async (offset = 0) => {
    const id = ++reqId.current;
    if (offset === 0) setLoading(true);
    try {
      const res = await api.items({ q: query || undefined, kind: kind || undefined, tag: tag || undefined,
                                    entity: entity || undefined, category: category || undefined, space,
                                    archived: archived || undefined, limit: PAGE, offset });
      if (id !== reqId.current) return;
      setItems((prev) => (offset ? [...prev, ...res.items] : res.items));
      setTotal(res.total);
      setSearching(res.search);
      setError("");
    } catch (e) {
      if (id === reqId.current) setError((e as Error).message);
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  };

  useEffect(() => { load(0); }, [query, kind, tag, entity, category, archived]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    api.resurface(space).then(setResurface).catch(() => {});
    api.tags(space).then((t) => setTags(t.slice(0, 40))).catch(() => {});
    api.entities(space).then((e) => setEntities(e.slice(0, 24))).catch(() => {});
    if (perso) api.categories().then((c) => setCounts(Object.fromEntries(c.map((x) => [x.id, x.count])))).catch(() => {});
    api.items({ space, archived: true, limit: 1 }).then((r) => setArchivedCount(r.total)).catch(() => {});
  }, [space, perso]);

  // ---- swipe actions: pin, archive, delete, each with an undo ----

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast((cur) => (cur?.id === toast.id ? null : cur)), UNDO_MS);
    return () => clearTimeout(timer);
  }, [toast]);
  const notify = (message: string, undo?: () => void) => setToast({ id: Date.now(), message, undo });

  const patchLocal = (id: string, patch: Partial<ItemSummary>) =>
    setItems((prev) => prev.map((i) => (i.id === id ? { ...i, ...patch } : i)));
  const takeOut = (it: ItemSummary) => {
    const index = itemsRef.current.findIndex((i) => i.id === it.id);
    setItems((prev) => prev.filter((i) => i.id !== it.id));
    setTotal((n) => Math.max(0, n - 1));
    return () => {
      setItems((prev) => (prev.some((i) => i.id === it.id) ? prev
        : [...prev.slice(0, Math.max(0, index)), it, ...prev.slice(Math.max(0, index))]));
      setTotal((n) => n + 1);
    };
  };

  const togglePin = async (it: ItemSummary) => {
    const pinned = !it.pinned;
    patchLocal(it.id, { pinned });
    try {
      await api.patch(it.id, { pinned });
      notify(pinned ? t("Épinglé") : t("Désépinglé"), () => {
        patchLocal(it.id, { pinned: !pinned });
        api.patch(it.id, { pinned: !pinned }).catch((e) => setError((e as Error).message));
      });
    } catch (e) {
      patchLocal(it.id, { pinned: !pinned });
      setError((e as Error).message);
    }
  };

  const setArchived = async (it: ItemSummary, value: boolean) => {
    const putBack = takeOut(it);
    setArchivedCount((n) => Math.max(0, n + (value ? 1 : -1)));
    try {
      await api.patch(it.id, { archived: value });
      notify(value ? t("Archivé") : t("Sorti des archives"), () => {
        putBack();
        setArchivedCount((n) => Math.max(0, n + (value ? -1 : 1)));
        api.patch(it.id, { archived: !value }).catch((e) => setError((e as Error).message));
      });
    } catch (e) {
      putBack();
      setArchivedCount((n) => Math.max(0, n + (value ? -1 : 1)));
      setError((e as Error).message);
    }
  };

  // A deletion can't be undone on the server: it waits UNDO_MS, and happens at once if the page goes away.
  const pendingDeletes = useRef(new Map<string, number>());
  const commitDelete = useCallback((id: string) => {
    const timer = pendingDeletes.current.get(id);
    if (timer === undefined) return;
    clearTimeout(timer);
    pendingDeletes.current.delete(id);
    api.remove(id).catch((e) => setError((e as Error).message));
  }, []);
  useEffect(() => {
    const flush = () => [...pendingDeletes.current.keys()].forEach(commitDelete);
    window.addEventListener("pagehide", flush);
    return () => { window.removeEventListener("pagehide", flush); flush(); };
  }, [commitDelete]);

  const remove = (it: ItemSummary) => {
    const putBack = takeOut(it);
    if (it.archived || archived) setArchivedCount((n) => Math.max(0, n - 1));
    pendingDeletes.current.set(it.id, window.setTimeout(() => commitDelete(it.id), UNDO_MS));
    notify(t("Supprimé"), () => {
      clearTimeout(pendingDeletes.current.get(it.id));
      pendingDeletes.current.delete(it.id);
      putBack();
      if (it.archived || archived) setArchivedCount((n) => n + 1);
    });
  };

  const actionsFor = (it: ItemSummary): { start?: SwipeAction; end: SwipeAction[] } => {
    const del: SwipeAction = { id: "delete", label: t("Supprimer"), icon: <Trash2 size={19} />, tone: "danger", run: () => remove(it) };
    if (archived) {
      return { end: [del, { id: "unarchive", label: t("Ressortir"), icon: <ArchiveRestore size={19} />, tone: "ink",
                            run: () => setArchived(it, false) }] };
    }
    return {
      start: { id: "pin", label: it.pinned ? t("Désépingler") : t("Épingler"), icon: it.pinned ? <PinOff size={19} /> : <Pin size={19} />,
               tone: "pin", run: () => togglePin(it) },
      end: [del, { id: "archive", label: t("Archiver"), icon: <Archive size={19} />, tone: "ink", run: () => setArchived(it, true) }],
    };
  };

  // tant que des éléments sont en cours de traitement, on rafraîchit
  const hasPending = items.some((i) => i.status === "pending" || i.status === "processing");
  useEffect(() => {
    if (!hasPending || searching) return;
    const t = setInterval(() => load(0), 4000);
    return () => clearInterval(t);
  }, [hasPending, searching]); // eslint-disable-line react-hooks/exhaustive-deps

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    value ? next.set(key, value) : next.delete(key);
    setParams(next, { replace: true });
  };

  const activeFilters = useMemo(
    () => [tag && { key: "tag", label: `#${tag}` }, entity && { key: "entity", label: entity }].filter(Boolean) as { key: string; label: string }[],
    [tag, entity],
  );
  const browsing = !query && !kind && !tag && !entity && !category;
  const charterCount = (counts.principe || 0) + (counts.valeur || 0);
  const current = categoryOf(category);
  const newNoteHref = `/note/new${current ? `?category=${current.id}` : ""}`;

  return (
    <div className="page wide">
      <div className="feed-layout">
        <div>
          {archived && (
            <header className="space-head">
              <div>
                <button className="back" onClick={() => setFilter("archived", "")}><ArrowLeft size={16} /> {perso ? t("Perso") : t("Veille")}</button>
                <h1 className="title">{t("Archives")}</h1>
                <p className="muted">{t("Les éléments archivés ne sont plus dans le fil ni dans les suggestions. Glisse une fiche vers la gauche pour la ressortir ou la supprimer.")}</p>
              </div>
            </header>
          )}
          {!archived && !perso && (
            <header className="page-head">
              <h1 className="title">{t("Veille")}</h1>
              <Link className="icon-link" to="/todo" aria-label={pending > 0 ? t("À faire, {n} en attente", { n: pending }) : t("À faire")}>
                <ListChecks size={22} />{pending > 0 && <span className="badge">{pending}</span>}
              </Link>
              <Link className="icon-link" to="/settings" aria-label={t("Réglages")}><Cog size={22} /></Link>
            </header>
          )}
          {!archived && perso && (
            <header className="space-head">
              <div>
                <h1 className="title">{t("Perso")}</h1>
                <p className="muted">{t("Tes principes, tes valeurs, tes leçons et tes objectifs. Le mode Conseil s'appuie dessus pour t'aider à décider.")}</p>
              </div>
              <div className="space-actions">
                <Link className="btn primary" to={newNoteHref}><NotebookPen size={16} /> {t("Nouvelle note")}</Link>
                <Link className="btn" to="/journal"><CalendarDays size={16} /> {t("Journal")}</Link>
                <Link className="btn" to="/ask?mode=advice"><Compass size={16} /> {t("Demander conseil")}</Link>
              </div>
            </header>
          )}

          {!archived && <>
          <div className="searchbar">
            <Search size={18} />
            <label className="sr-only" htmlFor="q">{perso ? t("Chercher dans tes notes perso") : t("Chercher dans ta veille")}</label>
            <input id="q" className="field" type="search" autoComplete="off" enterKeyHint="search"
                   placeholder={perso ? t("Chercher une leçon, un principe, un souvenir…") : t("Chercher un sujet, une idée, une personne…")}
                   value={q} onChange={(e) => setQ(e.target.value)} />
          </div>

          {perso ? (
            <div className="chips" role="group" aria-label={t("Filtrer par catégorie")}>
              <button className="chip" aria-pressed={!category} onClick={() => setFilter("category", "")}>{t("Tout")}</button>
              {CATEGORIES.map((c) => (
                <button key={c.id} className="chip" aria-pressed={category === c.id}
                        onClick={() => setFilter("category", category === c.id ? "" : c.id)}>
                  {c.plural}{counts[c.id] ? <span className="n">{counts[c.id]}</span> : null}
                </button>
              ))}
            </div>
          ) : (
            <div className="chips" role="group" aria-label={t("Filtrer par type")}>
              <button className="chip" aria-pressed={!kind} onClick={() => setFilter("kind", "")}>{t("Tout")}</button>
              {FILTER_ORDER.map((k: Kind) => (
                <button key={k} className="chip" aria-pressed={kind === k} onClick={() => setFilter("kind", kind === k ? "" : k)}>
                  <span className="dot" data-kind={k} />{KINDS[k].plural}
                </button>
              ))}
            </div>
          )}
          {activeFilters.length > 0 && (
            <div className="chips">
              {activeFilters.map((f) => (
                <button key={f.key} className="chip" aria-pressed="true" onClick={() => setFilter(f.key, "")}>
                  {f.label}<X size={14} className="x" aria-label={t("Retirer le filtre")} />
                </button>
              ))}
            </div>
          )}

          </>}

          {!archived && perso && !loading && browsing && items.length > 0 && charterCount === 0 && (
            <div className="warn charter-tip">
              {t("Écris d'abord quelques {principles} et {values} : le mode Conseil les relit en entier à chaque question.")
                .split(/(\{principles\}|\{values\})/).map((part, i) =>
                  part === "{principles}" ? <Link key={i} to="/note/new?category=principe">{t("principes")}</Link>
                  : part === "{values}" ? <Link key={i} to="/note/new?category=valeur">{t("valeurs")}</Link>
                  : part)}
            </div>
          )}

          {!archived && browsing && resurface.length > 0 && (
            <>
              <div className="resurface-title mobile"><Shuffle size={15} /> {perso ? t("Écrit il y a un moment, toujours vrai ?") : t("Sauvé il y a un moment, toujours d'actualité")}</div>
              <div className="resurface">
                {resurface.map((it) => <Fiche key={it.id} item={it} compact />)}
              </div>
            </>
          )}

          {error && <div className="error-box">{error}</div>}

          {!loading && items.length === 0 && !error && (
            archived ? (
              <div className="empty"><p>{t("Rien dans les archives.")}</p></div>
            ) : browsing ? (
              perso ? (
                <div className="empty">
                  <h2 className="title" style={{ fontSize: 22 }}>{t("Ton espace perso est vide")}</h2>
                  <p>{t("Commence par tes principes et tes valeurs : ce sont eux que le mode Conseil relit en entier pour t'aider à décider. "
                    + "Ensuite, note tes leçons, tes objectifs, ton journal.")}</p>
                  <div className="space-actions" style={{ justifyContent: "center" }}>
                    <Link className="btn primary" to="/note/new?category=principe">{t("Écrire un principe")}</Link>
                    <Link className="btn" to="/note/new?category=valeur">{t("Écrire une valeur")}</Link>
                  </div>
                  <p className="hint">{t("Depuis le bouton Partager, ajoute {tag} à ta note pour ranger un lien ici.")
                    .split(/(\{tag\})/).map((part, i) => (part === "{tag}" ? <code key={i}>#perso</code> : part))}</p>
                </div>
              ) : (
                <div className="empty">
                  <h2 className="title" style={{ fontSize: 22 }}>{t("Ta KB est vide pour l'instant")}</h2>
                  <p>{t("Partage un tweet, un article ou un PDF avec le Raccourci « Ajouter à ma KB », ou colle un lien ici.")}</p>
                  <Link className="btn primary" to="/add">{t("Ajouter un premier élément")}</Link>
                </div>
              )
            ) : (
              <div className="empty">
                <p>{t("Rien ne correspond. Essaie d'autres mots ou retire un filtre.")}</p>
                {perso && current && <Link className="btn" to={newNoteHref}><NotebookPen size={16} /> {t("Nouvelle note · {category}", { category: current.label })}</Link>}
              </div>
            )
          )}

          {items.length > 0 && (
            <div className="feed-meta">
              {searching
                ? (items.length > 1 ? t("{n} résultats les plus proches", { n: items.length }) : t("{n} résultat le plus proche", { n: items.length }))
                : (total > 1 ? t("{n} éléments", { n: total }) : t("{n} élément", { n: total }))}
            </div>
          )}
          {items.map((it) => (
            <SwipeRow key={it.id} {...actionsFor(it)} open={opened?.id === it.id ? opened.side : null}
                      onOpenChange={(side) => setOpened(side ? { id: it.id, side } : (cur) => (cur?.id === it.id ? null : cur))}>
              <Fiche item={it} />
            </SwipeRow>
          ))}

          {!searching && items.length < total && (
            <div style={{ textAlign: "center", marginTop: 18 }}>
              <button className="btn" onClick={() => load(items.length)}>{t("Afficher plus")}</button>
            </div>
          )}

          {!archived && archivedCount > 0 && !loading && (
            <div className="archives-link">
              <button className="linkish" onClick={() => setFilter("archived", "1")}>
                <Archive size={14} /> {t("Archives")} <span className="n">{archivedCount}</span>
              </button>
            </div>
          )}

          {toast && (
            <div className="toast" role="status">
              <span>{toast.message}</span>
              {toast.undo && (
                <button type="button" className="toast-undo" onClick={() => { toast.undo?.(); setToast(null); }}>{t("Annuler|undo")}</button>
              )}
            </div>
          )}
        </div>

        <aside className="aside" aria-label={t("Explorer")}>
          {resurface.length > 0 && (
            <section>
              <h2>{t("À redécouvrir")}</h2>
              {resurface.map((it) => <Fiche key={it.id} item={it} compact />)}
            </section>
          )}
          {tags.length > 0 && (
            <section>
              <h2>{t("Tags")}</h2>
              <div className="tagcloud">
                {tags.map((t) => (
                  <button key={t.tag} onClick={() => setFilter("tag", t.tag)}>#{t.tag}<span className="n">{t.count}</span></button>
                ))}
              </div>
            </section>
          )}
          {entities.length > 0 && (
            <section>
              <h2>{perso ? t("Personnes et idées") : t("Personnes, outils, concepts")}</h2>
              <div className="tagcloud">
                {entities.map((e) => (
                  <button key={e.name} onClick={() => setFilter("entity", e.name)}>{e.name}<span className="n">{e.count}</span></button>
                ))}
              </div>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}
