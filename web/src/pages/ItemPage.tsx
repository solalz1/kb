import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type ItemDetail, type Space } from "../api";
import { cache, useQuery } from "../cache";
import { openInClaude } from "../claude";
import { Embed } from "../components/Embed";
import { Lines, PageSkeleton } from "../components/Skeleton";
import { IconBack, IconDownload, IconExternal, IconMore, IconPin, IconSpinner } from "../icons";
import { fullDate, genreLabel, hostOf, sourceLabel } from "../kinds";
import { localized, locale, t, tServer } from "../i18n";
import { useDesktop } from "../layout";
import { renderMarkdown } from "../markdown";
import { Link, useNavigate } from "../nav";
import { CATEGORIES, headLabel, isEditableNote, spaceHome } from "../perso";
import { dropItem, forgetItem, keys, patchItem, summaryOf } from "../queries";

const PREVIEW = 1400;     // characters of the full content shown before "show everything"
const shortDate = (iso: string) => new Date(iso).toLocaleDateString(locale, { day: "numeric", month: "long" });

/** The card as a list knows it, while the whole item loads: the page opens with its title and summary at once. */
const fromSummary = (id: string): ItemDetail | undefined => {
  const s = summaryOf(id);
  return s && {
    content: null, key_points: [], entities: [], use_cases: [], genre: null, metadata: {}, author_url: null, file_url: null,
    file_name: null, input_url: null, links: [], actions: [], language: null, ...s,
  };
};

