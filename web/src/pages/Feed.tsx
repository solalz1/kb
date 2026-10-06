import { Compass, NotebookPen, Search, Shuffle, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ItemSummary, type Kind, type Space } from "../api";
import { Fiche } from "../components/Fiche";
import { t } from "../i18n";
import { FILTER_ORDER, KINDS } from "../kinds";
import { CATEGORIES, categoryOf } from "../perso";

const PAGE = 30;

export default function Feed({ space }: { space: Space }) {
  const perso = space === "perso";
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") || "";
  const category = params.get("category") || "";
  const tag = params.get("tag") || "";
  const entity = params.get("entity") || "";
  const [q, setQ] = useState(params.get("q") || "");
  const [query, setQuery] = useState(q);
  const [items, setItems] = useState<ItemSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [searching, setSearching] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [resurface, setResurface] = useState<ItemSummary[]>([]);
  const [tags, setTags] = useState<{ tag: string; count: number }[]>([]);
  const [entities, setEntities] = useState<{ name: string; count: number }[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const reqId = useRef(0);

  // recherche « au fil de la frappe », avec un léger délai
  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);

  const load = async (offset = 0) => {
    const id = ++reqId.current;
    if (offset === 0) setLoading(true);
    try {
      const res = await api.items({ q: query || undefined, kind: kind || undefined, tag: tag || undefined,
                                    entity: entity || undefined, category: category || undefined, space,
                                    limit: PAGE, offset });
      if (id !== reqId.current) return;
      setItems((prev) => (offset ? [...prev, ...res.items] : res.items));
      setTotal(res.total);
      setSearching(res.search);
      setError("");
    } catch (e) {
      if (id === reqId.current) setError((e as Error).message);
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  };

  useEffect(() => { load(0); }, [query, kind, tag, entity, category]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    api.resurface(space).then(setResurface).catch(() => {});
    api.tags(space).then((t) => setTags(t.slice(0, 40))).catch(() => {});
    api.entities(space).then((e) => setEntities(e.slice(0, 24))).catch(() => {});
    if (perso) api.categories().then((c) => setCounts(Object.fromEntries(c.map((x) => [x.id, x.count])))).catch(() => {});
  }, [space, perso]);

  // tant que des éléments sont en cours de traitement, on rafraîchit
  const hasPending = items.some((i) => i.status === "pending" || i.status === "processing");
  useEffect(() => {
    if (!hasPending || searching) return;
    const t = setInterval(() => load(0), 4000);
    return () => clearInterval(t);
  }, [hasPending, searching]); // eslint-disable-line react-hooks/exhaustive-deps

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    value ? next.set(key, value) : next.delete(key);
    setParams(next, { replace: true });
  };

  const activeFilters = useMemo(
    () => [tag && { key: "tag", label: `#${tag}` }, entity && { key: "entity", label: entity }].filter(Boolean) as { key: string; label: string }[],
    [tag, entity],
  );
  const browsing = !query && !kind && !tag && !entity && !category;
  const charterCount = (counts.principe || 0) + (counts.valeur || 0);
  const current = categoryOf(category);
  const newNoteHref = `/note/new${current ? `?category=${current.id}` : ""}`;

  return (
    <div className="page wide">
      <div className="feed-layout">
        <div>
          {perso && (
            <header className="space-head">
              <div>
                <h1 className="title">{t("Perso")}</h1>
                <p className="muted">{t("Tes principes, tes valeurs, tes leçons et tes objectifs. Le mode Conseil s'appuie dessus pour t'aider à décider.")}</p>
              </div>
              <div className="space-actions">
                <Link className="btn primary" to={newNoteHref}><NotebookPen size={16} /> {t("Nouvelle note")}</Link>
                <Link className="btn" to="/ask?mode=advice"><Compass size={16} /> {t("Demander conseil")}</Link>
              </div>
            </header>
          )}

          <div className="searchbar">
            <Search size={18} />
            <label className="sr-only" htmlFor="q">{perso ? t("Chercher dans tes notes perso") : t("Chercher dans ta veille")}</label>
            <input id="q" className="field" type="search" autoComplete="off" enterKeyHint="search"
                   placeholder={perso ? t("Chercher une leçon, un principe, un souvenir…") : t("Chercher un sujet, une idée, une personne…")}
                   value={q} onChange={(e) => setQ(e.target.value)} />
          </div>

          {perso ? (
            <div className="chips" role="group" aria-label={t("Filtrer par catégorie")}>
              {CATEGORIES.map((c) => (
                <button key={c.id} className="chip" aria-pressed={category === c.id}
                        onClick={() => setFilter("category", category === c.id ? "" : c.id)}>
                  {c.plural}{counts[c.id] ? <span className="n">{counts[c.id]}</span> : null}
                </button>
              ))}
            </div>
          ) : (
            <div className="chips" role="group" aria-label={t("Filtrer par type")}>
              {FILTER_ORDER.map((k: Kind) => (
                <button key={k} className="chip" aria-pressed={kind === k} onClick={() => setFilter("kind", kind === k ? "" : k)}>
                  <span className="dot" data-kind={k} />{KINDS[k].plural}
                </button>
              ))}
            </div>
          )}
          {activeFilters.length > 0 && (
            <div className="chips">
              {activeFilters.map((f) => (
                <button key={f.key} className="chip" aria-pressed="true" onClick={() => setFilter(f.key, "")}>
                  {f.label}<X size={14} className="x" aria-label={t("Retirer le filtre")} />
                </button>
              ))}
            </div>
          )}

          {perso && !loading && browsing && items.length > 0 && charterCount === 0 && (
            <div className="warn charter-tip">
              {t("Écris d'abord quelques {principles} et {values} : le mode Conseil les relit en entier à chaque question.")
                .split(/(\{principles\}|\{values\})/).map((part, i) =>
                  part === "{principles}" ? <Link key={i} to="/note/new?category=principe">{t("principes")}</Link>
                  : part === "{values}" ? <Link key={i} to="/note/new?category=valeur">{t("valeurs")}</Link>
                  : part)}
            </div>
          )}

          {browsing && resurface.length > 0 && (
            <>
              <div className="resurface-title mobile"><Shuffle size={15} /> {perso ? t("Écrit il y a un moment, toujours vrai ?") : t("Sauvé il y a un moment, toujours d'actualité")}</div>
              <div className="resurface">
                {resurface.map((it) => <Fiche key={it.id} item={it} compact />)}
              </div>
            </>
          )}

          {error && <div className="error-box">{error}</div>}

          {!loading && items.length === 0 && !error && (
            browsing ? (
              perso ? (
                <div className="empty">
                  <h2 className="title" style={{ fontSize: 22 }}>{t("Ton espace perso est vide")}</h2>
                  <p>{t("Commence par tes principes et tes valeurs : ce sont eux que le mode Conseil relit en entier pour t'aider à décider. "
                    + "Ensuite, note tes leçons, tes objectifs, ton journal.")}</p>
                  <div className="space-actions" style={{ justifyContent: "center" }}>
                    <Link className="btn primary" to="/note/new?category=principe">{t("Écrire un principe")}</Link>
                    <Link className="btn" to="/note/new?category=valeur">{t("Écrire une valeur")}</Link>
                  </div>
                  <p className="hint">{t("Depuis le bouton Partager, ajoute {tag} à ta note pour ranger un lien ici.")
                    .split(/(\{tag\})/).map((part, i) => (part === "{tag}" ? <code key={i}>#perso</code> : part))}</p>
                </div>
              ) : (
                <div className="empty">
                  <h2 className="title" style={{ fontSize: 22 }}>{t("Ta KB est vide pour l'instant")}</h2>
                  <p>{t("Partage un tweet, un article ou un PDF avec le Raccourci « Ajouter à ma KB », ou colle un lien ici.")}</p>
                  <Link className="btn primary" to="/add">{t("Ajouter un premier élément")}</Link>
                </div>
              )
            ) : (
              <div className="empty">
                <p>{t("Rien ne correspond. Essaie d'autres mots ou retire un filtre.")}</p>
                {perso && current && <Link className="btn" to={newNoteHref}><NotebookPen size={16} /> {t("Nouvelle note · {category}", { category: current.label })}</Link>}
              </div>
            )
          )}

          {items.length > 0 && (
            <div className="feed-meta">
              {searching
                ? (items.length > 1 ? t("{n} résultats les plus proches", { n: items.length }) : t("{n} résultat le plus proche", { n: items.length }))
                : (total > 1 ? t("{n} éléments", { n: total }) : t("{n} élément", { n: total }))}
            </div>
          )}
          {items.map((it) => <Fiche key={it.id} item={it} />)}

          {!searching && items.length < total && (
            <div style={{ textAlign: "center", marginTop: 18 }}>
              <button className="btn" onClick={() => load(items.length)}>{t("Afficher plus")}</button>
            </div>
          )}
        </div>

        <aside className="aside" aria-label={t("Explorer")}>
          {resurface.length > 0 && (
            <section>
              <h2>{t("À redécouvrir")}</h2>
              {resurface.map((it) => <Fiche key={it.id} item={it} compact />)}
            </section>
          )}
          {tags.length > 0 && (
            <section>
              <h2>{t("Tags")}</h2>
              <div className="tagcloud">
                {tags.map((t) => (
                  <button key={t.tag} onClick={() => setFilter("tag", t.tag)}>#{t.tag}<span className="n">{t.count}</span></button>
                ))}
              </div>
            </section>
          )}
          {entities.length > 0 && (
            <section>
              <h2>{perso ? t("Personnes et idées") : t("Personnes, outils, concepts")}</h2>
              <div className="tagcloud">
                {entities.map((e) => (
                  <button key={e.name} onClick={() => setFilter("entity", e.name)}>{e.name}<span className="n">{e.count}</span></button>
                ))}
              </div>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}
