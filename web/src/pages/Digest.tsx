import { BookmarkPlus, Check, ChevronDown, ExternalLink, Lightbulb, Loader2, MessageSquareText, RefreshCw, Rocket, Settings2, SquareArrowOutUpRight, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type Digest as DigestT, type DigestEntry, type DigestKind, type DigestProject, type DigestSummary } from "../api";
import { openInClaude } from "../claude";
import { lang, locale, t } from "../i18n";

// Same order as backend/app/digest/render.py: from the most general to the most technical.
const SECTIONS: [string, string][] = [
  ["essentiel", t("L'essentiel")],
  ["industrie", t("Industrie et produits")],
  ["modeles", t("Modèles et labs")],
  ["voix", t("Tes ingénieurs")],
  ["recherche", t("Recherche")],
  ["ingenierie", t("Ingénierie et outils")],
];
const PAPER: Record<string, string> = { news: "article", blog: "article", paper: "pdf", repo: "repo", post: "tweet" };
const DIFFICULTY = ["", t("accessible"), t("intermédiaire"), t("ambitieux")];
// project kinds are API values (backend/app/digest/agent.py PROJECT_KINDS), shown as they are in French
const PROJECT_KIND: Record<string, string> = {
  benchmark: t("benchmark"), reproduction: t("reproduction"), outil: t("outil"), agent: t("agent"), analyse: t("analyse"),
  contribution: t("contribution"), ecriture: t("ecriture"),
};

// The server writes digest titles in French (backend/app/digest/render.py, title_for); in English they're rebuilt
// from the period. Periods are plain dates, read in UTC so the day never shifts.
const day = (iso: string, opts: Intl.DateTimeFormatOptions) =>
  new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString(locale, { ...opts, timeZone: "UTC" });
