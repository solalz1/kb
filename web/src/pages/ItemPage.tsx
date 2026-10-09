import { AlertTriangle, Archive, ArrowLeft, Compass, Download, ExternalLink, Loader2, MessageSquare, PenLine, Pin, RefreshCw, SquareArrowOutUpRight, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ItemDetail, type Space } from "../api";
import { openInClaude } from "../claude";
import { Embed } from "../components/Embed";
import { localized, t, tServer } from "../i18n";
import { fullDate, genreLabel, hostOf, sourceLabel } from "../kinds";
import { renderMarkdown } from "../markdown";
import { CATEGORIES, headLabel, isEditableNote, spaceHome } from "../perso";

export default function ItemPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [item, setItem] = useState<ItemDetail | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [editingNote, setEditingNote] = useState(false);
  const [tagInput, setTagInput] = useState("");

  const load = () =>
    api.item(id).then((it) => { setItem(it); setNote(it.user_note ?? ""); setError(""); }).catch((e) => setError(e.message));

  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const pending = item && (item.status === "pending" || item.status === "processing");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [pending]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <div className="page"><div className="error-box">{error}</div></div>;
  if (!item) return <div className="page"><div className="status-line"><Loader2 size={16} className="spin" /> {t("Chargement…")}</div></div>;

  const update = async (body: Parameters<typeof api.patch>[1]) => {
    await api.patch(item.id, body);
    setItem({ ...item, ...body } as ItemDetail);
  };
  const saveNote = async () => { await update({ user_note: note.trim() }); setEditingNote(false); };
  const addTag = async () => {
    const t = tagInput.trim().toLowerCase().replace(/^#/, "").replace(/\s+/g, "-");
    if (!t || item.tags.includes(t)) return setTagInput("");
    await update({ tags: [...item.tags, t] });
    setTagInput("");
  };
  const removeTag = (t: string) => update({ tags: item.tags.filter((x) => x !== t) });
  const toggleAction = async (aid: number, done: boolean) => {
    await api.setAction(aid, done);
    setItem({ ...item, actions: item.actions.map((a) => (a.id === aid ? { ...a, done } : a)) });
  };
  const remove = async () => {
    if (!window.confirm(t("Supprimer définitivement cet élément de ta KB ?"))) return;
    await api.remove(item.id);
    nav(spaceHome(item.space), { replace: true });
  };
  const reprocess = async () => { await api.reprocess(item.id); load(); };
  const moveTo = async (space: Space) => {
    await api.patch(item.id, { space });
    setItem({ ...item, space, category: space === "main" ? null : item.category });   // Veille : pas de catégorie
  };
  const perso = item.space === "perso";
  const writtenNote = isEditableNote(item);
  const noteText = writtenNote ? (item.content ?? item.input_text ?? "") : "";
  const shown = localized(item);

  const m = item.metadata || {};
  const who = item.author || item.site_name || hostOf(item.source_url);
  const thread: { id: string; url: string }[] = Array.isArray(m.thread) ? m.thread.filter((tw: unknown) => tw && typeof tw === "object") : [];
  const details = [
    item.published_at && t("publié le {date}", { date: fullDate(item.published_at) }),
    m.pages && (m.pages > 1 ? t("{n} pages", { n: m.pages }) : t("{n} page", { n: m.pages })),
    m.duration && `${Math.max(1, Math.round(m.duration / 60))} min`,
  ].filter(Boolean) as string[];

  return (
    <div className="page">
      <button className="back" onClick={() => (history.length > 1 ? nav(-1) : nav(spaceHome(item.space)))}><ArrowLeft size={16} /> {t("Retour")}</button>

      <article className="fiche sheet" data-kind={item.kind ?? undefined} data-space={perso ? "perso" : undefined}>
        <div className="fiche-head">
          {!perso && item.kind && <span className="dot" data-kind={item.kind} />}
          <span className="kind">{headLabel(item)}</span>
          {!perso && genreLabel(item.genre) && <span>{genreLabel(item.genre)}</span>}
          <span className="when">{writtenNote ? t("écrit le {date}", { date: fullDate(item.created_at) }) : t("sauvé le {date}", { date: fullDate(item.created_at) })}</span>
        </div>
        <h1>{shown.title || item.source_url || t("Sans titre")}</h1>
        <div className="byline">
          {item.author_url ? <a href={item.author_url} target="_blank" rel="noreferrer">{who}</a> : who}
          {details.length > 0 && <>{who ? ", " : ""}{details.join(", ")}</>}
        </div>

        {pending && <div className="status-line"><Loader2 size={16} className="spin" /> {t("Lecture, résumé et indexation en cours…")}</div>}
        {item.status === "error" && (
          <div className="error-box"><AlertTriangle size={15} /> {item.error}
            <div style={{ marginTop: 8 }}><button className="btn small" onClick={reprocess}><RefreshCw size={14} /> {t("Réessayer")}</button></div>
          </div>
        )}

        {writtenNote && noteText && (
          <div className="content-md note-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(noteText) }} />
        )}
        {shown.summary && !writtenNote && <p className="ruled">{shown.summary}</p>}

        {!editingNote && item.user_note && (
          <p className="why" onClick={() => setEditingNote(true)} title={t("Modifier")}>{item.user_note}</p>
        )}
        {(editingNote || (!item.user_note && !writtenNote)) && item.status === "ready" && (
          <div className="why-edit">
            <label className="sr-only" htmlFor="why">{t("Pourquoi tu gardes ça ?")}</label>
            <textarea id="why" className="field" style={{ minHeight: 60 }} placeholder={t("Pourquoi tu gardes ça ? (ça aide à le retrouver plus tard)")}
                      value={note} onChange={(e) => setNote(e.target.value)} />
            {(note !== (item.user_note ?? "")) && <button className="btn small primary" style={{ marginTop: 8 }} onClick={saveNote}>{t("Enregistrer la note")}</button>}
          </div>
        )}

        <div className="source-row">
          {writtenNote && (
            <Link className="btn primary" to={`/note/${item.id}/edit`}><PenLine size={16} /> {t("Modifier la note")}</Link>
          )}
          {item.source_url && (
            <a className="btn primary" href={item.source_url} target="_blank" rel="noreferrer"><ExternalLink size={16} /> {sourceLabel(item.kind)}</a>
          )}
          {item.file_url && (
            <a className="btn" href={item.file_url} target="_blank" rel="noreferrer"><Download size={16} /> {item.file_name || t("Fichier original")}</a>
          )}
          {item.status === "ready" && !perso && (
            <Link className="btn" to={`/ask?about=${item.id}`}><MessageSquare size={16} /> {t("Poser une question")}</Link>
          )}
          {item.status === "ready" && perso && (
            <Link className="btn" to="/ask?mode=advice"><Compass size={16} /> {t("Demander conseil")}</Link>
          )}
          {item.status === "ready" && (
            <button className="btn" onClick={() => openInClaude(
              t("Avec le connecteur KB, lis l'élément {id} de ma knowledge base (get_item, contenu complet) "
                + "ainsi que ses éléments liés (get_related), puis aide-moi à creuser « {title} ». "
                + "Commence par me dire en trois points ce qu'il faut en retenir.",
                { id: item.id, title: shown.title ?? t("cet élément") }))}>
              <SquareArrowOutUpRight size={16} /> {t("Creuser dans Claude")}
            </button>
          )}
        </div>
        <div className="placement">
          <div className="modes small" role="group" aria-label={t("Espace")}>
            <button aria-pressed={!perso} onClick={() => perso && moveTo("main")}>{t("Veille")}</button>
            <button aria-pressed={perso} onClick={() => !perso && moveTo("perso")}>{t("Perso")}</button>
          </div>
          {perso && (
            <label className="model-pick">
              <span>{t("Catégorie")}</span>
              <select value={item.category ?? ""} onChange={(e) => update({ category: e.target.value || null })}>
                <option value="">{t("Aucune")}</option>
                {CATEGORIES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
              </select>
            </label>
          )}
        </div>
        {m.hint && <div className="warn">{tServer(m.hint)}</div>}
        {m.thread_maybe_incomplete && (
          <div className="warn">{t("Ce tweet ouvre peut-être un thread plus long. Pour un thread de plus de 7 jours, partage son dernier tweet : tout ce qui précède sera récupéré.")}</div>
        )}
      </article>

      <Embed item={item} />

      {writtenNote && shown.summary && (
        <section className="section"><h2>{t("En bref")}</h2><p className="ruled summary-ruled">{shown.summary}</p></section>
      )}
      {shown.key_points?.length > 0 && (
        <section className="section"><h2>{t("Points clés")}</h2><ul>{shown.key_points.map((p, i) => <li key={i}>{p}</li>)}</ul></section>
      )}
      {shown.use_cases?.length > 0 && (
        <section className="section"><h2>{t("Utile pour")}</h2><ul>{shown.use_cases.map((p, i) => <li key={i}>{p}</li>)}</ul></section>
      )}
      {item.actions?.length > 0 && (
        <section className="section"><h2>{t("À faire")}</h2>
          <ul className="todo-list">
            {item.actions.map((a) => (
              <li key={a.id}>
                <input type="checkbox" checked={a.done} onChange={(e) => toggleAction(a.id, e.target.checked)} aria-label={a.text} />
                <span className={a.done ? "done" : ""}>{a.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {item.links?.length > 0 && (
        <section className="section links"><h2>{t("Liés dans ta KB")}</h2>
          {item.links.map((l) => (
            <Link key={l.id} to={`/item/${l.id}`}>
              <span className="dot" data-kind={l.kind} />
              <span><span className="t">{l.title}</span><span className="reason">{l.reason}</span></span>
            </Link>
          ))}
        </section>
      )}
      {thread.length > 1 && (
        <section className="section"><h2>{t("Tweets du thread")}</h2>
          <ul>{thread.map((tw, i) => <li key={tw.id}><a href={tw.url} target="_blank" rel="noreferrer">{t("Tweet {i} sur {n}", { i: i + 1, n: thread.length })}</a></li>)}</ul>
        </section>
      )}
      {item.entities?.length > 0 && (
        <section className="section"><h2>{t("Personnes, outils, concepts")}</h2>
          <div className="entities">{item.entities.map((e) => <Link key={e.name} to={`${spaceHome(item.space)}?entity=${encodeURIComponent(e.name)}`}>{e.name}</Link>)}</div>
        </section>
      )}
      <section className="section"><h2>{t("Tags")}</h2>
        <div className="tag-editor">
          {item.tags.map((tg) => (
            <span key={tg} className="chip">#{tg}<button className="x" style={{ border: 0, background: "none", padding: 0 }} onClick={() => removeTag(tg)} aria-label={t("Retirer {tag}", { tag: tg })}>×</button></span>
          ))}
          <input placeholder={t("Ajouter un tag")} value={tagInput} onChange={(e) => setTagInput(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && addTag()} onBlur={addTag} />
        </div>
      </section>
      {item.content && !writtenNote && (
        <section className="section">
          <details>
            <summary>{item.content.length >= 2000 ? t("Contenu complet ({n} k caractères)", { n: Math.round(item.content.length / 1000) }) : t("Contenu complet")}</summary>
            <div className="content-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(item.content.slice(0, 120_000)) }} />
          </details>
        </section>
      )}

      <div className="toolbar">
        <button className="btn ghost" onClick={() => update({ pinned: !item.pinned })}><Pin size={16} /> {item.pinned ? t("Désépingler") : t("Épingler")}</button>
        <button className="btn ghost" onClick={() => update({ archived: !item.archived })}><Archive size={16} /> {item.archived ? t("Désarchiver") : t("Archiver")}</button>
        <button className="btn ghost" onClick={reprocess}><RefreshCw size={16} /> {t("Retraiter")}</button>
        <button className="btn ghost danger" onClick={remove}><Trash2 size={16} /> {t("Supprimer")}</button>
      </div>
    </div>
  );
}
