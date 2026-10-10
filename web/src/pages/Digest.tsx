import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, type Digest as DigestT, type DigestEntry, type DigestKind, type DigestProject } from "../api";
import { cache, useQuery } from "../cache";
import { openInClaude } from "../claude";
import { PageSkeleton } from "../components/Skeleton";
import { locale, t } from "../i18n";
import { IconClose, IconDown, IconSpinner } from "../icons";
import { useDesktop } from "../layout";
import { Link, useNavigate } from "../nav";
import { keys } from "../queries";

// Same order as backend/app/digest/render.py: from the most general to the most technical.
const SECTIONS: [string, string][] = [
  ["essentiel", t("L'essentiel")],
  ["industrie", t("Industrie et produits")],
  ["modeles", t("Modèles et labs")],
  ["voix", t("Tes ingénieurs")],
  ["recherche", t("Recherche")],
  ["ingenierie", t("Ingénierie et outils")],
];
const DIFFICULTY = ["", t("accessible"), t("intermédiaire"), t("ambitieux")];
// project kinds are API values (backend/app/digest/agent.py PROJECT_KINDS), shown as they are in French
const PROJECT_KIND: Record<string, string> = {
  benchmark: t("benchmark"), reproduction: t("reproduction"), outil: t("outil"), agent: t("agent"), analyse: t("analyse"),
  contribution: t("contribution"), ecriture: t("ecriture"),
};

// Periods are plain dates, read in UTC so the day never shifts.
const day = (iso: string, opts: Intl.DateTimeFormatOptions) =>
  new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString(locale, { ...opts, timeZone: "UTC" });
