import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, type Folder, type ItemSummary, type Space } from "../api";
import { cache, useQuery } from "../cache";
import { Fiche, ResurfaceCard, ResurfaceNote } from "../components/Fiche";
import { CardsSkeleton } from "../components/Skeleton";
import { SwipeRow, type SwipeAction, type SwipeSide } from "../components/Swipe";
import { t } from "../i18n";
import { IconArchive, IconBack, IconCalendar, IconClose, IconEdit, IconFolder, IconPin, IconSearch, IconSettings, IconTodo, IconTrash } from "../icons";
import { KIND_FILTERS, kindsOf } from "../kinds";
import { useDesktop } from "../layout";
import { useFlip } from "../motion";
import { Link, useNavigate } from "../nav";
import { CATEGORIES, categoryOf } from "../perso";
import { dropItem, fetchItems, fetchMore, forgetItem, keys, patchItem, type ItemsPage, type ItemsQuery } from "../queries";

const UNDO_MS = 5000;     // how long the undo button stays, and how long a deletion waits before it happens

type Toast = { id: number; message: string; undo?: () => void };

/** Veille, Perso, or one folder (`/folders/:folderId`, any space). Everything comes from the cache (cache.ts): coming
 *  back to the feed shows it at once, where it was. */
export default function Feed({ space }: { space?: Space }) {
  const { folderId } = useParams();
  const nav = useNavigate();
  const desktop = useDesktop();
  const perso = space === "perso";
  const inFolder = Boolean(folderId);
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") || "";
  const category = params.get("category") || "";
  const tag = params.get("tag") || "";
  const entity = params.get("entity") || "";
  const archived = params.get("archived") === "1";
  const [q, setQ] = useState(params.get("q") || "");
  const [query, setQuery] = useState(q);
  const [actionError, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [opened, setOpened] = useState<{ id: string; side: SwipeSide } | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());     // deleted, waiting for the undo to pass
  const [more, setMore] = useState(false);
  const searchBox = useRef<HTMLInputElement>(null);
  const listBox = useRef<HTMLDivElement>(null);

  // search as you type, after a short pause
  useEffect(() => {
    const timer = setTimeout(() => setQuery(q.trim()), 350);
    return () => clearTimeout(timer);
  }, [q]);

  // ⌘K / Ctrl+K: the search field
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); searchBox.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const filters: ItemsQuery = {
    q: query || undefined, kind: kind ? kindsOf(kind) : undefined, tag: tag || undefined, entity: entity || undefined,
    category: category || undefined, space, folder: folderId, archived: archived || undefined,
  };
  const listKey = keys.items(filters);
  useEffect(() => { setError(""); }, [listKey]);       // an action that failed belongs to the list it was on
  const listNow = cache.peek<ItemsPage>(listKey);
  const busyItems = !listNow?.search && listNow?.items.some((i) => i.status === "pending" || i.status === "processing");
  // a folder being deleted: its list stays as it is, not asked for again (it would get a 404)
  const [leaving, setLeaving] = useState(false);
  const list = useQuery<ItemsPage>(leaving ? null : listKey, () => fetchItems(filters), { keep: true, poll: busyItems ? 4000 : 0 });
  const items = useMemo(() => (list.data?.items ?? []).filter((i) => !hidden.has(i.id)), [list.data, hidden]);
  const total = Math.max(0, (list.data?.total ?? 0) - ((list.data?.items.length ?? 0) - items.length));
  const searching = Boolean(list.data?.search);
  const cold = useRef(!list.data).current;       // nothing cached when the page opened: the list rises in when it comes
  const error = actionError || list.error;
  useFlip(listBox, items);

  const aside = !inFolder && !archived;
  const resurfaceQuery = useQuery(aside ? keys.resurface(space) : null, () => api.resurface(space), { maxAge: 10 * 60_000 });
  const resurface = useMemo(() => (resurfaceQuery.data ?? []).filter((i) => !hidden.has(i.id)), [resurfaceQuery.data, hidden]);
  // On a first visit, the cards wait for "À redécouvrir" above them, so that it doesn't push them down as it arrives.
  const stripComing = cold && aside && !query && !kind && !tag && !entity && !category && resurfaceQuery.loading;
  const loading = list.loading || stripComing;
  const tags = (useQuery(aside ? keys.tags(space) : null, () => api.tags(space), { maxAge: 2 * 60_000 }).data ?? []).slice(0, 24);
  const entities = (useQuery(aside ? keys.entities(space) : null, () => api.entities(space), { maxAge: 2 * 60_000 }).data ?? []).slice(0, 16);
  const stats = useQuery(keys.stats, api.stats).data;
  const week = aside ? stats?.this_week ?? null : null;
  const pending = stats?.open_actions ?? 0;          // open actions: a badge on the phone's À faire icon
  const categories = useQuery(aside && perso ? keys.categories : null, api.categories).data;
  const counts = useMemo(() => Object.fromEntries((categories ?? []).map((x) => [x.id, x.count])) as Record<string, number>, [categories]);
  const archivedKey = keys.archivedCount(space);
  const archivedCount = useQuery(aside ? archivedKey : null,
                                 () => api.items({ space, archived: true, limit: 1 }).then((r) => r.total)).data ?? 0;
  const folders = useQuery(inFolder && folderId !== "none" ? keys.folders : null, api.folders).data;
  const folder: Folder | null = folders?.folders.find((f) => f.id === folderId) ?? null;

  // ---- swipe actions: pin, archive, delete, each with an undo ----

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast((cur) => (cur?.id === toast.id ? null : cur)), UNDO_MS);
    return () => clearTimeout(timer);
  }, [toast]);
  const notify = (message: string, undo?: () => void) => { setError(""); setToast({ id: Date.now(), message, undo }); };
  const countArchived = (delta: number) => cache.update<number>(archivedKey, (n) => Math.max(0, n + delta));

  const togglePin = async (it: ItemSummary) => {
    const pinned = !it.pinned;
    patchItem(it.id, { pinned });
    try {
      await api.patch(it.id, { pinned });
      notify(pinned ? t("Épinglé") : t("Désépinglé"), () => {
        patchItem(it.id, { pinned: !pinned });
        api.patch(it.id, { pinned: !pinned }).catch((e) => setError((e as Error).message));
      });
    } catch (e) {
      patchItem(it.id, { pinned: !pinned });
      setError((e as Error).message);
    }
  };

  const setArchived = async (it: ItemSummary, value: boolean) => {
    const putBack = dropItem(it.id, listKey);
    countArchived(value ? 1 : -1);
    try {
      await api.patch(it.id, { archived: value });
      notify(value ? t("Archivé") : t("Sorti des archives"), () => {
        putBack();
        countArchived(value ? -1 : 1);
        api.patch(it.id, { archived: !value }).catch((e) => setError((e as Error).message));
      });
    } catch (e) {
      putBack();
      countArchived(value ? -1 : 1);
      setError((e as Error).message);
    }
  };

  // A deletion can't be undone on the server: it waits UNDO_MS, and happens at once if the page goes away.
  const pendingDeletes = useRef(new Map<string, number>());
  const showAgain = (id: string) => setHidden((h) => { const n = new Set(h); n.delete(id); return n; });
  const commitDelete = useCallback((id: string) => {
    const timer = pendingDeletes.current.get(id);
    if (timer === undefined) return;
    clearTimeout(timer);
    pendingDeletes.current.delete(id);
    dropItem(id);
    api.remove(id).then(() => forgetItem(id)).catch((e) => setError((e as Error).message)).finally(() => showAgain(id));
  }, []);
  useEffect(() => {
    const flush = () => [...pendingDeletes.current.keys()].forEach(commitDelete);
    window.addEventListener("pagehide", flush);
    return () => { window.removeEventListener("pagehide", flush); flush(); };
  }, [commitDelete]);

  const remove = (it: ItemSummary) => {
    setHidden((h) => new Set(h).add(it.id));
    if (it.archived || archived) countArchived(-1);
    pendingDeletes.current.set(it.id, window.setTimeout(() => commitDelete(it.id), UNDO_MS));
    notify(t("Supprimé"), () => {
      clearTimeout(pendingDeletes.current.get(it.id));
      pendingDeletes.current.delete(it.id);
      showAgain(it.id);
      if (it.archived || archived) countArchived(1);
    });
  };

  const loadMore = async () => {
    setMore(true);
    try { await fetchMore(filters); } catch (e) { setError((e as Error).message); } finally { setMore(false); }
  };

  const actionsFor = (it: ItemSummary): { start?: SwipeAction; end: SwipeAction[] } => {
    const del: SwipeAction = { id: "delete", label: t("Supprimer"), icon: <IconTrash size={19} />, tone: "danger", run: () => remove(it) };
    if (archived) {
      return { end: [del, { id: "unarchive", label: t("Ressortir"), icon: <IconArchive size={19} />, tone: "ink",
                            run: () => setArchived(it, false) }] };
    }
    return {
      start: { id: "pin", label: it.pinned ? t("Désépingler") : t("Épingler"), icon: <IconPin size={19} />,
               tone: "pin", run: () => togglePin(it) },
      end: [del, { id: "archive", label: t("Archiver"), icon: <IconArchive size={19} />, tone: "ink", run: () => setArchived(it, true) }],
    };
  };

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
  const countLabel = searching
    ? (items.length > 1 ? t("{n} résultats les plus proches", { n: items.length }) : t("{n} résultat le plus proche", { n: items.length }))
    : (total > 1 ? t("{n} éléments", { n: total }) : t("{n} élément", { n: total }));
  const headCount = !loading && !archived && (browsing && week !== null && !inFolder
    ? `${countLabel} · ${t("{n} cette semaine", { n: week })}` : countLabel);
  const showResurface = !archived && !inFolder && browsing && resurface.length > 0;

  return (
    <div className={`page feed${inFolder || archived ? " solo" : ""}`}>
      <div className="feed-main">
        {archived ? (
          <>
            <button className="back" onClick={() => setFilter("archived", "")}><IconBack size={18} /> {perso ? t("Perso") : t("Veille")}</button>
            <div className="title-block">
              <h1 className="title">{t("Archives")}</h1>
              <p className="lede">{t("Les éléments archivés ne sont plus dans le fil ni dans les suggestions. Glisse une fiche vers la gauche pour la ressortir ou la supprimer.")}</p>
            </div>
          </>
        ) : inFolder ? (
          <>
            <Link className="back" to="/folders"><IconBack size={18} /> {t("Dossiers")}</Link>
            {editing && folder ? (
              <FolderEditor folder={folder} onDone={(f) => {
                setEditing(false);
                if (f) cache.update<{ folders: Folder[] }>(keys.folders, (r) => ({ ...r, folders: r.folders.map((x) => (x.id === f.id ? { ...x, ...f } : x)) }));
              }}
                            onDeleting={setLeaving} onDeleted={() => nav("/folders", { replace: true })} />
            ) : (
              <>
                <header className="page-head">
                  <h1 className="title">{folderId === "none" ? t("Sans dossier") : folder?.name ?? t("Dossier")}</h1>
                  {headCount && <span className="feed-count desk-only">{headCount}</span>}
                  {folder && <button className="icon-link" style={{ display: "inline-flex", border: 0, background: "none" }}
                                     aria-label={t("Modifier le dossier")} onClick={() => setEditing(true)}><IconEdit size={20} /></button>}
                </header>
                {folder?.description && <p className="lede">{folder.description}</p>}
              </>
            )}
          </>
        ) : perso ? (
          <>
            <header className="page-head">
              <h1 className="title">{t("Perso")}</h1>
              {headCount && <span className="feed-count desk-only">{headCount}</span>}
              <Link className="icon-link" to="/journal" aria-label={t("Journal")}><IconCalendar size={22} /></Link>
            </header>
            <div className="feed-top" style={{ alignItems: "center" }}>
              <p className="lede">{t("Tes principes, tes leçons, tes objectifs.")}</p>
              <div className="btn-row" style={{ flexWrap: "nowrap" }}>
                <Link className="btn small desk-only" to={newNoteHref}>{t("Nouvelle note")}</Link>
                <Link className="btn small" style={{ height: 42, padding: "0 14px", borderRadius: 12 }} to="/ask?mode=advice">{t("Demander conseil")}</Link>
              </div>
            </div>
          </>
        ) : (
          <header className="page-head">
            <h1 className="title">{t("Veille")}</h1>
            {headCount && <span className="feed-count desk-only">{headCount}</span>}
            <Link className="icon-link" to="/folders" aria-label={t("Dossiers")}><IconFolder size={22} /></Link>
            <Link className="icon-link" to="/todo" aria-label={pending > 0 ? t("À faire, {n} en attente", { n: pending }) : t("À faire")}>
              <IconTodo size={22} />{pending > 0 && <span className="badge">{pending}</span>}
            </Link>
            <Link className="icon-link" to="/settings" aria-label={t("Réglages")}><IconSettings size={22} /></Link>
          </header>
        )}

        {!archived && <>
          <label className="search">
            <IconSearch size={20} />
            <span className="sr-only">{perso ? t("Chercher dans tes notes perso") : t("Chercher dans ta veille")}</span>
            <input ref={searchBox} type="search" autoComplete="off" enterKeyHint="search"
                   placeholder={perso ? t("Une leçon, un principe, un souvenir…") : desktop ? t("Chercher un sujet, une idée, une personne…") : t("Chercher un sujet, une personne…")}
                   value={q} onChange={(e) => setQ(e.target.value)} />
            <kbd>⌘K</kbd>
          </label>

          {perso ? (
            <div className="chips scroll" role="group" aria-label={t("Filtrer par catégorie")}>
              <button className="chip" aria-pressed={!category} onClick={() => setFilter("category", "")}>{t("Tout")}</button>
              {CATEGORIES.map((c) => (
                <button key={c.id} className="chip" aria-pressed={category === c.id}
                        onClick={() => setFilter("category", category === c.id ? "" : c.id)}>
                  {c.plural}{counts[c.id] ? <span className="n">{counts[c.id]}</span> : null}
                </button>
              ))}
            </div>
          ) : (
            <div className="chips scroll" role="group" aria-label={t("Filtrer par type")}>
              <button className="chip" aria-pressed={!kind} onClick={() => setFilter("kind", "")}>{t("Tout")}</button>
              {KIND_FILTERS.map((f) => (
                <button key={f.id} className="chip" aria-pressed={kind === f.id} onClick={() => setFilter("kind", kind === f.id ? "" : f.id)}>
                  {f.short ? <><span className="phone-only">{f.short}</span><span className="desk-only">{f.label}</span></> : f.label}
                </button>
              ))}
            </div>
          )}
          {activeFilters.length > 0 && (
            <div className="chips">
              {activeFilters.map((f) => (
                <button key={f.key} className="chip" aria-pressed="true" onClick={() => setFilter(f.key, "")}>
                  {f.label}<span className="x" aria-label={t("Retirer le filtre")}><IconClose size={14} /></span>
                </button>
              ))}
            </div>
          )}
        </>}

        {!archived && perso && !loading && browsing && items.length > 0 && charterCount === 0 && (
          <div className="warn">
            {t("Écris d'abord quelques {principles} et {values} : le mode Conseil les relit en entier à chaque question.")
              .split(/(\{principles\}|\{values\})/).map((part, i) =>
                part === "{principles}" ? <Link key={i} to="/note/new?category=principe">{t("principes")}</Link>
                : part === "{values}" ? <Link key={i} to="/note/new?category=valeur">{t("valeurs")}</Link>
                : part)}
          </div>
        )}

        {showResurface && (perso ? (
          <div className="resurface-strip">
            <span className="feed-count">{t("Écrit il y a un moment, toujours vrai ?")}</span>
            <ResurfaceNote item={resurface[0]} />
          </div>
        ) : (
          <div className="resurface-strip">
            <div className="strip-head"><h2>{t("À redécouvrir")}</h2><span>{t("sauvé il y a un moment")}</span></div>
            <div className="row">{resurface.map((it) => <ResurfaceCard key={it.id} item={it} />)}</div>
          </div>
        ))}

        {error && <div className="error-box">{error}</div>}

        {!loading && items.length === 0 && !error && (
          archived ? (
            <div className="empty"><p>{t("Rien dans les archives.")}</p></div>
          ) : inFolder && browsing ? (
            <div className="empty">
              <h2>{t("Ce dossier est vide")}</h2>
              <p>{t("Claude y range ce qui correspond à sa description. Tu peux aussi y déplacer un élément depuis sa fiche, ou le choisir dans le Raccourci.")}</p>
            </div>
          ) : browsing ? (
            perso ? (
              <div className="empty">
                <h2>{t("Ton espace perso est vide")}</h2>
                <p>{t("Commence par tes principes et tes valeurs : ce sont eux que le mode Conseil relit en entier pour t'aider à décider. "
                  + "Ensuite, note tes leçons, tes objectifs, ton journal.")}</p>
                <div className="btn-row" style={{ justifyContent: "center" }}>
                  <Link className="btn primary" to="/note/new?category=principe">{t("Écrire un principe")}</Link>
                  <Link className="btn" to="/note/new?category=valeur">{t("Écrire une valeur")}</Link>
                </div>
              </div>
            ) : (
              <div className="empty">
                <h2>{t("Ta KB est vide pour l'instant")}</h2>
                <p>{t("Partage un tweet, un article ou un PDF avec le Raccourci « Ajouter à ma KB », ou colle un lien ici.")}</p>
                <Link className="btn primary" to="/add">{t("Ajouter un premier élément")}</Link>
              </div>
            )
          ) : (
            <div className="empty">
              <p>{t("Rien ne correspond. Essaie d'autres mots ou retire un filtre.")}</p>
              {perso && current && <Link className="btn" to={newNoteHref}>{t("Nouvelle note · {category}", { category: current.label })}</Link>}
            </div>
          )
        )}

        {loading && <CardsSkeleton />}
        {items.length > 0 && !stripComing && (
          <div className={`feed-list${list.previous ? " refreshing" : ""}${cold ? " appear" : ""}`} ref={listBox}>
            <span className={`feed-count${searching || archived ? "" : " phone-only"}`}>{countLabel}</span>
            {items.map((it) => (
              <SwipeRow key={it.id} flipId={it.id} {...actionsFor(it)} open={opened?.id === it.id ? opened.side : null}
                        onOpenChange={(side) => setOpened(side ? { id: it.id, side } : (cur) => (cur?.id === it.id ? null : cur))}>
                <Fiche item={it} />
              </SwipeRow>
            ))}
          </div>
        )}

        {!searching && items.length < total && (
          <div style={{ textAlign: "center" }}><button className="btn small" onClick={loadMore} disabled={more}>{t("Afficher plus")}</button></div>
        )}

        {!archived && !inFolder && archivedCount > 0 && !loading && (
          <div className="archives-link">
            <button className="linkish" onClick={() => setFilter("archived", "1")}>
              <IconArchive size={15} /> {t("Archives")} <span className="n">{archivedCount}</span>
            </button>
          </div>
        )}

        {toast && (
          <div className="toast" role="status" key={toast.id}>
            <span>{toast.message}</span>
            {toast.undo && <button type="button" className="toast-undo" onClick={() => { toast.undo?.(); setToast(null); }}>{t("Annuler|undo")}</button>}
          </div>
        )}
      </div>

      {!inFolder && !archived && (
        <aside className="feed-aside" aria-label={t("Explorer")}>
          {resurface.length > 0 && (
            <section className="aside-block">
              <h2>{t("À redécouvrir")}</h2>
              <p>{perso ? t("Écrit il y a un moment, toujours vrai ?") : t("Sauvé il y a un moment, toujours d'actualité")}</p>
              {resurface.slice(0, 3).map((it) => <ResurfaceCard key={it.id} item={it} />)}
            </section>
          )}
          {tags.length > 0 && (
            <section className="aside-block">
              <h2>{t("Tags")}</h2>
              <div className="pills">
                {tags.map((x) => (
                  <button key={x.tag} type="button" className="pill" onClick={() => setFilter("tag", x.tag)}>#{x.tag} <span className="n">{x.count}</span></button>
                ))}
              </div>
            </section>
          )}
          {entities.length > 0 && (
            <section className="aside-block">
              <h2>{perso ? t("Personnes et idées") : t("Personnes & outils")}</h2>
              <div className="pills">
                {entities.map((e) => (
                  <button key={e.name} type="button" className="pill" onClick={() => setFilter("entity", e.name)}>{e.name} <span className="n">{e.count}</span></button>
                ))}
              </div>
            </section>
          )}
        </aside>
      )}
    </div>
  );
}