export default function ItemPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const desktop = useDesktop();
  // each visit counts once as a view; refreshes in the background don't
  const viewed = useRef("");
  // while it is being deleted the page stops asking for it (it would get a 404) and keeps showing what it had
  const [removing, setRemoving] = useState<ItemDetail | null>(null);
  const pendingNow = ["pending", "processing"].includes(cache.peek<ItemDetail>(keys.item(id))?.status ?? "");
  const query = useQuery<ItemDetail>(removing ? null : keys.item(id), () => {
    const view = viewed.current !== id;
    viewed.current = id;
    return api.item(id, view);
  }, { maxAge: 0, poll: pendingNow ? 3000 : 0 });
  const partial = !query.data && !removing;
  const item = removing ?? query.data ?? fromSummary(id) ?? null;
  const error = query.error;
  const [note, setNote] = useState("");
  const [savedNote, setSavedNote] = useState(false);
  const [tagInput, setTagInput] = useState<string | null>(null);
  const folders = useQuery(keys.folders, api.folders).data?.folders ?? [];
  const [menu, setMenu] = useState(false);
  const [allContent, setAllContent] = useState(false);
  const [failed, setFailed] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setAllContent(false); setFailed(""); setRemoving(null); }, [id]);
  // the "why" field follows the item, but never overwrites what is being typed
  const serverNote = item?.user_note ?? "";
  useEffect(() => { setNote(serverNote); }, [id, serverNote]);

  const pending = item && (item.status === "pending" || item.status === "processing");

  useEffect(() => {
    if (!menu) return;
    const close = (e: PointerEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [menu]);

  if (error && !item) return <div className="page"><div className="error-box">{error}</div></div>;
  if (!item) return <div className="page item-page"><div className="item-main"><PageSkeleton /></div></div>;

  /** Shown at once, then sent; if the API refuses, the page comes back to what it holds. */
  const update = async (body: Parameters<typeof api.patch>[1]) => {
    patchItem(item.id, body as Partial<ItemDetail>);
    setFailed("");
    try { await api.patch(item.id, body); } catch (e) { setFailed((e as Error).message); }
  };
  const saveNote = async () => {
    if (note.trim() === (item.user_note ?? "")) return;
    await update({ user_note: note.trim() });
    setSavedNote(true);
    setTimeout(() => setSavedNote(false), 2000);
  };
  const addTag = async () => {
    const tag = (tagInput ?? "").trim().toLowerCase().replace(/^#/, "").replace(/\s+/g, "-");
    setTagInput(null);
    if (!tag || item.tags.includes(tag)) return;
    await update({ tags: [...item.tags, tag] });
  };
  const removeTag = (tag: string) => update({ tags: item.tags.filter((x) => x !== tag) });
  const toggleAction = async (aid: number, done: boolean) => {
    cache.update<ItemDetail>(keys.item(item.id), (it) => ({ ...it, actions: it.actions.map((a) => (a.id === aid ? { ...a, done } : a)) }));
    try { await api.setAction(aid, done); } catch (e) { setFailed((e as Error).message); }
  };
  const remove = async () => {
    if (!window.confirm(t("Supprimer définitivement cet élément de ta KB ?"))) return;
    setRemoving(item);
    try {
      await api.remove(item.id);
      nav(spaceHome(item.space), { replace: true });
      forgetItem(item.id);
    } catch (e) {
      setRemoving(null);
      setFailed((e as Error).message);
    }
  };
  const reprocess = async () => {
    setMenu(false);
    patchItem(item.id, { status: "pending", error: null });
    try { await api.reprocess(item.id); } catch (e) { setFailed((e as Error).message); }
  };
  const moveTo = async (space: Space) => {
    dropItem(item.id);         // out of the lists of the space it leaves; they reload with it where it now is
    patchItem(item.id, { space, category: space === "main" ? null : item.category });   // Veille: no category
    try { await api.patch(item.id, { space }); } catch (e) { setFailed((e as Error).message); }
  };
  const fileIn = (folderId: string) => update({ folder_id: folderId || null });

  const perso = item.space === "perso";
  const writtenNote = isEditableNote(item);
  const noteText = writtenNote ? (item.content ?? item.input_text ?? "") : "";
  const shown = localized(item);
  const m = item.metadata || {};
  const who = item.author || item.site_name || hostOf(item.source_url);
  const thread: { id: string; url: string }[] = Array.isArray(m.thread) ? m.thread.filter((tw: unknown) => tw && typeof tw === "object") : [];
  const ready = item.status === "ready";
  const content = !writtenNote && item.content ? item.content : "";
  const back = perso ? t("Perso") : t("Veille");
  const tools = [
    { label: item.pinned ? t("Désépingler") : t("Épingler"), run: () => { setMenu(false); update({ pinned: !item.pinned }); } },
    { label: item.archived ? t("Désarchiver") : t("Archiver"), run: () => { setMenu(false); update({ archived: !item.archived }); } },
    { label: t("Retraiter"), run: reprocess },
    { label: t("Supprimer"), run: () => { setMenu(false); remove(); }, danger: true },
  ];
  const dig = () => openInClaude(
    t("Avec le connecteur KB, lis l'élément {id} de ma knowledge base (get_item, contenu complet) "
      + "ainsi que ses éléments liés (get_related), puis aide-moi à creuser « {title} ». "
      + "Commence par me dire en trois points ce qu'il faut en retenir.",
      { id: item.id, title: shown.title ?? t("cet élément") }));

  return (
    <div className="page item-page">
      <div className="item-main">
        <div className="item-top">
          <button className="back" onClick={() => (history.length > 1 ? nav(-1) : nav(spaceHome(item.space)))}>
            <IconBack size={desktop ? 18 : 20} /> {back}
          </button>
          <div className="menu-wrap phone-only" ref={menuRef}>
            <button className="icon-link" style={{ border: 0, background: "none" }} aria-label={t("Plus d'actions")}
                    aria-expanded={menu} onClick={() => setMenu((v) => !v)}><IconMore size={22} /></button>
            {menu && (
              <div className="menu" role="menu">
                {tools.map((x) => <button key={x.label} role="menuitem" className={x.danger ? "danger" : undefined} onClick={x.run}>{x.label}</button>)}
              </div>
            )}
          </div>
        </div>

        <div className="item-meta">
          {!perso && item.kind && <span className="dot" data-kind={item.kind} />}
          <span className="kind">{headLabel(item)}</span>
          {desktop ? (
            <>
              {[!perso && genreLabel(item.genre), who].filter(Boolean).length > 0 && (
                <span>{!perso && genreLabel(item.genre) ? `${genreLabel(item.genre)}${who ? " · " : ""}` : ""}
                  {who && (item.author_url ? <a href={item.author_url} target="_blank" rel="noreferrer">{who}</a> : who)}</span>
              )}
              <span>·</span>
              <span>{writtenNote ? t("écrit le {date}", { date: fullDate(item.created_at) }) : t("sauvé le {date}", { date: fullDate(item.created_at) })}</span>
            </>
          ) : <span>{[who, shortDate(item.created_at)].filter(Boolean).join(" · ")}</span>}
          {item.pinned && <IconPin size={14} className="pin" aria-label={t("Épinglé")} role="img" aria-hidden={false} />}
        </div>

        <h1 className="item-title">{shown.title || item.source_url || t("Sans titre")}</h1>

        {pending && <div className="status-line"><IconSpinner /> {t("Lecture, résumé et indexation en cours…")}</div>}
        {(failed || (error && item)) && <div className="error-box">{failed || error}</div>}
        {item.status === "error" && (
          <div className="error-box">{item.error}
            <div style={{ marginTop: 8 }}><button className="btn xs" onClick={reprocess}>{t("Réessayer")}</button></div>
          </div>
        )}

        {writtenNote && noteText && <div className="note-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(noteText) }} />}
        {!writtenNote && shown.summary && <p className="item-summary">{shown.summary}</p>}

        <div className="item-actions">
          {writtenNote && <Link className="btn primary primary-action" to={`/note/${item.id}/edit`}>{t("Modifier la note")}</Link>}
          {item.source_url && (
            <a className="btn primary primary-action" href={item.source_url} target="_blank" rel="noreferrer"><IconExternal size={18} /> {sourceLabel(item.kind)}</a>
          )}
          {ready && (
            <div className="more">
              {perso
                ? <Link className="btn" to="/ask?mode=advice">{t("Demander conseil")}</Link>
                : <Link className="btn" to={`/ask?about=${item.id}`}>{t("Poser une question")}</Link>}
              <button className="btn" onClick={dig}>{t("Creuser dans Claude")}</button>
              {item.file_url && <a className="btn" href={item.file_url} target="_blank" rel="noreferrer"><IconDownload size={16} /> {item.file_name || t("Fichier original")}</a>}
            </div>
          )}
        </div>

        {item.kind !== "tweet" && <Embed item={item} />}

        {!writtenNote && ready && (
          <label className="stack why-box">
            <span className="label">{t("Pourquoi tu gardes ça ?")} {savedNote && <span className="saved">· {t("Enregistré")}</span>}</span>
            <textarea className="field serif" rows={2} value={note} onChange={(e) => setNote(e.target.value)} onBlur={saveNote}
                      placeholder={desktop ? t("Une phrase suffit — elle aide à le retrouver plus tard.") : t("Une phrase suffit.")} />
          </label>
        )}
        {writtenNote && item.user_note && <p className="why-box hint">{item.user_note}</p>}
        {m.hint && <div className="warn">{tServer(m.hint)}</div>}
        {m.thread_maybe_incomplete && (
          <div className="warn">{t("Ce tweet ouvre peut-être un thread plus long. Pour un thread de plus de 7 jours, partage son dernier tweet : tout ce qui précède sera récupéré.")}</div>
        )}

      </div>

      <div className="item-more">
        {partial && <section className="sec"><Lines n={4} widths={["100%", "96%", "88%", "52%"]} /></section>}
        {writtenNote && shown.summary && (
          <section className="sec"><h2>{t("En bref")}</h2><p className="item-summary" style={{ margin: 0 }}>{shown.summary}</p></section>
        )}
        {shown.key_points?.length > 0 && (
          <section className="sec"><h2>{t("Points clés")}</h2><ul className="points">{shown.key_points.map((p, i) => <li key={i}>{p}</li>)}</ul></section>
        )}
        {content && (
          <section className="sec">
            <h2>{t("Contenu complet")}</h2>
            <div className="content-box">
              <div className="content-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(allContent ? content.slice(0, 120_000) : content.slice(0, PREVIEW)) }} />
              {content.length > PREVIEW && (
                <button className="more-content" onClick={() => setAllContent((v) => !v)}>
                  {allContent ? t("Réduire") : t("… afficher la suite ({n} k caractères)", { n: Math.round(content.length / 1000) })}
                </button>
              )}
            </div>
          </section>
        )}
        {thread.length > 1 && (
          <section className="sec"><h2>{t("Tweets du thread")}</h2>
            <ul className="points">{thread.map((tw, i) => <li key={tw.id}><a href={tw.url} target="_blank" rel="noreferrer">{t("Tweet {i} sur {n}", { i: i + 1, n: thread.length })}</a></li>)}</ul>
          </section>
        )}
      </div>

      <aside className="item-aside">
        {shown.use_cases?.length > 0 && (
          <section className="aside-sec"><h2 className="mini-h">{t("Utile pour")}</h2>
            {shown.use_cases.map((u, i) => <p key={i} className="use-for">{u}</p>)}
          </section>
        )}
        {item.actions?.length > 0 && (
          <section className="aside-sec"><h2 className="mini-h">{t("À faire")}</h2>
            {item.actions.map((a) => (
              <label key={a.id} className="check-card">
                <input type="checkbox" checked={a.done} onChange={(e) => toggleAction(a.id, e.target.checked)} />
                <span className={a.done ? "done" : ""}>{a.text}</span>
              </label>
            ))}
          </section>
        )}
        {item.links?.length > 0 && (
          <section className="aside-sec"><h2 className="mini-h">{t("Liés dans ta KB")}</h2>
            {item.links.map((l) => (
              <Link key={l.id} to={`/item/${l.id}`} className="related"><span className="t">{l.title}</span><span className="r">{l.reason}</span></Link>
            ))}
          </section>
        )}
        {item.entities?.length > 0 && (
          <section className="aside-sec"><h2 className="mini-h">{t("Personnes, outils, concepts")}</h2>
            <div className="pills">{item.entities.map((e) => <Link key={e.name} className="pill" to={`${spaceHome(item.space)}?entity=${encodeURIComponent(e.name)}`}>{e.name}</Link>)}</div>
          </section>
        )}
        <section className="aside-sec"><h2 className="mini-h">{t("Tags")}</h2>
          <div className="pills">
            {item.tags.map((tg) => (
              <span key={tg} className="pill">#{tg}
                <button type="button" className="icon-btn x" onClick={() => removeTag(tg)}
                        aria-label={t("Retirer {tag}", { tag: tg })}>×</button></span>
            ))}
            {tagInput === null
              ? <button type="button" className="pill add" onClick={() => setTagInput("")}>{t("+ tag")}</button>
              : <span className="pill"><input autoFocus aria-label={t("Ajouter un tag")} placeholder={t("nouveau tag")} value={tagInput}
                                              onChange={(e) => setTagInput(e.target.value)} onBlur={addTag}
                                              onKeyDown={(e) => { if (e.key === "Enter") addTag(); if (e.key === "Escape") setTagInput(null); }} /></span>}
          </div>
        </section>
        <section className="aside-sec placement"><h2 className="mini-h">{t("Rangement")}</h2>
          <label className="stack">
            <span className="hint">{t("Dossier")}</span>
            <select className="field" value={item.folder_id ?? ""} onChange={(e) => fileIn(e.target.value)} aria-label={t("Dossier")}>
              <option value="">{t("Aucun dossier")}</option>
              {folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </label>
          <div className="seg sm" role="group" aria-label={t("Espace")}>
            <button type="button" aria-pressed={!perso} onClick={() => perso && moveTo("main")}>{t("Veille")}</button>
            <button type="button" aria-pressed={perso} onClick={() => !perso && moveTo("perso")}>{t("Perso")}</button>
          </div>
          {perso && (
            <select className="field" value={item.category ?? ""} onChange={(e) => update({ category: e.target.value || null })} aria-label={t("Catégorie")}>
              <option value="">{t("Sans catégorie")}</option>
              {CATEGORIES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          )}
        </section>
        <div className="item-tools desk-only">
          {tools.map((x) => <button key={x.label} type="button" className={x.danger ? "danger" : undefined} onClick={x.run}>{x.label}</button>)}
        </div>
      </aside>
    </div>
  );
}
