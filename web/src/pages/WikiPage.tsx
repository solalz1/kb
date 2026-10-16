import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useQuery } from "../cache";
import { openInClaude } from "../claude";
import { Lines } from "../components/Skeleton";
import { localized, t } from "../i18n";
import { IconBack } from "../icons";
import { ago } from "../kinds";
import { useDesktop } from "../layout";
import { Link, useNavigate } from "../nav";
import { keys } from "../queries";
import { headLabel } from "../perso";
import { internalTarget, KIND_LABEL, renderWiki, sectionLabel } from "../wiki";

const REASON: Record<string, string> = {
  ingest: t("intégration"), lint: t("relecture"), answer: t("réponse gardée"), obsidian: t("modifiée dans Obsidian"),
  chat: t("depuis Claude"), "lint: fusion": t("fusion"),
};

/** One page of the wiki: its text with links to the other pages and to its sources, what links to it, its history. */
export default function WikiPage() {
  const { slug = "" } = useParams();
  const desktop = useDesktop();
  const nav = useNavigate();
  const query = useQuery(slug ? keys.wikiPage(slug) : null, () => api.wikiPage(slug));
  const page = query.data ?? null;

  const html = useMemo(() => {
    if (!page) return "";
    const refs: Record<string, { id: string; title: string | null; n?: number }> = { ...page.refs };
    for (const s of page.sources) {
      const ref = s.id.replace(/-/g, "").slice(0, 8);
      refs[ref] = { id: s.id, title: s.title, n: s.n };
    }
    return renderWiki(page.content, page.links_resolved, refs);
  }, [page]);

  const onClick = (e: React.MouseEvent) => {
    const to = internalTarget(e);
    if (!to) return;
    e.preventDefault();
    nav(to);
  };

  if (query.error && !page) {
    return (
      <div className="page">
        <Link className="back" to="/wiki"><IconBack size={18} /> {t("Wiki")}</Link>
        <div className="error-box">{query.error}</div>
      </div>
    );
  }
  if (!page) {
    return (
      <div className="page item-page wiki-page" aria-busy="true">
        <div className="item-main"><Lines n={6} widths={["30%", "70%", "100%", "96%", "90%", "60%"]} /></div>
      </div>
    );
  }

  const dig = () => openInClaude(
    t("Avec le connecteur KB, lis la page « {title} » de mon wiki (read_wiki_page) et les pages qui y renvoient, "
      + "puis aide-moi à aller plus loin sur ce sujet : ce qui est solide, ce qui se contredit, ce qu'il me manque.",
      { title: page.title }));

  return (
    <div className="page item-page wiki-page">
      <div className="item-main">
        <div className="item-top">
          <button className="back" onClick={() => (history.length > 1 ? nav(-1) : nav("/wiki"))}>
            <IconBack size={desktop ? 18 : 20} /> {t("Wiki")}
          </button>
        </div>
        <div className="item-meta">
          <span className="wiki-dot" data-section={page.section} />
          <span className="kind">{sectionLabel(page.section)}</span>
          <span>· {KIND_LABEL[page.kind]}</span>
          <span>· {page.updated_by === "user" ? t("modifiée par toi {when}", { when: ago(page.updated_at) })
                                              : t("mise à jour {when}", { when: ago(page.updated_at) })}</span>
        </div>
        <h1 className="item-title">{page.title}</h1>
        {page.archived && (
          <div className="warn">
            {page.merged_into
              ? <>{t("Cette page a été fondue dans")} <Link to={`/wiki/${page.merged_into.slug}`}>{page.merged_into.title}</Link>.</>
              : t("Cette page a été retirée du wiki.")}
          </div>
        )}
        {page.summary && <p className="item-summary">{page.summary}</p>}
        <div className="item-actions">
          <div className="more">
            <Link className="btn" to={`/ask?page=${encodeURIComponent(page.title)}`}>{t("Poser une question")}</Link>
            <button className="btn" onClick={dig}>{t("Creuser dans Claude")}</button>
          </div>
        </div>
        <article className="wiki-body answer" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
      </div>

      <div className="item-more" />

      <aside className="item-aside">
        {page.sources.length > 0 && (
          <section className="aside-sec"><h2 className="mini-h">{t("Sources ({n})", { n: page.sources.length })}</h2>
            <div className="sources">
              {page.sources.map((s) => (
                <div key={s.id} className="source" data-kind={s.kind ?? undefined} data-space={s.space === "perso" ? "perso" : undefined}>
                  <span className="n">{s.n}</span>
                  <span className="body">
                    <Link to={`/item/${s.id}`} className="st">{localized(s).title || t("Sans titre")}</Link>
                    <span className="sm">{headLabel({ ...s, kind: s.kind === "wiki" ? null : s.kind })}{s.author ? ` · ${s.author}` : ""}
                      {s.source_url && <> · <a href={s.source_url} target="_blank" rel="noreferrer">{t("source ↗")}</a></>}</span>
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}
        {page.backlinks.length > 0 && (
          <section className="aside-sec"><h2 className="mini-h">{t("Pages qui y renvoient")}</h2>
            {page.backlinks.map((b) => (
              <Link key={b.id} to={`/wiki/${b.slug}`} className="related"><span className="t">{b.title}</span>
                {b.summary && <span className="r">{b.summary}</span>}</Link>
            ))}
          </section>
        )}
        {page.history.length > 0 && (
          <section className="aside-sec"><h2 className="mini-h">{t("Historique")}</h2>
            <ol className="wiki-history">
              {page.history.slice(0, 8).map((h) => (
                <li key={h.revision}>
                  <b>{t("Version {n}", { n: h.revision })}</b> · {h.author === "user" ? t("toi") : "Claude"}
                  {h.reason && ` · ${REASON[h.reason] ?? h.reason}`} · {ago(h.created_at)}
                </li>
              ))}
            </ol>
          </section>
        )}
      </aside>
    </div>
  );
}
