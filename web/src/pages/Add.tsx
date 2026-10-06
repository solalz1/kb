import { Check, Loader2, NotebookPen, Paperclip } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Space } from "../api";
import { t, tServer } from "../i18n";
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
  const [progress, setProgress] = useState<number | null>(null);   // upload fraction, null when not uploading
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
      setError(t("Colle un lien, écris une note ou joins un fichier."));
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
        setProgress(0);
        res = await api.uploadFiles(form, setProgress);
      } else {
        const text = value.trim();
        const isUrl = /^https?:\/\/\S+$/i.test(text);
        res = await api.ingest(isUrl ? { url: text, note, ...where } : { text, note, ...where });
      }
      setDone({ message: tServer(res.message), id: res.id });
      setValue(""); setNote(""); setFiles([]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  return (
    <div className="page">
      <div className="space-head">
        <h1 className="title">{t("Ajouter à ta KB")}</h1>
        <Link className="btn small" to={`/note/new${space === "perso" ? "" : "?space=main"}`}><NotebookPen size={15} /> {t("Écrire une note longue")}</Link>
      </div>
      <form onSubmit={submit}>
        <label className="lbl" htmlFor="what">{t("Lien ou note")}</label>
        <textarea id="what" className="field" value={value} onChange={(e) => { setValue(e.target.value); setError(""); }}
                  placeholder={t("https://x.com/… ou une idée, une citation, un compte rendu…")} />

        <div className={`drop${over ? " over" : ""}`} style={{ marginTop: 12 }}
             onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
             onDrop={(e) => { e.preventDefault(); setOver(false); setFiles((f) => [...f, ...Array.from(e.dataTransfer.files)]); }}>
          <button type="button" className="btn small" onClick={() => picker.current?.click()}><Paperclip size={15} /> {t("Joindre des fichiers")}</button>
          <div className="hint">{t("PDF, captures d'écran, photos, audio, vidéo, Word, PowerPoint, Excel… Tu peux aussi glisser-déposer ou coller.")}</div>
          <input ref={picker} type="file" multiple onChange={(e) => setFiles((f) => [...f, ...Array.from(e.target.files ?? [])])} />
          {files.length > 0 && (
            <div className="files">
              {files.map((f, i) => (
                <div key={i}>{f.name} ({Math.max(1, Math.round(f.size / 1024))} {t("Ko")})
                  <button type="button" className="btn small ghost" onClick={() => setFiles(files.filter((_, j) => j !== i))}>{t("Retirer")}</button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="segmented-row">
          <span className="muted">{t("Ranger dans")}</span>
          <div className="modes small" role="group" aria-label={t("Espace")}>
            <button type="button" aria-pressed={space === "main"} onClick={() => setSpace("main")}>{t("Veille")}</button>
            <button type="button" aria-pressed={space === "perso"} onClick={() => setSpace("perso")}>{t("Perso")}</button>
          </div>
        </div>
        {space === "perso" && (
          <div className="chips wrap" role="group" aria-label={t("Catégorie")} style={{ marginTop: 10 }}>
            {CATEGORIES.map((c) => (
              <button type="button" key={c.id} className="chip" aria-pressed={category === c.id}
                      onClick={() => setCategory(category === c.id ? "" : c.id)}>{c.label}</button>
            ))}
          </div>
        )}

        <label className="lbl" htmlFor="note">{t("Pourquoi tu gardes ça ?")} <span className="muted">{t("(facultatif)")}</span></label>
        <input id="note" className="field" value={note} onChange={(e) => setNote(e.target.value)}
               placeholder={t("ex. pour mon projet d'agent, idée de feature, à relire avant l'entretien…")} />
        <div className="hint">{t("Cette phrase oriente le résumé et aide à retrouver l'élément plus tard.")}</div>

        {error && <div className="error-box">{error}</div>}
        <div style={{ marginTop: 20 }}>
          <button className="btn primary" type="submit" disabled={busy}>
            {busy ? <Loader2 size={16} className="spin" /> : null}{" "}
            {progress === null ? t("Ajouter à la KB") : progress < 1 ? t("Envoi… {pct} %", { pct: Math.round(progress * 100) }) : t("Enregistrement…")}
          </button>
          {progress !== null && (
            <div className="upload-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100}
                 aria-valuenow={Math.round(progress * 100)}><span style={{ width: `${progress * 100}%` }} /></div>
          )}
          {progress !== null && progress < 1 && <div className="hint">{t("Garde l'app ouverte jusqu'à la fin de l'envoi.")}</div>}
        </div>
      </form>

      <p className="hint" style={{ marginTop: 36 }}>
        {t("Sur iPhone et Mac, le plus rapide reste le bouton Partager : installe les Raccourcis (voir")} <Link to="/settings">{t("Réglages")}</Link>).
      </p>

      {done && (
        <div className="toast" role="status">
          <Check size={16} /> {done.message} <Link to={`/item/${done.id}`} style={{ color: "inherit" }}>{t("Voir")}</Link>
        </div>
      )}
    </div>
  );
}
