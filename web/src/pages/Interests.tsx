import { useEffect, useState } from "react";
import { api, type Interests as InterestsT, type Watch } from "../api";
import { useQuery } from "../cache";
import { dollars } from "../components/Costs";
import { PageSkeleton } from "../components/Skeleton";
import { t } from "../i18n";
import { IconBack, IconClose, IconSpinner } from "../icons";
import { ago } from "../kinds";
import { Link } from "../nav";
import { keys } from "../queries";

const ORIGIN: Record<Watch["origin"], string> = {
  manual: t("ajouté par toi"), auto: t("appris de ta KB"), default: t("par défaut"), suggested: t("suggéré par l'agent"),
  x_follow: t("suivi sur X"),
};

// the server writes this note for people it follows automatically (backend/app/digest/profile.py, auto_follow)
const noteOf = (note: string) => {
  const m = note.match(/^(\d+) tweets sauvegardés dans ta KB$/);
  return m ? t("{n} tweets sauvegardés", { n: m[1] }) : note;
};
const hostOf = (url: string | null) => { try { return url ? new URL(url).hostname.replace(/^www\./, "") : ""; } catch { return ""; } };

/** What the digest agent understood of the user's tastes, and the people and feeds it follows. */
export default function Interests() {
  const query = useQuery(keys.interests, api.interests);
  const data = query.data ?? null;
  const [text, setText] = useState("");
  const [busy, setBusy] = useState("");
  const [failed, setError] = useState("");
  const error = failed || query.error;
  const [handle, setHandle] = useState("");
  const [name, setName] = useState("");
  const [feedUrl, setFeedUrl] = useState("");
  const [xUser, setXUser] = useState("");

  // the text box follows what the server holds, never overwriting what is being typed
  const serverText = data?.text ?? "";
  useEffect(() => { setText(serverText); }, [serverText]);
  const load = query.refresh;

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError("");
    try { await fn(); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };

  if (!data) {
    return <div className="page">{error ? <div className="error-box">{error}</div> : <PageSkeleton />}</div>;
  }

  const prof = data.profile;
  const people = data.people.filter((p) => p.status !== "suggested");
  const suggestions = data.people.filter((p) => p.status === "suggested");
  const sched = data.schedule;

  const row = (w: Watch) => (
    <div key={w.id} className={`watch-row${w.status === "muted" ? " muted-row" : ""}`}>
      <span className="who">
        <b>{w.name}{w.x_handle && <span> <a href={`https://x.com/${w.x_handle}`} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>@{w.x_handle}</a></span>}</b>
        <span>{[w.kind === "feed" ? hostOf(w.url || w.feed_url) : "", ORIGIN[w.origin], w.note ? noteOf(w.note) : "", w.status === "muted" ? t("en pause") : ""]
          .filter(Boolean).join(" · ")}</span>
        {w.last_error && <span className="err">{t("Flux en erreur : {error}", { error: w.last_error })}</span>}
      </span>
      <button className="btn xs outline" onClick={() => run(`w${w.id}`, () => api.patchWatch(w.id, { status: w.status === "muted" ? "active" : "muted" }))}>
        {w.status === "muted" ? t("Réactiver") : t("Mettre en pause")}
      </button>
      {w.kind === "feed" && w.origin === "manual" && (
        <button className="icon-btn" aria-label={t("Retirer {name}", { name: w.name })} onClick={() => run(`d${w.id}`, () => api.removeWatch(w.id))}><IconClose size={18} /></button>
      )}
    </div>
  );

  return (
    <div className="page">
      <Link className="back" to="/digest"><IconBack size={18} /> {t("Digest")}</Link>
      <div className="title-block">
        <h1 className="title">{t("Ce qui t'intéresse")}</h1>
        <p className="lede">{t("Ton agent apprend de ce que tu sauvegardes, de tes objectifs Perso et de tes votes dans les digests. "
          + "Tu peux aussi lui dire les choses directement.")}</p>
      </div>

      <div className="column">
        <p className="kraft-box" style={{ margin: 0 }}>
          {sched.enabled
            ? <>{t("Digest chaque jour à {hour} h ({timezone}), le lundi avec la semaine et des projets.", { hour: sched.hour, timezone: sched.timezone })}
                {sched.email ? t(" Envoyé aussi par e-mail.") : t(" Pas d'e-mail configuré.")}
                {sched.x ? "" : t(" Comptes X non lus (X_BEARER_TOKEN ou DIGEST_X_MAX_POSTS manquant).")}</>
            : <>{t("Le digest automatique est désactivé : mets")} <code>DIGEST_ENABLED=true</code>{" "}
                {t("dans les variables du serveur. Tu peux quand même en générer un depuis la page Digest.")}</>}
        </p>

        <section className="card">
          <h2>{t("Ce que l'agent a compris")}</h2>
          {prof.summary ? (
            <>
              <p style={{ fontFamily: "var(--serif)", fontSize: 18, lineHeight: 1.4 }}>{prof.summary}</p>
              {(prof.topics?.length ?? 0) > 0 && (
                <div className="topic-chips">{prof.topics!.map((x) => (
                  <span key={x.name}>{x.name}{x.weight > 1 && <i>{"★".repeat(x.weight - 1)}</i>}</span>
                ))}</div>
              )}
              {(prof.level || (prof.avoid?.length ?? 0) > 0) && (
                <p className="body" style={{ fontSize: 14 }}>
                  {[prof.level && t("Niveau : {level}", { level: prof.level }), (prof.avoid?.length ?? 0) > 0 && t("À éviter : {list}", { list: prof.avoid!.join(", ") })]
                    .filter(Boolean).join(" · ")}
                </p>
              )}
            </>
          ) : <p className="hint" style={{ fontSize: 14 }}>{t("Pas encore de profil : il se calcule avant le premier digest, ou maintenant.")}</p>}
          <div className="inline-row">
            <button className="btn small" onClick={() => run("refresh", api.refreshInterests)} disabled={Boolean(busy)}>
              {busy === "refresh" && <IconSpinner size={14} />} {t("Recalculer")}
            </button>
            {prof.computed_at && <span className="hint">{t("calculé {when}", { when: ago(prof.computed_at) })}</span>}
          </div>
        </section>

        <section className="card">
          <h2>{t("En quelques mots")}</h2>
          <label className="sr-only" htmlFor="interests-text">{t("Ce qui t'intéresse")}</label>
          <textarea id="interests-text" className="field" rows={3} value={text} onChange={(e) => setText(e.target.value)}
                    placeholder={t("Ex. : l'évaluation des LLM, le post-training, les modèles ouverts, les agents de code. Garde les grandes nouvelles tech, laisse tomber la crypto. Niveau : ingénieur ML.")} />
          <button className="btn small dark" style={{ alignSelf: "flex-start" }} disabled={Boolean(busy)}
                  onClick={() => run("text", async () => { await api.setInterests(text); await api.refreshInterests(); })}>
            {busy === "text" && <IconSpinner size={14} />} {t("Enregistrer et recalculer")}
          </button>
        </section>

        {suggestions.length > 0 && (
          <section className="card">
            <h2>{t("Ingénieurs à suivre ?")}</h2>
            <div className="watch-list">{suggestions.map((w) => (
              <div key={w.id} className="watch-row">
                <span className="who"><b>{w.name}{w.x_handle && <span> @{w.x_handle}</span>}</b>{w.note && <span>{noteOf(w.note)}</span>}</span>
                <button className="btn xs dark" onClick={() => run(`s${w.id}`, () => api.patchWatch(w.id, { status: "active" }))}>{t("+ Suivre")}</button>
                <button className="icon-btn" aria-label={t("Ignorer {name}", { name: w.name })} onClick={() => run(`m${w.id}`, () => api.patchWatch(w.id, { status: "muted" }))}><IconClose size={18} /></button>
              </div>
            ))}</div>
          </section>
        )}

        <section className="card">
          <h2>{t("Ingénieurs suivis")}</h2>
          <p className="hint" style={{ fontSize: 14 }}>{t("Leurs posts X des dernières 24 h (et leur blog, si tu en donnes l'adresse) entrent dans « Tes ingénieurs ».")}</p>
          <XFollowBox x={data.x_follow} busy={busy} xUser={xUser} setXUser={setXUser} run={run} />
          {people.length ? <div className="watch-list">{people.map(row)}</div> : <p className="hint">{t("Personne pour l'instant.")}</p>}
          <form className="add-row" onSubmit={(e) => { e.preventDefault(); run("person", async () => {
            await api.addWatch({ x_handle: handle, name: name || undefined });
            setHandle(""); setName("");
          }); }}>
            <input className="field" aria-label={t("Compte X")} placeholder={t("@ compte X (ex. karpathy)")} value={handle} onChange={(e) => setHandle(e.target.value)} required />
            <input className="field" aria-label={t("Nom")} placeholder={t("Nom (facultatif)")} value={name} onChange={(e) => setName(e.target.value)} style={{ flexBasis: 160 }} />
            <button className="btn small dark" disabled={Boolean(busy) || !handle.trim()}>{busy === "person" && <IconSpinner size={14} />} {t("+ Suivre")}</button>
          </form>
        </section>

        <section className="card">
          <h2>{t("Flux et blogs")}</h2>
          <p className="hint" style={{ fontSize: 14 }}>{t("Toujours lus aussi : Hacker News, les papiers du jour de Hugging Face et les dépôts GitHub qui montent.")}</p>
          <div className="watch-list">{data.feeds.map(row)}</div>
          <form className="add-row" onSubmit={(e) => { e.preventDefault(); run("feed", async () => {
            await api.addWatch({ url: feedUrl });
            setFeedUrl("");
          }); }}>
            <input className="field" type="url" aria-label={t("Adresse d'un blog ou d'un flux RSS")} placeholder={t("Adresse d'un blog ou d'un flux RSS")}
                   value={feedUrl} onChange={(e) => setFeedUrl(e.target.value)} required style={{ flexBasis: 260 }} />
            <button className="btn small dark" disabled={Boolean(busy) || !feedUrl.trim()}>{busy === "feed" && <IconSpinner size={14} />} {t("+ Ajouter")}</button>
          </form>
        </section>

        {error && <div className="error-box">{error}</div>}
      </div>
    </div>
  );
}

