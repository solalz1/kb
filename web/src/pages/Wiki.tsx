import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type WikiLogEntry, type WikiPageSummary, type WikiSection } from "../api";
import { useQuery } from "../cache";
import { Lines } from "../components/Skeleton";
import { t } from "../i18n";
import { IconBack, IconDownload, IconGem, IconSearch, IconSpark, IconSpinner } from "../icons";
import { ago } from "../kinds";
import { Link } from "../nav";
import { obsidianOpen, obsidianSearch } from "../obsidian";
import { keys } from "../queries";
import { KIND_LABEL, SECTIONS } from "../wiki";
import { Toast } from "../components/Toast";

type View = "pages" | "journal";

/** The wiki's home: every page by section, what Claude is integrating now, the night's review and its ideas. */
export default function Wiki() {
  const [params, setParams] = useSearchParams();
  const section = (SECTIONS.some((s) => s.key === params.get("section")) ? params.get("section") : null) as WikiSection | null;
  const view: View = params.get("vue") === "journal" ? "journal" : "pages";
  const [busyQueue, setBusyQueue] = useState(false);
  const query = useQuery(keys.wiki, api.wiki, { poll: busyQueue ? 6000 : 0 });
  const wiki = query.data ?? null;
  const [filter, setFilter] = useState("");
  const [notice, setNotice] = useState("");
  const [working, setWorking] = useState<"" | "lint" | "zip">("");
  const [failed, setFailed] = useState("");

  const queued = (wiki?.queue.pending ?? 0) + (wiki?.queue.running ?? 0);
  useEffect(() => { setBusyQueue(queued > 0); }, [queued]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 4500);
    return () => clearTimeout(timer);
  }, [notice]);

  const pick = (next: { section?: WikiSection | null; vue?: View }) => {
    const p = new URLSearchParams(params);
    if ("section" in next) { if (next.section) p.set("section", next.section); else p.delete("section"); }
    if ("vue" in next) { if (next.vue === "journal") p.set("vue", "journal"); else p.delete("vue"); }
    setParams(p, { replace: true });
  };

  const pages = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (wiki?.pages ?? []).filter((p) => (!section || p.section === section)
      && (!q || p.title.toLowerCase().includes(q) || (p.summary ?? "").toLowerCase().includes(q)));
  }, [wiki, section, filter]);

  const lint = async () => {
    setWorking("lint");
    setFailed("");
    try {
      await api.wikiLint();
      setNotice(t("Relecture lancée : Claude passe le wiki en revue, le journal dira ce qu'il a corrigé."));
      await query.refresh();
    } catch (e) { setFailed((e as Error).message); } finally { setWorking(""); }
  };

  const zip = async () => {
    setWorking("zip");
    setFailed("");
    try {
      const blob = await api.wikiZip();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `kb-wiki-${new Date().toISOString().slice(0, 10)}.zip`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 30_000);
      setNotice(t("Décompresse le zip et ouvre le dossier « KB-Wiki » dans Obsidian (Ouvrir un dossier comme coffre)."));
    } catch (e) { setFailed((e as Error).message); } finally { setWorking(""); }
  };

  const error = failed || query.error;
  const counts = Object.fromEntries((wiki?.sections ?? []).map((s) => [s.key, s.count])) as Record<WikiSection, number>;
  const ideas = wiki?.suggestions.items ?? [];

  return (
    <div className="page wiki-home">
      <Link className="back phone-only" to="/"><IconBack size={18} /> {t("Veille")}</Link>
      <div className="title-block">
        <h1 className="title">{t("Wiki")}</h1>
        <p className="lede">{t("Claude range ici tout ce que tu sauvegardes, par thème : une page par sujet, reliée aux autres, mise à jour à chaque partage et relue chaque nuit.")}</p>
      </div>

      <div className="wiki-status" aria-live="polite">
        {queued > 0 && (
          <span className="status-line"><IconSpinner />
            {wiki?.queue.current?.title
              ? t("J'intègre « {title} »… ({n} en attente)", { title: wiki.queue.current.title, n: wiki.queue.pending })
              : t("{n} élément(s) à intégrer…", { n: queued })}
          </span>
        )}
        {wiki?.last_lint && (
          <span className="hint">{t("Relu {when} : {what}", { when: ago(wiki.last_lint.at), what: wiki.last_lint.title.replace(/^Relecture : /, "") })}</span>
        )}
        {(wiki?.queue.errors ?? 0) > 0 && <span className="hint err">{t("{n} intégration(s) en erreur cette semaine", { n: wiki!.queue.errors })}</span>}
      </div>

      <div className="btn-row">
        <a className="btn small primary" href={obsidianOpen()}><IconGem size={16} /> {t("Ouvrir dans Obsidian")}</a>
        <button className="btn small" onClick={lint} disabled={Boolean(working) || !wiki?.pages.length}>
          {working === "lint" ? <IconSpinner /> : <IconSpark size={16} />} {t("Relire maintenant")}
        </button>
        <button className="btn small" onClick={zip} disabled={Boolean(working) || !wiki?.pages.length}>
          {working === "zip" ? <IconSpinner /> : <IconDownload size={16} />} {t("Télécharger le coffre (zip)")}
        </button>
        <Link className="btn small ghost" to="/settings/wiki">{t("Synchro Obsidian")}</Link>
      </div>

      {error && <div className="error-box">{error}</div>}

      {ideas.length > 0 && view === "pages" && (
        <section className="wiki-ideas" aria-label={t("À creuser")}>
          <h2 className="mini-h">{t("À creuser, d'après la dernière relecture")}</h2>
          <ul>{ideas.map((i) => <li key={i.text} data-kind={i.kind}>{i.text}</li>)}</ul>
        </section>
      )}

      <div className="wiki-tools">
        <div className="seg sm" role="group" aria-label={t("Vue")}>
          <button type="button" aria-pressed={view === "pages"} onClick={() => pick({ vue: "pages" })}>{t("Pages")}</button>
          <button type="button" aria-pressed={view === "journal"} onClick={() => pick({ vue: "journal" })}>{t("Journal|wiki log")}</button>
        </div>
        {view === "pages" && (
          <label className="wiki-filter">
            <IconSearch size={16} />
            <input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t("Filtrer les pages")}
                   aria-label={t("Filtrer les pages")} />
          </label>
        )}
        {view === "pages" && (
          <a className="btn small ghost obsidian-search" href={obsidianSearch(filter)}
             title={t("Recherche plein texte dans tout le coffre, dans l'app Obsidian")}>
            <IconGem size={16} /> {filter.trim() ? t("Chercher « {q} » dans Obsidian", { q: filter.trim() }) : t("Chercher dans Obsidian")}
          </a>
        )}
      </div>

      {view === "pages" && (
        <div className="chips scroll" role="group" aria-label={t("Sections")}>
          <button type="button" className="chip sm" aria-pressed={!section} onClick={() => pick({ section: null })}>
            {t("Tout")} <span className="n">{wiki?.pages.length ?? ""}</span>
          </button>
          {SECTIONS.map((s) => (
            <button key={s.key} type="button" className="chip sm" aria-pressed={section === s.key} onClick={() => pick({ section: s.key })}>
              {s.label} <span className="n">{counts[s.key] ?? ""}</span>
            </button>
          ))}
        </div>
      )}

      {view === "journal" ? <Journal /> : wiki === null && !error ? (
        <div className="wiki-list" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="wiki-card"><Lines n={2} widths={["45%", "85%"]} /></div>)}</div>
      ) : wiki && wiki.pages.length === 0 ? (
        <div className="kraft-box">
          {queued > 0
            ? t("Claude est en train d'écrire les premières pages à partir de ce que tu as déjà sauvegardé.")
            : t("Le wiki se remplit tout seul : chaque élément que tu partages y est intégré, rangé par thème avec ceux qui en parlent déjà.")}
        </div>
      ) : (
        <PageList pages={pages} grouped={!section && !filter.trim()} />
      )}
      <Toast>{notice}</Toast>
    </div>
  );
}

