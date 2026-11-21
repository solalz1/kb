import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useQuery } from "../cache";
import { t, tServer } from "../i18n";
import { IconClip, IconSpinner } from "../icons";
import { Link, useNavigate } from "../nav";
import { CATEGORIES } from "../perso";
import { keys } from "../queries";
import { Toast } from "../components/Toast";
import { canGoBack } from "../motion";
import { dayLabel, isDayKey, journalHref, keyOf, parseKey } from "../days";

/** Where the « + » files what is added: Veille, Perso, or the journal (a Perso note on a calendar day). */
type Place = "main" | "perso" | "journal";
const placeOf = (v: string | null): Place => (v === "perso" || v === "journal" ? v : "main");

export default function Add() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [value, setValue] = useState(params.get("url") || params.get("text") || "");
  const [note, setNote] = useState("");
  const [space, setSpace] = useState<Place>(() => placeOf(params.get("space")));
  // computed once: a page left open past midnight keeps the day it showed
  const [todayKey] = useState(() => keyOf(new Date()));
  const [day, setDay] = useState(() => (isDayKey(params.get("day")) ? params.get("day")! : todayKey));
  const journal = space === "journal";
  const [category, setCategory] = useState("");
  const [folder, setFolder] = useState(params.get("folder") || "");
  const folders = useQuery(keys.folders, api.folders).data?.folders ?? [];
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);   // upload fraction, null when not uploading
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ message: string; id: string; day?: string } | null>(null);
  const picker = useRef<HTMLInputElement>(null);

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
      // Veille: nothing forced, a #perso in the note can still file it in Perso; the journal: a Perso note of the day
      const where: Record<string, string> = journal ? { space: "perso", category: "journal", entry_date: day }
        : space === "perso" ? { space, ...(category ? { category } : {}) } : {};
      if (folder && !journal) where.folder = folder;
      const why = journal ? "" : note.trim();
      const text = value.trim();
      const isUrl = /^https?:\/\/\S+$/i.test(text);
      if (files.length) {
        const form = new FormData();
        files.forEach((f) => form.append("file", f, f.name));
        if (why) form.append("note", why);
        if (text) form.append("text", text);
        Object.entries(where).forEach(([k, v]) => form.append(k, v));
        setProgress(0);
        res = await api.uploadFiles(form, setProgress);
      } else if (journal && !isUrl) {
        // the user's own words, kept as they are
        const note = await api.createNote({ content: text, space: "perso", category: "journal", entry_date: day });
        res = { message: "Ajouté à ton journal ✓", id: note.id, items: [] };
      } else {
        res = await api.ingest(isUrl ? { url: text, note: why, ...where } : { text, note: why, ...where });
      }
      const fresh = !res.items.some((i) => i.duplicate);
      setDone({ message: journal && fresh ? t("Ajouté à ton journal ✓") : tServer(res.message), id: res.id,
                day: journal ? day : undefined });
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
        <button type="button" className="back" onClick={() => (canGoBack() ? nav(-1) : nav("/"))}>{t("Annuler")}</button>
        <button className="btn primary" type="submit" disabled={busy}>
          {busy && <IconSpinner size={15} />}
          {progress === null ? t("Ajouter") : progress < 1 ? t("Envoi… {pct} %", { pct: Math.round(progress * 100) }) : t("Enregistrement…")}
        </button>
      </div>

      <h1 className="add-title">{t("Qu'est-ce que tu gardes ?")}</h1>

      <label className="stack">
        <span className="label">{journal ? t("Ta journée, tes pensées") : t("Lien, idée ou citation")}</span>
        <textarea className="field serif" rows={journal ? 6 : 4} value={value} onChange={(e) => { setValue(e.target.value); setError(""); }}
                  style={{ fontSize: 19, padding: "14px 16px", borderRadius: 14, resize: "none" }}
                  placeholder={journal ? t("Ce qui s'est passé, ce que tu as ressenti, ce que tu retiens…") : t("Colle un lien, ou écris directement…")} />
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
          <button type="button" aria-pressed={journal} onClick={() => setSpace("journal")}>{t("Journal")}</button>
        </div>
        {journal && (
          <div className="journal-day">
            <label className="sr-only" htmlFor="add-day">{t("Jour du journal")}</label>
            <input id="add-day" className="field" type="date" value={day} required
                   onChange={(e) => { if (isDayKey(e.target.value)) setDay(e.target.value); }} />
            <span className="hint">
              {day === todayKey ? t("Aujourd'hui · {day}", { day: dayLabel(parseKey(day), false) }) : dayLabel(parseKey(day))}
              {day !== todayKey && <> · <button type="button" className="linkish" onClick={() => setDay(todayKey)}>{t("aujourd'hui")}</button></>}
            </span>
          </div>
        )}
        {space === "perso" && (
          <div className="chips" role="group" aria-label={t("Catégorie")}>
            {CATEGORIES.map((c) => (
              <button type="button" key={c.id} className="chip sm" aria-pressed={category === c.id}
                      onClick={() => setCategory(category === c.id ? "" : c.id)}>{c.label}</button>
            ))}
          </div>
        )}
      </div>

      {folders.length > 0 && !journal && (
        <label className="stack">
          <span className="label">{t("Dossier")}</span>
          <select className="field" value={folder} onChange={(e) => setFolder(e.target.value)} style={{ height: 50, borderRadius: 14 }}>
            <option value="">{t("Automatique : Claude choisit")}</option>
            {folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>
      )}

      {!journal && <label className="stack">
        <span className="label">{t("Pourquoi tu gardes ça ?")} <span className="opt">{t("facultatif")}</span></span>
        <input className="field" value={note} onChange={(e) => setNote(e.target.value)} style={{ height: 50, borderRadius: 14, padding: "0 16px", fontSize: 16 }}
               placeholder={t("ex. pour mon projet d'agent, idée de feature…")} />
        <span className="hint">{t("Cette phrase oriente le résumé et aide à le retrouver plus tard.")}</span>
      </label>}

      {error && <div className="error-box">{error}</div>}

      <div className="quick-tip">
        <b>{t("Plus rapide encore")}</b>
        <span>{t("Depuis n'importe quelle app, bouton Partager → « Ajouter à ma KB ». Pas besoin d'ouvrir l'app.")}</span>
      </div>
      {journal ? (
        <p className="hint">{t("Chaque jour a sa page : ")}<Link to={journalHref(day, todayKey)}>{t("ouvre le calendrier du journal")}</Link>.</p>
      ) : (
        <p className="hint">{t("Pour un texte long, ")}<Link to={`/note/new${space === "perso" ? "" : "?space=main"}`}>{t("écris une note")}</Link>.</p>
      )}

      <Toast id={done?.id}>{done && <>{done.message} {done.day
        ? <Link to={journalHref(done.day, todayKey)}>{t("Voir le jour")}</Link>
        : <Link to={`/item/${done.id}`}>{t("Voir")}</Link>}</>}</Toast>
    </form>
  );
}
