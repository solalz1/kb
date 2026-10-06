import { ArrowLeft, Loader2, Lock } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type ItemDetail, type Space } from "../api";
import { CATEGORIES, categoryOf, isEditableNote, spaceHome } from "../perso";

const DRAFT_KEY = "kb_note_draft";

interface Draft { title: string; content: string; space: Space; category: string; tags: string }

function readDraft(): Draft | null {
  try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || "null"); } catch { return null; }
}
function writeDraft(d: Draft | null) {
  try { d ? localStorage.setItem(DRAFT_KEY, JSON.stringify(d)) : localStorage.removeItem(DRAFT_KEY); } catch { /* stockage indisponible */ }
}

/** Écrire (ou réécrire) une note : principe, valeur, leçon, journal… ou une simple note de veille. */
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

  useEffect(() => {
    if (!id) return;
    api.item(id).then((it) => {
      if (!isEditableNote(it)) { setError("Seules les notes écrites se modifient ici. Pour un lien ou un fichier, change le titre, la note ou les tags depuis sa fiche."); return; }
      setOriginal(it);
      setForm({ title: it.title ?? "", content: it.content ?? it.input_text ?? "", space: it.space,
                category: it.category ?? "", tags: "" });
    }).catch((e) => setError(e.message));
  }, [id]);

  // brouillon gardé sur l'appareil tant que la nouvelle note n'est pas enregistrée
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
    if (!form.content.trim()) { setError("Écris quelque chose avant d'enregistrer."); return; }
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
        const tags = form.tags.split(",").map((t) => t.trim()).filter(Boolean);
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
  // « Retour » garde le brouillon sur l'appareil ; « Abandonner » l'efface (après confirmation)
  const discard = () => {
    if (!editing && form.content.trim() && !window.confirm("Effacer ce brouillon ?")) return;
    if (!editing) writeDraft(null);
    leave();
  };

  if (editing && !original && !error) {
    return <div className="page"><div className="status-line"><Loader2 size={16} className="spin" /> Chargement…</div></div>;
  }

  return (
    <div className="page">
      <button className="back" onClick={editing ? discard : leave}><ArrowLeft size={16} /> {editing ? "Annuler" : "Retour"}</button>
      <h1 className="title">{editing ? "Modifier la note" : cat ? `Nouvelle note · ${cat.label}` : "Nouvelle note"}</h1>

      {error && <div className="error-box">{error}</div>}
      {restored && (
        <div className="warn" style={{ marginBottom: 14 }}>
          Brouillon restauré.{" "}
          <button type="button" className="linkish" onClick={() => { setForm(fresh()); setRestored(false); }}>Repartir d'une page blanche</button>
        </div>
      )}
      {(!editing || original) && (
        <form onSubmit={save} className="fiche sheet note-editor" data-kind="note" data-space={perso ? "perso" : undefined}>
          <div className="fiche-head">
            <div className="modes small" role="group" aria-label="Espace">
              <button type="button" aria-pressed={perso} onClick={() => set("space", "perso")}>Perso</button>
              <button type="button" aria-pressed={!perso} onClick={() => set("space", "main")}>Veille</button>
            </div>
            <span className="when"><Lock size={13} /> privée</span>
          </div>

          {perso && (
            <div className="chips wrap" role="group" aria-label="Catégorie">
              {CATEGORIES.map((c) => (
                <button type="button" key={c.id} className="chip" aria-pressed={form.category === c.id}
                        onClick={() => set("category", form.category === c.id ? "" : c.id)}>{c.label}</button>
              ))}
            </div>
          )}
          {perso && !form.category && <div className="hint" style={{ marginTop: 0 }}>Sans catégorie, Claude en choisit une à l'enregistrement.</div>}

          <label className="sr-only" htmlFor="note-title">Titre</label>
          <input id="note-title" className="title-input" value={form.title} onChange={(e) => set("title", e.target.value)}
                 placeholder={editing ? "Titre" : "Titre (facultatif, sinon Claude en propose un)"} maxLength={300} />

          <label className="sr-only" htmlFor="note-body">Texte</label>
          <textarea id="note-body" ref={box} className="note-body" value={form.content} onChange={(e) => set("content", e.target.value)}
                    placeholder={perso ? (cat?.placeholder ?? "Écris librement : une idée, une leçon, un objectif, une réflexion…")
                                       : "Une idée, un compte rendu, une citation…"} autoFocus={!editing} />

          {cat?.charter && (
            <p className="hint">Tes {cat.plural.toLowerCase()} font partie de ta charte : le mode Conseil les relit en entier à chaque question. Écris-les comme tu te les dirais.</p>
          )}

          {!editing && (
            <>
              <label className="lbl" htmlFor="note-tags">Tags <span className="muted">(facultatif, séparés par des virgules)</span></label>
              <input id="note-tags" className="field" value={form.tags} onChange={(e) => set("tags", e.target.value)} placeholder="famille, travail, santé" />
            </>
          )}

          <div className="editor-actions">
            <button className="btn primary" type="submit" disabled={busy || !form.content.trim()}>
              {busy ? <Loader2 size={16} className="spin" /> : null} Enregistrer
            </button>
            <button className="btn ghost" type="button" onClick={discard}>{editing ? "Annuler" : "Abandonner"}</button>
            <span className="hint" style={{ margin: 0 }}>
              {editing ? "Résumé, tags et liens sont refaits après l'enregistrement." : "Ton texte est gardé tel quel ; Claude ajoute résumé, tags et liens."}
            </span>
          </div>
        </form>
      )}
    </div>
  );
}
