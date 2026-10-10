import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Folder } from "../api";
import { t } from "../i18n";
import { IconBack, IconFolder, IconPlus, IconSpark, IconSpinner } from "../icons";

/** Every folder with what's in it, a way to add one, and Claude to file what isn't filed by hand. */
export default function Folders() {
  const [folders, setFolders] = useState<Folder[] | null>(null);
  const [unfiled, setUnfiled] = useState(0);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");

  const apply = (r: { folders: Folder[]; unfiled: number }) => { setFolders(r.folders); setUnfiled(r.unfiled); };
  useEffect(() => { api.folders().then(apply).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 4000);
    return () => clearTimeout(timer);
  }, [notice]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy("add");
    setError("");
    try {
      await api.createFolder({ name, description: description || undefined });
      setName(""); setDescription(""); setAdding(false);
      apply(await api.folders());
      setNotice(t("Dossier créé. « Ranger automatiquement » y range ce qui lui correspond."));
    } catch (err) { setError((err as Error).message); } finally { setBusy(""); }
  };

  const sort = async () => {
    setBusy("sort");
    setError("");
    try {
      const r = await api.sortFolders();
      apply(r);
      setNotice(t("{n} éléments rangés", { n: r.filed }));
    } catch (err) { setError((err as Error).message); } finally { setBusy(""); }
  };

  return (
    <div className="page">
      <Link className="back phone-only" to="/"><IconBack size={18} /> {t("Veille")}</Link>
      <div className="title-block">
        <h1 className="title">{t("Dossiers")}</h1>
        <p className="lede">{t("Claude range chaque nouvel élément dans le dossier qui lui correspond. Tu peux le déplacer depuis sa fiche, ou choisir le dossier en partageant.")}</p>
      </div>

      <div className="folder-actions">
        <button className="btn small primary" onClick={() => setAdding((v) => !v)} aria-expanded={adding}><IconPlus size={16} /> {t("Nouveau dossier")}</button>
        <button className="btn small" onClick={sort} disabled={Boolean(busy) || !folders?.length}>
          {busy === "sort" ? <IconSpinner /> : <IconSpark size={16} />} {busy === "sort" ? t("Rangement…") : t("Ranger automatiquement")}
        </button>
      </div>

      {adding && (
        <form className="folder-form" onSubmit={add} style={{ maxWidth: 760 }}>
          <label className="stack">
            <span className="label">{t("Nom du dossier")}</span>
            <input className="field" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required autoFocus
                   placeholder={t("ex. Lectures, Startup, Santé")} />
          </label>
          <label className="stack">
            <span className="label">{t("Ce qui va dedans")} <span className="opt">{t("facultatif")}</span></span>
            <textarea className="field" rows={2} value={description} onChange={(e) => setDescription(e.target.value)}
                      placeholder={t("ex. préparation d'entretiens, questions techniques, études de cas")} />
            <span className="hint">{t("Claude s'en sert pour ranger les nouveaux éléments.")}</span>
          </label>
          <div className="btn-row">
            <button className="btn small primary" disabled={busy === "add" || !name.trim()}>{t("Créer le dossier")}</button>
            <button type="button" className="btn small ghost" onClick={() => setAdding(false)}>{t("Annuler")}</button>
          </div>
        </form>
      )}

      {error && <div className="error-box">{error}</div>}
      {folders === null && !error && <div className="status-line"><IconSpinner /> {t("Chargement…")}</div>}

      {folders && (
        <div className="folder-list folders-grid">
          {folders.map((f) => (
            <Link key={f.id} to={`/folders/${f.id}`} className="folder-card">
              <span className="ic"><IconFolder size={22} /></span>
              <span className="txt">
                <span className="t">{f.name}</span>
                {f.description && <span className="d">{f.description}</span>}
              </span>
              <span className="n">{f.count}</span>
            </Link>
          ))}
          {unfiled > 0 && (
            <Link to="/folders/none" className="folder-card unfiled">
              <span className="ic"><IconFolder size={22} /></span>
              <span className="txt"><span className="t">{t("Sans dossier")}</span><span className="d">{t("Ce qui ne rentre dans aucun dossier")}</span></span>
              <span className="n">{unfiled}</span>
            </Link>
          )}
          {folders.length === 0 && <p className="hint">{t("Aucun dossier pour l'instant.")}</p>}
        </div>
      )}
      {notice && <div className="toast" role="status">{notice}</div>}
    </div>
  );
}