const weekEnd = (iso: string) => new Date(Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) + 6 * 86400_000).toISOString();
/** First day of the period a digest written now covers: today, or last week's Monday (as in digest/agent.py). */
function currentStart(kind: DigestKind): string {
  const d = new Date();
  if (kind === "weekly") d.setDate(d.getDate() - ((d.getDay() + 6) % 7) - 7);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function titleOf(d: { kind: DigestKind; period_start: string; period_end?: string }, long: boolean): string {
  if (d.kind === "weekly") {
    return t("Semaine du {start} au {end}", {
      start: day(d.period_start, { day: "numeric", month: "long" }),
      end: day(d.period_end ?? weekEnd(d.period_start), { day: "numeric", month: "long", ...(long ? { year: "numeric" } : {}) }),
    });
  }
  return t("Digest du {date}", { date: day(d.period_start, long ? { weekday: "long", day: "numeric", month: "long", year: "numeric" } : { day: "numeric", month: "long" }) });
}
/** The date menu's label: the date alone for a daily digest. */
const shortDate = (h: { kind: DigestKind; period_start: string }) =>
  h.kind === "weekly" ? t("semaine du {date}", { date: day(h.period_start, { day: "numeric", month: "short" }) }) : day(h.period_start, { day: "numeric", month: "long" });

type Votes = Record<string, number>;

export default function Digest() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const desktop = useDesktop();
  const kind: DigestKind = params.get("kind") === "weekly" ? "weekly" : "daily";
  const key = id ? keys.digest(Number(id)) : keys.latestDigest(kind);
  const now = cache.peek<DigestT | null>(key);
  // while a digest (or extra projects) is being written, poll
  const writing = now?.status === "generating" || Boolean(now?.data.projects_pending);
  const query = useQuery<DigestT | null>(key, () => (id ? api.digest(Number(id)) : api.latestDigest(kind)), { poll: writing ? 3000 : 0 });
  const digest = query.data ?? null;
  const loading = query.loading;
  const [failed, setError] = useState("");
  const error = failed || query.error;
  const historyQuery = useQuery(keys.digests, () => api.digests());
  const history = historyQuery.data ?? [];
  const [votes, setVotes] = useState<Votes>({});
  const [kept, setKept] = useState<Record<string, string>>({});
  const [toast, setToast] = useState("");
  const inFlight = useRef(new Set<string>());

  // votes as the server has them, each time the digest arrives
  useEffect(() => {
    const v: Votes = {}, k: Record<string, string> = {};
    digest?.feedback.forEach((f) => { v[`${f.target}:${f.entry_key}`] = f.vote; if (f.item_id) k[`${f.target}:${f.entry_key}`] = f.item_id; });
    setVotes(v);
    setKept(k);
  }, [digest]);
  const busy = digest?.status === "generating" || Boolean(digest?.data.projects_pending);
  const load = query.refresh;
  // the list of past digests follows the one on screen (one being written becomes ready)
  const refreshHistory = historyQuery.refresh;
  useEffect(() => { if (digest) refreshHistory(); }, [digest?.id, digest?.status, refreshHistory]); // eslint-disable-line react-hooks/exhaustive-deps
  // the other period, fetched ahead: switching between Aujourd'hui and Semaine is then instant
  useEffect(() => {
    if (id) return;
    const other: DigestKind = kind === "weekly" ? "daily" : "weekly";
    cache.prefetch(keys.latestDigest(other), () => api.latestDigest(other));
  }, [id, kind]);

  const flash = (text: string) => { setToast(text); setTimeout(() => setToast(""), 3500); };
  const shownKind: DigestKind = digest?.kind ?? kind;

  // the current period's digest, written now (or rewritten: it costs Claude calls again, so ask first)
  const generate = async (which: DigestKind = kind) => {
    const done = history.some((h) => h.kind === which && h.period_start === currentStart(which) && h.status === "ready");
    if (done && !window.confirm(which === "weekly" ? t("Le digest de la semaine existe déjà. Le réécrire ?")
                                                  : t("Le digest d'aujourd'hui existe déjà. Le réécrire ?"))) return;
    try {
      const r = await api.generateDigest(which);
      flash(which === "weekly" ? t("Je prépare la semaine… (une à deux minutes)") : t("Je prépare le digest… (environ une minute)"));
      if (String(r.id) === id) await load(); else nav(`/digest/${r.id}`);
    } catch (e) { setError((e as Error).message); }
  };

  // retry after an error, or rebuild the digest on screen (not today's: this one)
  const regenerate = async () => {
    if (!digest) return;
    try {
      await api.regenerateDigest(digest.id);
      flash(t("Je réécris ce digest…"));
      await load();
    } catch (e) { flash((e as Error).message); }
  };

  const vote = async (target: "entry" | "project", key: string, value: number) => {
    if (!digest) return;
    const k = `${target}:${key}`;
    if (inFlight.current.has(k)) return;          // a double tap never saves twice
    inFlight.current.add(k);
    const next = votes[k] === value && value !== 2 ? 0 : value;
    setVotes((v) => ({ ...v, [k]: next }));
    try {
      const r = await api.digestFeedback(digest.id, { target, key, vote: next });
      if (r.item_id && next === 2) {
        setKept((m) => ({ ...m, [k]: r.item_id! }));
        flash(target === "entry" ? t("Ajouté à ta KB ✓") : t("Projet noté dans ta KB ✓"));
      }
    } catch (e) {
      flash((e as Error).message);
    } finally {
      inFlight.current.delete(k);
    }
  };

  const more = async () => {
    if (!digest) return;
    try {
      await api.moreProjects(digest.id);
      cache.update<DigestT | null>(key, (d) => d && { ...d, data: { ...d.data, projects_pending: true } });
    } catch (e) { flash((e as Error).message); }
  };

  const entries = digest?.data.entries ?? [];
  const refs = useMemo(() => Object.fromEntries(entries.map((e) => [e.key, e])), [entries]);
  const visible = entries.filter((e) => !e.hidden);
  const projects = digest?.data.projects ?? [];
  const switchKind = (k: DigestKind) => {
    if (id) nav(`/digest${k === "weekly" ? "?kind=weekly" : ""}`);
    else setParams(k === "weekly" ? { kind: "weekly" } : {});
  };
  const pastOfKind = history.filter((h) => h.kind === shownKind);
  const tools = (
    <>
      <Link className="btn small ghost" to="/digest/interets">{t("Mes intérêts")}</Link>
      {(digest || error) && <button className="btn small" onClick={() => generate(shownKind)}>{t("Générer maintenant")}</button>}
    </>
  );

  return (
    <div className="page">
      <div className="digest-bar">
        <div className="seg" role="group" aria-label={t("Période")}>
          <button type="button" aria-pressed={shownKind === "daily"} onClick={() => switchKind("daily")}>{t("Aujourd'hui")}</button>
          <button type="button" aria-pressed={shownKind === "weekly"} onClick={() => switchKind("weekly")}>{t("Semaine")}</button>
        </div>
        {digest && (
          <label className="date-pick">
            {shortDate(digest)} <IconDown size={16} />
            {pastOfKind.length > 1 && (
              <select value={digest.id} onChange={(e) => nav(`/digest/${e.target.value}`)} aria-label={t("Archives")}>
                {pastOfKind.map((h) => <option key={h.id} value={h.id}>{titleOf(h, true)}</option>)}
              </select>
            )}
          </label>
        )}
        {desktop && <><span style={{ flex: 1 }} />{tools}</>}
      </div>

      <div className="digest-main column">
        {error && <div className="error-box">{error}</div>}
        {loading && <PageSkeleton />}

        {!loading && !digest && !error && (
          <div className="empty">
            <h2>{kind === "weekly" ? t("Pas encore de digest de la semaine") : t("Pas encore de digest")}</h2>
            <p>{t("Chaque matin, ton agent lit tes sources (Hacker News, papiers, labs, blogs, les ingénieurs que tu suis sur X), "
              + "garde ce qui compte pour toi et te le résume, du plus général au plus technique. Le lundi, il ajoute la semaine "
              + "et des idées de projets.")}</p>
            <div className="btn-row" style={{ justifyContent: "center" }}>
              <button className="btn primary" onClick={() => generate()}>{t("Générer maintenant")}</button>
              <Link className="btn" to="/digest/interets">{t("Dire ce qui m'intéresse")}</Link>
            </div>
          </div>
        )}

        {digest && (
          <>
            <div className="digest-head">
              <h1>{titleOf(digest, desktop)}</h1>
              {digest.headline && <p>{digest.headline}</p>}
            </div>
            {digest.status === "generating" && <div className="status-line"><IconSpinner /> {t("Ton agent lit tes sources et écrit le digest…")}</div>}
            {digest.status === "error" && (
              <div className="error-box">{t("Le digest n'a pas pu être écrit : {error}", { error: digest.error ?? "" })}
                <div style={{ marginTop: 8 }}><button className="btn xs" onClick={regenerate}>{t("Réessayer")}</button></div>
              </div>
            )}

            {(digest.data.trends?.length ?? 0) > 0 && (
              <section className="d-section">
                <h2 className="mini-h">{t("La semaine en bref")}</h2>
                <ul className="trends">{digest.data.trends!.map((x, i) => (
                  <li key={i}>{x.text}{x.refs.filter((r) => refs[r]).map((r) => (
                    <a key={r} className="ref" href={refs[r].url} target="_blank" rel="noreferrer" title={refs[r].title}>↗</a>
                  ))}</li>
                ))}</ul>
              </section>
            )}

            {SECTIONS.map(([sid, label]) => {
              const rows = visible.filter((e) => e.section === sid);
              if (!rows.length) return null;
              return (
                <section key={sid} className="d-section">
                  <h2 className="mini-h">{label}</h2>
                  {rows.map((e) => <Entry key={e.key} e={e} vote={votes[`entry:${e.key}`] ?? 0} keptId={kept[`entry:${e.key}`]}
                                          onVote={(v) => vote("entry", e.key, v)} />)}
                </section>
              );
            })}

            {digest.status === "ready" && !visible.length && !projects.length && (
              <p className="hint">{t("Rien de marquant dans tes sources sur cette période.")}</p>
            )}

            {digest.status === "ready" && (
              <section className="d-section">
                <h2 className="mini-h">{t("Projets pour cette semaine")}</h2>
                {projects.length === 0 && (
                  <p className="hint">{digest.kind === "weekly" ? "" : t("Les projets arrivent avec le digest du lundi. ")}{t("Tu peux aussi en demander maintenant, à partir de l'actualité récente.")}</p>
                )}
                {projects.map((p, i) => <Project key={p.key} p={p} n={i + 1} refs={refs} vote={votes[`project:${p.key}`] ?? 0}
                                                 keptId={kept[`project:${p.key}`]} onVote={(v) => vote("project", p.key, v)} />)}
                <div className="btn-row">
                  <button className="btn small" onClick={more} disabled={Boolean(digest.data.projects_pending)}>
                    {digest.data.projects_pending && <IconSpinner size={15} />}
                    {digest.data.projects_pending ? t("Je cherche des idées…") : projects.length ? t("Proposer d'autres projets") : t("Proposer des projets")}
                  </button>
                  <button className="btn small ghost" onClick={() => openInClaude(digest.kind === "weekly"
                    ? t("Avec le connecteur KB, lis mon digest de la semaine (get_digest, kind=\"{kind}\") et discutons-en : "
                      + "qu'est-ce que je dois vraiment retenir, et lequel des projets proposés me ferait le plus progresser "
                      + "vers mes objectifs ?", { kind: digest.kind })
                    : t("Avec le connecteur KB, lis mon digest du jour (get_digest, kind=\"{kind}\") et discutons-en : "
                      + "qu'est-ce que je dois vraiment retenir, et lequel des projets proposés me ferait le plus progresser "
                      + "vers mes objectifs ?", { kind: digest.kind }))}>
                    {t("En discuter dans Claude")}
                  </button>
                </div>
              </section>
            )}

            {digest.status === "ready" && (
              <p className="hint">
                {t("Tes votes et ce que tu gardes apprennent à l'agent ce que tu aimes.")}{" "}
                <button className="linkish" style={{ fontSize: 13 }} onClick={regenerate} disabled={busy}>{t("Réécrire ce digest")}</button>
              </p>
            )}
          </>
        )}
        {!desktop && <div className="btn-row">{tools}</div>}
      </div>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}

function Entry({ e, vote, keptId, onVote }: { e: DigestEntry; vote: number; keptId?: string; onVote: (v: number) => void }) {
  const inKb = e.in_kb || Boolean(keptId);
  const fromX = e.person && e.source === "X";
  return (
    <article className={`d-entry${e.section === "voix" ? " voice" : ""}`}>
      <span className="src">{fromX ? t("{name} · sur X", { name: e.person! }) : [e.source, e.author].filter(Boolean).join(" · ")}</span>
      <h3><a href={e.url} target="_blank" rel="noreferrer">{e.title}</a></h3>
      {e.summary && <p className="s">{e.summary}</p>}
      {e.why && <p className="w">{t("Pour toi : {why}", { why: e.why.charAt(0).toLowerCase() + e.why.slice(1) })}</p>}
      <div className="d-actions">
        {inKb ? (
          keptId ? <Link className="btn outline ok" to={`/item/${keptId}`}>{t("Dans ta KB")} ✓</Link>
                 : <span className="btn outline ok">{t("Dans ta KB")} ✓</span>
        ) : (
          <button className="btn dark" onClick={() => onVote(2)}>{t("Garder")}</button>
        )}
        {e.links?.discussion && <a className="btn outline" href={e.links.discussion} target="_blank" rel="noreferrer">{t("Discussion")}</a>}
        {e.links?.arxiv && <a className="btn outline" href={e.links.arxiv} target="_blank" rel="noreferrer">arXiv</a>}
        <span className="grow" />
        <button className={`icon-btn boxed${vote === -1 ? " on" : ""}`} onClick={() => onVote(-1)} aria-label={t("Pas intéressé")} aria-pressed={vote === -1}><IconClose size={18} /></button>
      </div>
    </article>
  );
}

function Project({ p, n, refs, vote, keptId, onVote }: {
  p: DigestProject; n: number; refs: Record<string, DigestEntry>; vote: number; keptId?: string; onVote: (v: number) => void;
}) {
  const sources = p.refs.filter((r) => refs[r]).map((r) => refs[r]);
  return (
    <article className="d-entry project">
      <span className="src">{t("Projet {n}", { n })} · {[p.effort, DIFFICULTY[p.difficulty], PROJECT_KIND[p.kind] ?? p.kind].filter(Boolean).join(" · ")}</span>
      <h3>{p.title}</h3>
      <p className="s">{p.pitch}</p>
      <dl>
        <dt>{t("Pourquoi maintenant")}</dt>
        <dd>{p.why_now}{sources.map((s) => <a key={s.key} className="ref" href={s.url} target="_blank" rel="noreferrer" title={s.title}> ↗ {s.title}</a>)}</dd>
        <dt>{t("Ce que tu apprends")}</dt><dd>{p.learn}</dd>
        <dt>{t("Plan")}</dt><dd><ol>{p.plan.map((step, i) => <li key={i}>{step}</li>)}</ol></dd>
        <dt>{t("Livrable")}</dt><dd>{p.deliverable}</dd>
      </dl>
      <div className="d-actions">
        {keptId ? <Link className="btn outline ok" to={`/item/${keptId}`}>{t("Noté dans ta KB")} ✓</Link>
                : <button className="btn primary" onClick={() => onVote(2)}>{t("Je le fais")}</button>}
        <button className="btn outline" onClick={() => openInClaude(
          t("Je veux faire ce projet cette semaine : « {title} ». {pitch}\n\nPlan proposé :\n{plan}\n\n"
            + "Avec le connecteur KB (search_kb, find_for_project), regarde ce que ma KB contient d'utile, puis aide-moi à "
            + "découper la première session de travail : environnement, données, premier script.",
            { title: p.title, pitch: p.pitch, plan: p.plan.map((s, i) => `${i + 1}. ${s}`).join("\n") }))}>
          {t("Démarrer dans Claude")}
        </button>
        <span className="grow" />
        <button className={`icon-btn boxed${vote === -1 ? " on" : ""}`} onClick={() => onVote(-1)} aria-label={t("Pas intéressé")} aria-pressed={vote === -1}><IconClose size={18} /></button>
      </div>
    </article>
  );
}
