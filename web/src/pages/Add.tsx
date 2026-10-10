import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type Folder, type Space } from "../api";
import { t, tServer } from "../i18n";
import { IconClip, IconSpinner } from "../icons";
import { CATEGORIES } from "../perso";

export default function Add() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [value, setValue] = useState(params.get("url") || params.get("text") || "");
  const [note, setNote] = useState("");
  const [space, setSpace] = useState<Space>(params.get("space") === "perso" ? "perso" : "main");
  const [category, setCategory] = useState("");
  const [folder, setFolder] = useState(params.get("folder") || "");
  const [folders, setFolders] = useState<Folder[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);   // upload fraction, null when not uploading
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ message: string; id: string } | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => { api.folders().then((r) => setFolders(r.folders)).catch(() => {}); }, []);
  useEffect(() => {
    if (!done) return;
    const timer = setTimeout(() => setDone(null), 5000);
    return () => clearTimeout(timer);
  }, [done]);

  // paste an image or a file straight in (Cmd+V on a Mac)
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
      // Veille: nothing forced, a #perso in the note can still file it in Perso
      const where: Record<string, string> = space === "perso" ? { space, ...(category ? { category } : {}) } : {};
      if (folder) where.folder = folder;
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
    <form className="page roomy add-page" onSubmit={submit}>
      <div className="add-top">
        <button type="button" className="back" onClick={() => (history.length > 1 ? nav(-1) : nav("/"))}>{t("Annuler")}</button>
        <button className="btn primary" type="submit" disabled={busy}>
          {busy && <IconSpinner size={15} />}
          {progress === null ? t("Ajouter") : progress < 1 ? t("Envoi… {pct} %", { pct: Math.round(progress * 100) }) : t("Enregistrement…")}
        </button>
      </div>

      <h1 className="add-title">{t("Qu'est-ce que tu gardes ?")}</h1>

      <label className="stack">
        <span className="label">{t("Lien, idée ou citation")}</span>
        <textarea className="field serif" rows={4} value={value} onChange={(e) => { setValue(e.target.value); setError(""); }}
                  style={{ fontSize: 19, padding: "14px 16px", borderRadius: 14, resize: "none" }}
                  placeholder={t("Colle un lien, ou écris directement…")} />
      </label>

      <div className="stack-8">
        <button type="button" className={`drop${over ? " over" : ""}`} onClick={() => picker.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
                onDrop={(e) => { e.preventDefault(); setOver(false); setFiles((f) => [...f, ...Array.from(e.dataTransfer.files)]); }}>
          <IconClip size={20} /> {t("Joindre un fichier ou une photo")}
        </button>
        <input ref={picker} type="file" multiple hidden aria-label={t("Joindre des fichiers")}
               onChange={(e) => setFiles((f) => [...f, ...Array.from(e.target.files ?? [])])} />
        {files.length > 0 && (
          <div className="files">
            {files.map((f, i) => (
              <div key={i}><span>{f.name} ({Math.max(1, Math.round(f.size / 1024))} {t("Ko")})</span>
                <button type="button" className="btn xs ghost" onClick={() => setFiles(files.filter((_, j) => j !== i))}>{t("Retirer")}</button>
              </div>
            ))}
          </div>
        )}
        {progress !== null && (
          <div className="upload-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}>
            <span style={{ width: `${progress * 100}%` }} />
          </div>
        )}
        {progress !== null && progress < 1 && <p className="hint">{t("Garde l'app ouverte jusqu'à la fin de l'envoi.")}</p>}
      </div>

      <div className="stack-8">
        <span className="label">{t("Ranger dans")}</span>
        <div className="seg wide" role="group" aria-label={t("Espace")}>
          <button type="button" aria-pressed={space === "main"} onClick={() => setSpace("main")}>{t("Veille")}</button>
          <button type="button" aria-pressed={space === "perso"} onClick={() => setSpace("perso")}>{t("Perso")}</button>
        </div>
        {space === "perso" && (
          <div className="chips" role="group" aria-label={t("Catégorie")}>
            {CATEGORIES.map((c) => (
              <button type="button" key={c.id} className="chip sm" aria-pressed={category === c.id}
                      onClick={() => setCategory(category === c.id ? "" : c.id)}>{c.label}</button>
            ))}
          </div>
        )}
      </div>

      {folders.length > 0 && (
        <label className="stack">
          <span className="label">{t("Dossier")}</span>
          <select className="field" value={folder} onChange={(e) => setFolder(e.target.value)} style={{ height: 50, borderRadius: 14 }}>
            <option value="">{t("Automatique : Claude choisit")}</option>
            {folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>
      )}

      <label className="stack">
        <span className="label">{t("Pourquoi tu gardes ça ?")} <span className="opt">{t("facultatif")}</span></span>
        <input className="field" value={note} onChange={(e) => setNote(e.target.value)} style={{ height: 50, borderRadius: 14, padding: "0 16px", fontSize: 16 }}
               placeholder={t("ex. pour mon projet d'agent, idée de feature…")} />
        <span className="hint">{t("Cette phrase oriente le résumé et aide à le retrouver plus tard.")}</span>
      </label>

      {error && <div className="error-box">{error}</div>}

      <div className="quick-tip">
        <b>{t("Plus rapide encore")}</b>
        <span>{t("Depuis n'importe quelle app, bouton Partager → « Ajouter à ma KB ». Pas besoin d'ouvrir l'app.")}</span>
      </div>
      <p className="hint">{t("Pour un texte long, ")}<Link to={`/note/new${space === "perso" ? "" : "?space=main"}`}>{t("écris une note")}</Link>.</p>

      {done && (
        <div className="toast" role="status">
          {done.message} <Link to={`/item/${done.id}`}>{t("Voir")}</Link>
        </div>
      )}
    </form>
  );
}
