import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, type ItemDetail, type Space } from "../api";
import { cache } from "../cache";
import { PageSkeleton } from "../components/Skeleton";
import { t } from "../i18n";
import { IconBack, IconLock, IconSpinner } from "../icons";
import { useNavigate } from "../nav";
import { CATEGORIES, categoryOf, isEditableNote, spaceHome } from "../perso";
import { keys } from "../queries";

const DRAFT_KEY = "kb_note_draft";

interface Draft { title: string; content: string; space: Space; category: string; tags: string }

function readDraft(): Draft | null {
  try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || "null"); } catch { return null; }
}
function writeDraft(d: Draft | null) {
  try { d ? localStorage.setItem(DRAFT_KEY, JSON.stringify(d)) : localStorage.removeItem(DRAFT_KEY); } catch { /* storage unavailable */ }
}

/** Write (or rewrite) a note: a principle, a value, a lesson, the journal… or a plain note in Veille. */
export default function NoteEditor() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const editing = Boolean(id);

  const draft = editing ? null : readDraft();
  const hasDraft = Boolean(draft && draft.content.trim());
  const fresh = (): Draft => {
    const cat = params.get("category");
    const space = (params.get("space") === "main" ? "main" : "perso") as Space;
    return { title: "", content: "", space: cat ? "perso" : space, category: cat && categoryOf(cat) ? cat : "", tags: "" };
  };
  const [form, setForm] = useState<Draft>(() => (hasDraft ? draft! : fresh()));
  const [restored, setRestored] = useState(hasDraft);
  const [original, setOriginal] = useState<ItemDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);

  // the note as its page just had it, or else as the server has it now (without counting a visit)
  useEffect(() => {
    if (!id) return;
    const cached = cache.fresh(keys.item(id), 10_000) ? cache.peek<ItemDetail>(keys.item(id)) : undefined;
    (cached ? Promise.resolve(cached) : api.item(id, false)).then((it) => {
      if (!isEditableNote(it)) { setError(t("Seules les notes écrites se modifient ici. Pour un lien ou un fichier, change le titre, la note ou les tags depuis sa fiche.")); return; }
      setOriginal(it);
      setForm({ title: it.title ?? "", content: it.content ?? it.input_text ?? "", space: it.space, category: it.category ?? "", tags: "" });
    }).catch((e) => setError(e.message));
  }, [id]);

  // the draft stays on the device until the new note is saved
  useEffect(() => { if (!editing) writeDraft(form); }, [form, editing]);

  useEffect(() => {
    const el = box.current;
    if (el) { el.style.height = "auto"; el.style.height = `${Math.max(240, el.scrollHeight)}px`; }
  }, [form.content]);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => { setForm((f) => ({ ...f, [key]: value })); setError(""); };
  const cat = categoryOf(form.category);
  const perso = form.space === "perso";

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.content.trim()) { setError(t("Écris quelque chose avant d'enregistrer.")); return; }
    setBusy(true);
    try {
      if (editing && original) {
        const body: Parameters<typeof api.patch>[1] = {};
        if (form.title.trim() !== (original.title ?? "")) body.title = form.title.trim();
        if (form.content.trim() !== (original.content ?? original.input_text ?? "").trim()) body.content = form.content.trim();
        if (form.space !== original.space) body.space = form.space;
        if ((form.category || null) !== (original.category || null)) body.category = form.category || null;
        if (Object.keys(body).length) await api.patch(original.id, body);
        nav(`/item/${original.id}`, { replace: true });
      } else {
        const tags = form.tags.split(",").map((x) => x.trim()).filter(Boolean);
        const res = await api.createNote({ content: form.content.trim(), title: form.title.trim() || undefined,
                                           space: form.space, category: perso ? form.category || null : null, tags });
        writeDraft(null);
        nav(`/item/${res.id}`, { replace: true });
      }
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  const leave = () => { if (history.length > 1) nav(-1); else nav(spaceHome(form.space)); };
  // going back keeps the draft on the device; "Abandonner" erases it (after asking)
  const discard = () => {
    if (!editing && form.content.trim() && !window.confirm(t("Effacer ce brouillon ?"))) return;
    if (!editing) writeDraft(null);
    leave();
  };

  if (editing && !original && !error) {
    return <div className="page"><PageSkeleton /></div>;
  }

  return (
    <div className="page tight">
      <button className="back" onClick={editing ? discard : leave}><IconBack size={18} /> {perso ? t("Perso") : t("Veille")}</button>
      <h1 className="title">{editing ? t("Modifier la note") : t("Nouvelle note")}</h1>

      {error && <div className="error-box">{error}</div>}
      {restored && (
        <div className="warn">
          {t("Brouillon restauré.")}{" "}
          <button type="button" className="linkish" onClick={() => { setForm(fresh()); setRestored(false); }}>{t("Repartir d'une page blanche")}</button>
        </div>
      )}
      {(!editing || original) && (
        <form onSubmit={save} className="column" style={{ gap: 16 }}>
          <div className="inline-row">
            <div className="seg" role="group" aria-label={t("Espace")}>
              <button type="button" aria-pressed={perso} onClick={() => set("space", "perso")}>{t("Perso")}</button>
              <button type="button" aria-pressed={!perso} onClick={() => set("space", "main")}>{t("Veille")}</button>
            </div>
            <span style={{ flex: 1 }} />
            <span className="privacy"><IconLock size={14} /> {t("privée")}</span>
          </div>

          {perso && (
            <div className="chips" role="group" aria-label={t("Catégorie")}>
              {CATEGORIES.map((c) => (
                <button type="button" key={c.id} className="chip sm" aria-pressed={form.category === c.id}
                        onClick={() => set("category", form.category === c.id ? "" : c.id)}>{c.label}</button>
              ))}
            </div>
          )}

          <div className={`editor-sheet${perso ? "" : " main-space"}`}>
            <label className="sr-only" htmlFor="note-title">{t("Titre")}</label>
            <input id="note-title" className="title-input" value={form.title} onChange={(e) => set("title", e.target.value)}
                   placeholder={editing ? t("Titre") : t("Titre (facultatif, sinon Claude en propose un)")} maxLength={300} />
            <label className="sr-only" htmlFor="note-body">{t("Texte")}</label>
            <textarea id="note-body" ref={box} className="note-body" rows={9} value={form.content} onChange={(e) => set("content", e.target.value)}
                      placeholder={perso ? (cat?.placeholder ?? t("Écris librement : une idée, une leçon, un objectif, une réflexion…"))
                                         : t("Une idée, un compte rendu, une citation…")} autoFocus={!editing} />
            {cat?.charter && (
              <p className="hint">{t("Tes {category} font partie de ta charte : le mode Conseil les relit en entier à chaque question. Écris-les comme tu te les dirais.", { category: cat.plural.toLowerCase() })}</p>
            )}
            {perso && !form.category && <p className="hint">{t("Sans catégorie, Claude en choisit une à l'enregistrement.")}</p>}
          </div>

          {!editing && (
            <label className="stack">
              <span className="label">{t("Tags")} <span className="opt">{t("facultatif, séparés par des virgules")}</span></span>
              <input className="field" value={form.tags} onChange={(e) => set("tags", e.target.value)} placeholder={t("famille, travail, santé")} />
            </label>
          )}

          <div className="editor-actions">
            <button className="btn primary" type="submit" disabled={busy}>{busy && <IconSpinner size={15} />} {t("Enregistrer")}</button>
            <button className="btn ghost" type="button" onClick={discard}>{editing ? t("Annuler") : t("Abandonner")}</button>
            <span className="hint right">
              {editing ? t("Résumé, tags et liens sont refaits après l'enregistrement.") : t("Ton texte est gardé tel quel ; Claude ajoute résumé, tags et liens.")}
            </span>
          </div>
        </form>
      )}
    </div>
  );
}