/** Rename a folder, say what goes in it (Claude files by it), or delete it. */
export function FolderEditor({ folder, onDone, onDeleting, onDeleted }: {
  folder: Folder; onDone: (f?: Folder) => void; onDeleting: (on: boolean) => void; onDeleted: () => void;
}) {
  const [name, setName] = useState(folder.name);
  const [description, setDescription] = useState(folder.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try { onDone(await api.updateFolder(folder.id, { name, description })); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!window.confirm(t("Supprimer le dossier « {name} » ? Ses éléments restent dans ta KB, sans dossier.", { name: folder.name }))) return;
    setBusy(true);
    onDeleting(true);
    try { await api.deleteFolder(folder.id); onDeleted(); } catch (err) { onDeleting(false); setError((err as Error).message); setBusy(false); }
  };

  return (
    <form className="folder-form" onSubmit={save}>
      <label className="stack">
        <span className="label">{t("Nom du dossier")}</span>
        <input className="field" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required />
      </label>
      <label className="stack">
        <span className="label">{t("Ce qui va dedans")} <span className="opt">{t("facultatif")}</span></span>
        <textarea className="field" rows={2} value={description} onChange={(e) => setDescription(e.target.value)}
                  placeholder={t("ex. préparation d'entretiens, questions techniques, études de cas")} />
        <span className="hint">{t("Claude s'en sert pour ranger les nouveaux éléments.")}</span>
      </label>
      {error && <div className="error-box">{error}</div>}
      <div className="btn-row">
        <button className="btn small primary" disabled={busy || !name.trim()}>{t("Enregistrer")}</button>
        <button type="button" className="btn small ghost" onClick={() => onDone()}>{t("Annuler")}</button>
        <span style={{ flex: 1 }} />
        <button type="button" className="btn small danger" onClick={remove} disabled={busy}><IconTrash size={16} /> {t("Supprimer le dossier")}</button>
      </div>
    </form>
  );
}
