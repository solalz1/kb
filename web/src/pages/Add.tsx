import { Check, Loader2, NotebookPen, Paperclip } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Space } from "../api";
import { CATEGORIES } from "../perso";

export default function Add() {
  const [params] = useSearchParams();
  const [value, setValue] = useState(params.get("url") || params.get("text") || "");
  const [note, setNote] = useState("");
  const [space, setSpace] = useState<Space>(params.get("space") === "perso" ? "perso" : "main");
  const [category, setCategory] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ message: string; id: string } | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(null), 5000);
    return () => clearTimeout(t);
  }, [done]);

  // coller une image ou un fichier directement (Cmd+V sur Mac)
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const pasted = Array.from(e.clipboardData?.files ?? []);
      if (pasted.length) { e.preventDefault(); setFiles((f) => [...f, ...pasted]); }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!value.trim() && files.length === 0) {
      setError("Colle un lien, écris une note ou joins un fichier.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      let res;
      // Veille : rien d'imposé, un #perso dans la note peut encore ranger l'élément dans Perso
      const where: { space?: "perso"; category?: string } = space === "perso" ? { space, ...(category ? { category } : {}) } : {};
      if (files.length) {
        const form = new FormData();
        files.forEach((f) => form.append("file", f, f.name));
        if (note.trim()) form.append("note", note.trim());
        if (value.trim()) form.append("text", value.trim());
        Object.entries(where).forEach(([k, v]) => form.append(k, v));
        res = await api.ingest(form);
      } else {
        const text = value.trim();
        const isUrl = /^https?:\/\/\S+$/i.test(text);
        res = await api.ingest(isUrl ? { url: text, note, ...where } : { text, note, ...where });
      }
      setDone({ message: res.message, id: res.id });
      setValue(""); setNote(""); setFiles([]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <div className="space-head">
        <h1 className="title">Ajouter à ta KB</h1>
        <Link className="btn small" to={`/note/new${space === "perso" ? "" : "?space=main"}`}><NotebookPen size={15} /> Écrire une note longue</Link>
      </div>
      <form onSubmit={submit}>
        <label className="lbl" htmlFor="what">Lien ou note</label>
        <textarea id="what" className="field" value={value} onChange={(e) => { setValue(e.target.value); setError(""); }}
                  placeholder="https://x.com/… ou une idée, une citation, un compte rendu…" />

        <div className={`drop${over ? " over" : ""}`} style={{ marginTop: 12 }}
             onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
             onDrop={(e) => { e.preventDefault(); setOver(false); setFiles((f) => [...f, ...Array.from(e.dataTransfer.files)]); }}>
          <button type="button" className="btn small" onClick={() => picker.current?.click()}><Paperclip size={15} /> Joindre des fichiers</button>
          <div className="hint">PDF, captures d'écran, photos, audio, vidéo, Word, PowerPoint, Excel… Tu peux aussi glisser-déposer ou coller.</div>
          <input ref={picker} type="file" multiple onChange={(e) => setFiles((f) => [...f, ...Array.from(e.target.files ?? [])])} />
          {files.length > 0 && (
            <div className="files">
              {files.map((f, i) => (
                <div key={i}>{f.name} ({Math.max(1, Math.round(f.size / 1024))} Ko)
                  <button type="button" className="btn small ghost" onClick={() => setFiles(files.filter((_, j) => j !== i))}>Retirer</button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="segmented-row">
          <span className="muted">Ranger dans</span>
          <div className="modes small" role="group" aria-label="Espace">
            <button type="button" aria-pressed={space === "main"} onClick={() => setSpace("main")}>Veille</button>
            <button type="button" aria-pressed={space === "perso"} onClick={() => setSpace("perso")}>Perso</button>
          </div>
        </div>
        {space === "perso" && (
          <div className="chips wrap" role="group" aria-label="Catégorie" style={{ marginTop: 10 }}>
            {CATEGORIES.map((c) => (
              <button type="button" key={c.id} className="chip" aria-pressed={category === c.id}
                      onClick={() => setCategory(category === c.id ? "" : c.id)}>{c.label}</button>
            ))}
          </div>
        )}

        <label className="lbl" htmlFor="note">Pourquoi tu gardes ça ? <span className="muted">(facultatif)</span></label>
        <input id="note" className="field" value={note} onChange={(e) => setNote(e.target.value)}
               placeholder="ex. pour mon projet d'agent, idée de feature, à relire avant l'entretien…" />
        <div className="hint">Cette phrase oriente le résumé et aide à retrouver l'élément plus tard.</div>

        {error && <div className="error-box">{error}</div>}
        <div style={{ marginTop: 20 }}>
          <button className="btn primary" type="submit" disabled={busy}>
            {busy ? <Loader2 size={16} className="spin" /> : null} Ajouter à la KB
          </button>
        </div>
      </form>

      <p className="hint" style={{ marginTop: 36 }}>
        Sur iPhone et Mac, le plus rapide reste le bouton Partager : installe le Raccourci « Ajouter à ma KB » (voir <Link to="/settings">Réglages</Link>).
        Ajoute <code>#perso</code> (et par exemple <code>#leçon</code>) dans ta note pour ranger l'élément dans l'espace Perso.
      </p>

      {done && (
        <div className="toast" role="status">
          <Check size={16} /> {done.message} <Link to={`/item/${done.id}`} style={{ color: "inherit" }}>Voir</Link>
        </div>
      )}
    </div>
  );
}
