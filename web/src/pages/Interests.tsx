import { ArrowLeft, AtSign, Check, Loader2, Plus, RefreshCw, Rss, X as Close } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Interests as InterestsT, type Watch } from "../api";
import { t } from "../i18n";
import { ago } from "../kinds";

const ORIGIN: Record<Watch["origin"], string> = {
  manual: t("ajouté par toi"), auto: t("appris de ta KB"), default: t("par défaut"), suggested: t("suggéré par l'agent"),
};
// the server writes this note for people it follows automatically (backend/app/digest/profile.py, auto_follow)
const noteOf = (note: string) => {
  const m = note.match(/^(\d+) tweets sauvegardés dans ta KB$/);
  return m ? t("{n} tweets sauvegardés dans ta KB", { n: m[1] }) : note;
};

/** What the digest agent understood of the user's tastes, and the people and feeds it follows. */
export default function Interests() {
  const [data, setData] = useState<InterestsT | null>(null);
  const [text, setText] = useState("");
  const [savedText, setSavedText] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [handle, setHandle] = useState("");
  const [name, setName] = useState("");
  const [feedUrl, setFeedUrl] = useState("");

  const load = () => api.interests().then((d) => { setData(d); setText(d.text); setSavedText(d.text); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError("");
    try { await fn(); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };

  if (!data) {
    return <div className="page">{error ? <div className="error-box">{error}</div> : <div className="status-line"><Loader2 size={16} className="spin" /> {t("Chargement…")}</div>}</div>;
  }

  const prof = data.profile;
  const people = data.people.filter((p) => p.status !== "suggested");
  const suggestions = data.people.filter((p) => p.status === "suggested");
  const sched = data.schedule;

  const row = (w: Watch) => (
    <li key={w.id} className={`watch-row${w.status === "muted" ? " muted-row" : ""}`}>
      <div className="w-main">
        <span className="w-name">{w.name}</span>
        {w.x_handle && <a href={`https://x.com/${w.x_handle}`} target="_blank" rel="noreferrer">@{w.x_handle}</a>}
        <span className="w-origin">{ORIGIN[w.origin]}</span>
        {w.note && <span className="w-note">{noteOf(w.note)}</span>}
        {w.last_error && <span className="w-err">{t("Flux en erreur : {error}", { error: w.last_error })}</span>}
      </div>
      <div className="w-actions">
        <button className="pill" onClick={() => run(`w${w.id}`, () => api.patchWatch(w.id, { status: w.status === "muted" ? "active" : "muted" }))}>
          {w.status === "muted" ? t("Réactiver") : t("Mettre en pause")}
        </button>
        {w.kind === "feed" && w.origin === "manual" && (
          <button className="icon-btn" aria-label={t("Retirer {name}", { name: w.name })} onClick={() => run(`d${w.id}`, () => api.removeWatch(w.id))}><Close size={15} /></button>
        )}
      </div>
    </li>
  );

  return (
    <div className="page interests">
      <Link className="back" to="/digest"><ArrowLeft size={16} /> {t("Digest")}</Link>
      <h1 className="title">{t("Ce qui t'intéresse")}</h1>
      <p className="muted">{t("Ton agent apprend de ce que tu sauvegardes, de tes objectifs Perso et de tes votes dans les digests. "
        + "Tu peux aussi lui dire les choses directement.")}</p>

      <div className={`warn${sched.enabled ? " ok-box" : ""}`}>
        {sched.enabled
          ? <>{t("Digest chaque jour à {hour} h ({timezone}), le lundi avec la semaine et des projets.", { hour: sched.hour, timezone: sched.timezone })}
              {sched.email ? t(" Envoyé aussi par e-mail.") : t(" Pas d'e-mail configuré.")}
              {sched.x ? "" : t(" Comptes X non lus (X_BEARER_TOKEN ou DIGEST_X_MAX_POSTS manquant).")}</>
          : <>{t("Le digest automatique est désactivé : mets")} <code>DIGEST_ENABLED=true</code>{" "}
              {t("dans les variables du serveur. Tu peux quand même en générer un depuis la page Digest.")}</>}
      </div>

      <section className="section">
        <h2>{t("Ce que l'agent a compris")}</h2>
        {prof.summary ? (
          <>
            <p>{prof.summary}</p>
            {(prof.topics?.length ?? 0) > 0 && (
              <div className="chips wrap">{prof.topics!.map((t) => (
                <span key={t.name} className="chip static">{t.name}{t.weight > 1 && <span className="n">{"★".repeat(t.weight - 1)}</span>}</span>
              ))}</div>
            )}
            {prof.level && <p className="hint">{t("Niveau : {level}", { level: prof.level })}</p>}
            {(prof.avoid?.length ?? 0) > 0 && <p className="hint">{t("À éviter : {list}", { list: prof.avoid!.join(", ") })}</p>}
          </>
        ) : <p className="muted">{t("Pas encore de profil : il se calcule avant le premier digest, ou maintenant.")}</p>}
        <button className="btn small" onClick={() => run("refresh", api.refreshInterests)} disabled={Boolean(busy)}>
          {busy === "refresh" ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />} {t("Recalculer")}
          {prof.computed_at && <span className="muted"> · {t("calculé {when}", { when: ago(prof.computed_at) })}</span>}
        </button>
      </section>

      <section className="section">
        <h2>{t("En quelques mots")}</h2>
        <label className="sr-only" htmlFor="interests-text">{t("Ce qui t'intéresse")}</label>
        <textarea id="interests-text" className="field" value={text} onChange={(e) => setText(e.target.value)}
                  placeholder={t("Ex. : l'évaluation des LLM, le post-training, les modèles ouverts, les agents de code. Garde les grandes nouvelles tech, laisse tomber la crypto. Niveau : ingénieur ML.")} />
        {text !== savedText && (
          <button className="btn small primary" style={{ marginTop: 8 }} onClick={() => run("text", async () => {
            await api.setInterests(text);
            await api.refreshInterests();
          })} disabled={Boolean(busy)}>
            {busy === "text" ? <Loader2 size={14} className="spin" /> : <Check size={14} />} {t("Enregistrer et recalculer")}
          </button>
        )}
      </section>

      {suggestions.length > 0 && (
        <section className="section">
          <h2>{t("Ingénieurs à suivre ?")}</h2>
          <ul className="watch-list">{suggestions.map((w) => (
            <li key={w.id} className="watch-row">
              <div className="w-main">
                <span className="w-name">{w.name}</span>
                {w.x_handle && <a href={`https://x.com/${w.x_handle}`} target="_blank" rel="noreferrer">@{w.x_handle}</a>}
                {w.note && <span className="w-note">{noteOf(w.note)}</span>}
              </div>
              <div className="w-actions">
                <button className="pill" onClick={() => run(`s${w.id}`, () => api.patchWatch(w.id, { status: "active" }))}><Plus size={14} /> {t("Suivre")}</button>
                <button className="icon-btn" aria-label={t("Ignorer {name}", { name: w.name })} onClick={() => run(`m${w.id}`, () => api.patchWatch(w.id, { status: "muted" }))}><Close size={15} /></button>
              </div>
            </li>
          ))}</ul>
        </section>
      )}

      <section className="section">
        <h2>{t("Ingénieurs suivis")}</h2>
        <p className="hint" style={{ marginTop: 0 }}>{t("Leurs posts X des dernières 24 h (et leur blog, si tu en donnes l'adresse) entrent dans « Tes ingénieurs ».")}</p>
        {people.length ? <ul className="watch-list">{people.map(row)}</ul> : <p className="muted">{t("Personne pour l'instant.")}</p>}
        <form className="add-row" onSubmit={(e) => { e.preventDefault(); run("person", async () => {
          await api.addWatch({ x_handle: handle, name: name || undefined });
          setHandle(""); setName("");
        }); }}>
          <div className="with-icon"><AtSign size={15} /><input className="field" placeholder={t("compte X (ex. karpathy)")} value={handle} onChange={(e) => setHandle(e.target.value)} required /></div>
          <input className="field" placeholder={t("Nom (facultatif)")} value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn small" disabled={Boolean(busy) || !handle.trim()}>{busy === "person" ? <Loader2 size={14} className="spin" /> : <Plus size={14} />} {t("Suivre")}</button>
        </form>
      </section>

      <section className="section">
        <h2>{t("Flux et blogs")}</h2>
        <p className="hint" style={{ marginTop: 0 }}>{t("Toujours lus aussi : Hacker News, les papiers du jour de Hugging Face et les dépôts GitHub qui montent.")}</p>
        <ul className="watch-list">{data.feeds.map(row)}</ul>
        <form className="add-row" onSubmit={(e) => { e.preventDefault(); run("feed", async () => {
          await api.addWatch({ url: feedUrl });
          setFeedUrl("");
        }); }}>
          <div className="with-icon"><Rss size={15} /><input className="field" type="url" placeholder={t("Adresse d'un blog ou d'un flux RSS")} value={feedUrl} onChange={(e) => setFeedUrl(e.target.value)} required /></div>
          <button className="btn small" disabled={Boolean(busy) || !feedUrl.trim()}>{busy === "feed" ? <Loader2 size={14} className="spin" /> : <Plus size={14} />} {t("Ajouter")}</button>
        </form>
      </section>

      {error && <div className="error-box">{error}</div>}
    </div>
  );
}
