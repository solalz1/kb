import { useMemo, useState } from "react";
import { api, type TodoAction } from "../api";
import { useQuery } from "../cache";
import { Lines } from "../components/Skeleton";
import { t } from "../i18n";
import { IconBack } from "../icons";
import { Link } from "../nav";
import { keys } from "../queries";

const GROUPS: Record<string, string> = {
  try: t("À tester"), read: t("À lire"), watch: t("À regarder"), follow: t("À suivre"), buy: t("À acheter"), do: t("À faire"),
};

/** What saved items suggest doing. Ticking one updates the badges by itself: the change refreshes the counts. */
export default function Todo() {
  const [showDone, setShowDone] = useState(false);
  const query = useQuery(keys.actions(showDone), () => api.actions(showDone), { keep: true });
  // what was ticked or unticked on this visit stays where it is (under "Fait"), so it can be unticked again,
  // even once the list from the server leaves done actions out
  const [ticked, setTicked] = useState<ReadonlyMap<number, TodoAction>>(new Map());
  const [error, setError] = useState("");
  const actions = useMemo(() => {
    const list = query.data ?? [];
    const ids = new Set(list.map((a) => a.id));
    return [...list.map((a) => ticked.get(a.id) ?? a), ...[...ticked.values()].filter((a) => !ids.has(a.id))];
  }, [query.data, ticked]);
  const loaded = Boolean(query.data);

  const toggle = async (a: TodoAction) => {
    const next = { ...a, done: !a.done };
    setTicked((m) => new Map(m).set(a.id, next));
    setError("");
    try {
      await api.setAction(a.id, next.done);
    } catch (e) {
      setTicked((m) => new Map(m).set(a.id, a));
      setError((e as Error).message);
    }
  };

  const groups = useMemo(() => {
    const g: Record<string, TodoAction[]> = {};
    actions.filter((a) => !a.done).forEach((a) => (g[a.kind || "do"] ??= []).push(a));
    return Object.entries(g).sort(([a], [b]) => Object.keys(GROUPS).indexOf(a) - Object.keys(GROUPS).indexOf(b));
  }, [actions]);
  const done = actions.filter((a) => a.done);

  const row = (a: TodoAction) => (
    <label key={a.id} className={`todo-item${a.done ? " done" : ""}`}>
      <input type="checkbox" checked={a.done} onChange={() => toggle(a)} aria-label={a.text} />
      <span className="txt">
        <span className="t">{a.text}</span>
        {!a.done && (
          <span className="from">
            <span className="dot" data-kind={a.item_kind ?? undefined} />
            <Link to={`/item/${a.item_id}`} onClick={(e) => e.stopPropagation()}>{a.item_title || t("Source")}</Link>
          </span>
        )}
      </span>
    </label>
  );

  return (
    <div className="page">
      <Link className="back phone-only" to="/"><IconBack size={20} /> {t("Veille")}</Link>
      <div className="title-block">
        <h1 className="title">{t("À faire")}</h1>
        <p className="lede">{t("Ce que tes sauvegardes suggèrent de faire : outils à tester, papiers à lire, comptes à suivre.")}</p>
      </div>
      <label className="check-line">
        <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} /> {t("Afficher aussi ce qui est fait")}
      </label>

      {!loaded && <Lines n={4} widths={["70%", "84%", "60%", "76%"]} />}
      {error && <div className="error-box">{error}</div>}
      {loaded && actions.length === 0 && (
        <div className="empty"><p>{t("Rien en attente. Les actions apparaissent ici quand un élément sauvegardé en suggère.")}</p></div>
      )}

      <div className="todo-groups">
        {groups.map(([kind, list]) => (
          <section key={kind} className="todo-group">
            <h2>{GROUPS[kind] ?? t("Autres")}</h2>
            {list.map(row)}
          </section>
        ))}
        {done.length > 0 && (
          <section className="todo-group">
            <h2 className="done-h">{t("Fait")}</h2>
            {done.map(row)}
          </section>
        )}
      </div>
    </div>
  );
}