function PageList({ pages, grouped }: { pages: WikiPageSummary[]; grouped: boolean }) {
  if (!pages.length) return <p className="hint">{t("Aucune page ne correspond.")}</p>;
  if (!grouped) return <div className="wiki-list">{pages.map((p) => <PageCard key={p.id} page={p} />)}</div>;
  return (
    <>
      {SECTIONS.filter((s) => pages.some((p) => p.section === s.key)).map((s) => (
        <section key={s.key} className="wiki-section" aria-label={s.label}>
          <div className="wiki-section-head"><h2>{s.label}</h2><span className="hint">{s.hint}</span></div>
          <div className="wiki-list">{pages.filter((p) => p.section === s.key).map((p) => <PageCard key={p.id} page={p} />)}</div>
        </section>
      ))}
    </>
  );
}

function PageCard({ page }: { page: WikiPageSummary }) {
  return (
    <Link to={`/wiki/${page.slug}`} className="wiki-card" data-section={page.section}>
      <span className="t">{page.title}</span>
      {page.summary && <span className="d">{page.summary}</span>}
      <span className="m">
        {KIND_LABEL[page.kind]}
        {page.n_sources > 0 && <> · {t(page.n_sources > 1 ? "{n} sources" : "{n} source", { n: page.n_sources })}</>}
        {" · "}{page.updated_by === "user" ? t("modifiée par toi {when}", { when: ago(page.updated_at) }) : t("mise à jour {when}", { when: ago(page.updated_at) })}
      </span>
    </Link>
  );
}