const weekEnd = (iso: string) => new Date(Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) + 6 * 86400_000).toISOString();
/** First day of the period a digest written now covers: today, or last week's Monday (as in digest/agent.py). */
function currentStart(kind: DigestKind): string {
  const d = new Date();
  if (kind === "weekly") d.setDate(d.getDate() - ((d.getDay() + 6) % 7) - 7);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function titleOf(d: { kind: DigestKind; title: string; period_start: string; period_end?: string }): string {
  if (lang === "fr") return d.title;
  if (d.kind === "weekly") {
    return t("Semaine du {start} au {end}", {
      start: day(d.period_start, { day: "numeric", month: "long" }),
      end: day(d.period_end ?? weekEnd(d.period_start), { day: "numeric", month: "long", year: "numeric" }),
    });
  }
  return t("Digest du {date}", { date: day(d.period_start, { weekday: "long", day: "numeric", month: "long", year: "numeric" }) });
}
/** Archive menu label: the date alone for a daily digest. */
const archiveLabel = (h: DigestSummary) =>
  lang === "fr" ? h.title.replace(/^Digest du /, "")
    : h.kind === "weekly" ? titleOf(h) : day(h.period_start, { weekday: "long", day: "numeric", month: "long", year: "numeric" });

type Votes = Record<string, number>;

export default function Digest() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const kind: DigestKind = params.get("kind") === "weekly" ? "weekly" : "daily";
  const [digest, setDigest] = useState<DigestT | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<DigestSummary[]>([]);
  const [votes, setVotes] = useState<Votes>({});
  const [kept, setKept] = useState<Record<string, string>>({});
  const [toast, setToast] = useState("");
  const req = useRef(0);
  const inFlight = useRef(new Set<string>());

  const apply = (d: DigestT | null) => {
    setDigest(d);
    const v: Votes = {}, k: Record<string, string> = {};
    d?.feedback.forEach((f) => { v[`${f.target}:${f.entry_key}`] = f.vote; if (f.item_id) k[`${f.target}:${f.entry_key}`] = f.item_id; });
    setVotes(v);
    setKept(k);
  };

  const load = async () => {
    const n = ++req.current;
    try {
      const d = id ? await api.digest(Number(id)) : await api.latestDigest(kind);
      if (n !== req.current) return;
      apply(d);
      setError("");
    } catch (e) {
      if (n === req.current) setError((e as Error).message);
    } finally {
      if (n === req.current) setLoading(false);
    }
  };

  useEffect(() => { setLoading(true); load(); }, [id, kind]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.digests().then(setHistory).catch(() => {}); }, [digest?.id, digest?.status]);

  // while a digest (or extra projects) is being written, poll — always with the current id/kind
  const loadRef = useRef(load);
  loadRef.current = load;
  const busy = digest?.status === "generating" || Boolean(digest?.data.projects_pending);
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => loadRef.current(), 3000);
    return () => clearInterval(t);
  }, [busy]);

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
      setDigest({ ...digest, data: { ...digest.data, projects_pending: true } });
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

  return (
    <div className="page digest-page">
      <div className="digest-bar">
        <div className="modes" role="group" aria-label={t("Période")}>
          <button aria-pressed={shownKind === "daily"} onClick={() => switchKind("daily")}>{t("Aujourd'hui")}</button>
          <button aria-pressed={shownKind === "weekly"} onClick={() => switchKind("weekly")}>{t("Semaine")}</button>
        </div>
        {pastOfKind.length > 1 && (
          <label className="model-pick archive">
            <span className="hide-sm">{t("Archives")}</span>
            <select value={digest?.id ?? ""} onChange={(e) => nav(`/digest/${e.target.value}`)} aria-label={t("Archives")}>
              {pastOfKind.map((h) => <option key={h.id} value={h.id}>{archiveLabel(h)}</option>)}
            </select>
            <ChevronDown size={14} aria-hidden />
          </label>
        )}
        <Link className="btn small ghost" to="/digest/interets"><Settings2 size={14} /> {t("Mes intérêts")}</Link>
        {(digest || error) && (
          <button className="btn small" onClick={() => generate(shownKind)}><RefreshCw size={14} /> {t("Générer maintenant")}</button>
        )}
      </div>

      {error && <div className="error-box">{error}</div>}
      {loading && <div className="status-line"><Loader2 size={16} className="spin" /> {t("Chargement…")}</div>}

      {!loading && !digest && !error && (
        <div className="empty">
          <h2 className="title" style={{ fontSize: 22 }}>{kind === "weekly" ? t("Pas encore de digest de la semaine") : t("Pas encore de digest")}</h2>
          <p>{t("Chaque matin, ton agent lit tes sources (Hacker News, papiers, labs, blogs, les ingénieurs que tu suis sur X), "
            + "garde ce qui compte pour toi et te le résume, du plus général au plus technique. Le lundi, il ajoute la semaine "
            + "et des idées de projets.")}</p>
          <div className="space-actions" style={{ justifyContent: "center" }}>
            <button className="btn primary" onClick={() => generate()}><RefreshCw size={16} /> {t("Générer maintenant")}</button>
            <Link className="btn" to="/digest/interets">{t("Dire ce qui m'intéresse")}</Link>
          </div>
        </div>
      )}

      {digest && (
        <>
          <h1 className="title digest-title">{titleOf(digest)}</h1>
          {digest.status === "generating" && (
            <div className="status-line"><Loader2 size={16} className="spin" /> {t("Ton agent lit tes sources et écrit le digest…")}</div>
          )}
          {digest.status === "error" && (
            <div className="error-box">{t("Le digest n'a pas pu être écrit : {error}", { error: digest.error ?? "" })}
              <div style={{ marginTop: 8 }}><button className="btn small" onClick={regenerate}><RefreshCw size={14} /> {t("Réessayer")}</button></div>
            </div>
          )}
          {digest.headline && <p className="digest-headline">{digest.headline}</p>}

          {(digest.data.trends?.length ?? 0) > 0 && (
            <section className="section">
              <h2>{t("La semaine en bref")}</h2>
              <ul>{digest.data.trends!.map((t, i) => (
                <li key={i}>{t.text}{t.refs.filter((r) => refs[r]).map((r) => (
                  <a key={r} className="ref" href={refs[r].url} target="_blank" rel="noreferrer" title={refs[r].title}>↗</a>
                ))}</li>
              ))}</ul>
            </section>
          )}

          {SECTIONS.map(([sid, label]) => {
            const rows = visible.filter((e) => e.section === sid);
            if (!rows.length) return null;
            return (
              <section key={sid} className="section">
                <h2>{label}</h2>
                <div className="digest-entries">
                  {rows.map((e) => <Entry key={e.key} e={e} vote={votes[`entry:${e.key}`] ?? 0} keptId={kept[`entry:${e.key}`]}
                                           onVote={(v) => vote("entry", e.key, v)} />)}
                </div>
              </section>
            );
          })}

          {digest.status === "ready" && !visible.length && !projects.length && (
            <p className="muted">{t("Rien de marquant dans tes sources sur cette période.")}</p>
          )}

          {digest.status === "ready" && (
            <section className="section">
              <h2><Lightbulb size={18} className="h-icon" /> {t("Projets pour cette semaine")}</h2>
              {projects.length === 0 && (
                <p className="muted">{digest.kind === "weekly" ? "" : t("Les projets arrivent avec le digest du lundi. ")}{t("Tu peux aussi en demander maintenant, à partir de l'actualité récente.")}</p>
              )}
              <div className="projects">
                {projects.map((p, i) => <Project key={p.key} p={p} n={i + 1} refs={refs} vote={votes[`project:${p.key}`] ?? 0}
                                                 keptId={kept[`project:${p.key}`]} onVote={(v) => vote("project", p.key, v)} />)}
              </div>
              <div className="space-actions" style={{ marginTop: 14 }}>
                <button className="btn" onClick={more} disabled={Boolean(digest.data.projects_pending)}>
                  {digest.data.projects_pending ? <Loader2 size={16} className="spin" /> : <Rocket size={16} />}
                  {digest.data.projects_pending ? t(" Je cherche des idées…") : projects.length ? t(" Proposer d'autres projets") : t(" Proposer des projets")}
                </button>
                <button className="btn ghost" onClick={() => openInClaude(digest.kind === "weekly"
                  ? t("Avec le connecteur KB, lis mon digest de la semaine (get_digest, kind=\"{kind}\") et discutons-en : "
                    + "qu'est-ce que je dois vraiment retenir, et lequel des projets proposés me ferait le plus progresser "
                    + "vers mes objectifs ?", { kind: digest.kind })
                  : t("Avec le connecteur KB, lis mon digest du jour (get_digest, kind=\"{kind}\") et discutons-en : "
                    + "qu'est-ce que je dois vraiment retenir, et lequel des projets proposés me ferait le plus progresser "
                    + "vers mes objectifs ?", { kind: digest.kind }))}>
                  <SquareArrowOutUpRight size={16} /> {t("En discuter dans Claude")}
                </button>
              </div>
            </section>
          )}

          {digest.status === "ready" && (
            <p className="hint digest-foot">
              {t("Tes votes et ce que tu gardes apprennent à l'agent ce que tu aimes.")}{" "}
              <button className="linkish" onClick={regenerate} disabled={busy}>{t("Réécrire ce digest")}</button>
            </p>
          )}
        </>
      )}
      {toast && <div className="toast" role="status"><Check size={16} /> {toast}</div>}
    </div>
  );
}