/** Link the user's X account: whoever they follow from now on joins the people above, every morning. */
function XFollowBox({ x, busy, xUser, setXUser, run }: {
  x: InterestsT["x_follow"]; busy: string; xUser: string; setXUser: (v: string) => void;
  run: (label: string, fn: () => Promise<unknown>) => Promise<void>;
}) {
  if (!x.available) {
    return <p className="x-box">{t("Pour ajouter tout seul les comptes que tu suis sur X, renseigne X_BEARER_TOKEN dans Railway.")}</p>;
  }
  if (!x.configured) {
    return (
      <div className="x-box">
        <p className="lead">{t("Relie ton compte X : chaque personne que tu suis sur X arrive ici toute seule.")}</p>
        <form className="add-row" onSubmit={(e) => { e.preventDefault(); run("xlink", () => api.linkX(xUser).then(() => setXUser(""))); }}>
          <input className="field" aria-label={t("Ton compte X")} placeholder={t("ton compte X")} value={xUser} onChange={(e) => setXUser(e.target.value)} required />
          <button className="btn small dark" disabled={Boolean(busy) || !xUser.trim()}>{busy === "xlink" && <IconSpinner size={14} />} {t("Relier")}</button>
        </form>
        <p className="hint">{t("Chaque matin, avant le digest, l'agent lit tes 5 derniers abonnements (0,05 $ de crédits X) et continue tant qu'il en trouve de nouveaux.")}</p>
      </div>
    );
  }
  return (
    <div className="x-box">
      <p className="lead">
        {t("Relié à {handle}", { handle: `@${x.username}` })}
        {x.following_count != null && <> · {t("{n} abonnements", { n: x.following_count })}</>}
        {x.last_sync_at && <> · {t("vérifié {when}", { when: ago(x.last_sync_at) })}</>}
      </p>
      <button className="btn xs" onClick={() => run("xsync", api.syncX)} disabled={Boolean(busy)}>
        {busy === "xsync" && <IconSpinner size={14} />} {t("Vérifier maintenant")}
      </button>
      {!x.imported_at && (x.following_count ?? 0) > 0 && (
        <button className="btn xs" disabled={Boolean(busy)} onClick={() => {
          if (window.confirm(t("Importer les {n} comptes que tu suis déjà ? Ça coûte environ {cost} de crédits X, une seule fois.",
                               { n: x.following_count ?? 0, cost: dollars(x.import_cost) }))) run("ximport", api.importX);
        }}>
          {busy === "ximport" && <IconSpinner size={14} />} {t("Importer ceux d'avant (≈ {cost})", { cost: dollars(x.import_cost) })}
        </button>
      )}
      <button className="linkish" style={{ fontSize: 13, color: "var(--ink-3)" }} disabled={Boolean(busy)} onClick={() => run("xunlink", () => api.linkX(""))}>{t("Délier")}</button>
      {x.last_added.length > 0 && <p className="hint">{t("Ajouté la dernière fois : {names}", { names: x.last_added.join(", ") })}</p>}
      {x.last_error && <p className="hint" style={{ color: "var(--accent-ink)" }}>{x.last_error}</p>}
    </div>
  );
}