const LOG_KIND: Record<WikiLogEntry["kind"], string> = {
  ingest: t("Intégré"), lint: t("Relecture"), answer: t("Réponse gardée"), edit: t("Modifié"), sync: t("Obsidian"),
};

/** log.md, in the app: what happened to the wiki, newest first. */
function Journal() {
  const log = useQuery(keys.wikiLog, api.wikiLog).data ?? null;
  if (log === null) return <div className="wiki-log" aria-busy="true"><Lines n={4} widths={["70%", "90%", "60%", "80%"]} /></div>;
  if (!log.length) return <p className="hint">{t("Rien encore : chaque intégration, relecture ou modification apparaîtra ici.")}</p>;
  return (
    <ol className="wiki-log">
      {log.map((e) => (
        <li key={e.id} data-kind={e.kind}>
          <span className="when">{ago(e.at)}</span>
          <span className="what"><b>{LOG_KIND[e.kind]}</b> {e.title}</span>
          {(e.detail.pages?.length ?? 0) > 0 && (
            <span className="pages">{e.detail.pages!.map((title, i) => (
              <span key={title}>{i ? ", " : ""}<Link to={`/wiki/${encodeURIComponent(title)}`}>{title}</Link>
                {e.detail.created?.includes(title) ? ` (${t("nouvelle")})` : ""}</span>
            ))}</span>
          )}
          {e.detail.nothing && <span className="hint">{t("Rien de durable à intégrer.")}</span>}
          {(e.detail.fixed ?? []).slice(0, 6).map((f) => <span key={f.title} className="hint">{f.title} : {f.reason}</span>)}
          {(e.detail.merged ?? []).map((m) => <span key={m.from} className="hint">{t("Fusion : {from} → {into}", { from: m.from, into: m.into })}</span>)}
          {e.detail.replaced_claude_update && <span className="hint">{t("Ta version a remplacé une mise à jour de Claude : la relecture de la nuit reprendra ce qui manque.")}</span>}
        </li>
      ))}
    </ol>
  );
}