function Entry({ e, vote, keptId, onVote }: { e: DigestEntry; vote: number; keptId?: string; onVote: (v: number) => void }) {
  const inKb = e.in_kb || Boolean(keptId);
  return (
    <article className="d-entry" data-kind={PAPER[e.kind] ?? "article"}>
      <div className="d-meta">
        <span className="src">{e.person && e.source === "X" ? e.person : e.source}</span>
        {e.author && !(e.person && e.source === "X") && <span className="who">{e.author}</span>}
      </div>
      <a className="d-title" href={e.url} target="_blank" rel="noreferrer">{e.title}</a>
      {e.summary && <p className="d-summary">{e.summary}</p>}
      {e.why && <p className="d-why">{t("Pour toi : {why}", { why: e.why })}</p>}
      <div className="d-actions">
        <button className={`icon-btn${vote === 1 ? " on" : ""}`} onClick={() => onVote(1)} aria-label={t("Intéressant")} aria-pressed={vote === 1}><ThumbsUp size={15} /></button>
        <button className={`icon-btn${vote === -1 ? " on" : ""}`} onClick={() => onVote(-1)} aria-label={t("Pas pour moi")} aria-pressed={vote === -1}><ThumbsDown size={15} /></button>
        {inKb ? (
          keptId ? <Link className="pill ok" to={`/item/${keptId}`}><Check size={14} /> {t("Dans ta KB")}</Link>
                 : <span className="pill ok"><Check size={14} /> {t("Dans ta KB")}</span>
        ) : (
          <button className="pill" onClick={() => onVote(2)}><BookmarkPlus size={14} /> {t("Garder")}</button>
        )}
        {e.links?.discussion && <a className="pill ghost" href={e.links.discussion} target="_blank" rel="noreferrer"><MessageSquareText size={14} /> {t("Discussion")}</a>}
        {e.links?.arxiv && <a className="pill ghost" href={e.links.arxiv} target="_blank" rel="noreferrer"><ExternalLink size={14} /> arXiv</a>}
      </div>
    </article>
  );
}

function Project({ p, n, refs, vote, keptId, onVote }: {
  p: DigestProject; n: number; refs: Record<string, DigestEntry>; vote: number; keptId?: string; onVote: (v: number) => void;
}) {
  const sources = p.refs.filter((r) => refs[r]).map((r) => refs[r]);
  return (
    <article className="fiche project" data-kind="note">
      <div className="fiche-head">
        <span className="kind">{t("Projet {n}", { n })}</span>
        <span>{[p.effort, DIFFICULTY[p.difficulty], PROJECT_KIND[p.kind] ?? p.kind].filter(Boolean).join(" · ")}</span>
      </div>
      <h3>{p.title}</h3>
      <p className="ruled">{p.pitch}</p>
      <dl className="p-details">
        <dt>{t("Pourquoi maintenant")}</dt>
        <dd>{p.why_now}{sources.map((s) => <a key={s.key} className="ref" href={s.url} target="_blank" rel="noreferrer" title={s.title}> ↗ {s.title}</a>)}</dd>
        <dt>{t("Ce que tu apprends")}</dt><dd>{p.learn}</dd>
        <dt>{t("Plan")}</dt><dd><ol>{p.plan.map((step, i) => <li key={i}>{step}</li>)}</ol></dd>
        <dt>{t("Livrable")}</dt><dd>{p.deliverable}</dd>
      </dl>
      <div className="d-actions">
        {keptId ? (
          <Link className="pill ok" to={`/item/${keptId}`}><Check size={14} /> {t("Noté dans ta KB")}</Link>
        ) : (
          <button className="btn small primary" onClick={() => onVote(2)}><Rocket size={14} /> {t("Je le fais")}</button>
        )}
        <button className={`icon-btn${vote === 1 ? " on" : ""}`} onClick={() => onVote(1)} aria-label={t("Intéressant")} aria-pressed={vote === 1}><ThumbsUp size={15} /></button>
        <button className={`icon-btn${vote === -1 ? " on" : ""}`} onClick={() => onVote(-1)} aria-label={t("Pas pour moi")} aria-pressed={vote === -1}><ThumbsDown size={15} /></button>
        <button className="pill ghost" onClick={() => openInClaude(
          t("Je veux faire ce projet cette semaine : « {title} ». {pitch}\n\nPlan proposé :\n{plan}\n\n"
            + "Avec le connecteur KB (search_kb, find_for_project), regarde ce que ma KB contient d'utile, puis aide-moi à "
            + "découper la première session de travail : environnement, données, premier script.",
            { title: p.title, pitch: p.pitch, plan: p.plan.map((s, i) => `${i + 1}. ${s}`).join("\n") }))}>
          <SquareArrowOutUpRight size={14} /> {t("Démarrer dans Claude")}
        </button>
      </div>
    </article>
  );
}
