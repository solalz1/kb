import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type TodoAction } from "../api";
import { t } from "../i18n";
import { IconBack } from "../icons";

const GROUPS: Record<string, string> = {
  try: t("À tester"), read: t("À lire"), watch: t("À regarder"), follow: t("À suivre"), buy: t("À acheter"), do: t("À faire"),
};

/** `onChange`: the number of open actions, for the badges. */
export default function Todo({ onChange }: { onChange?: (open: number) => void }) {
  const [actions, setActions] = useState<TodoAction[]>([]);
  const [showDone, setShowDone] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => { api.actions(showDone).then((a) => { setActions(a); setLoaded(true); }); }, [showDone]);

  const toggle = async (a: TodoAction) => {
    await api.setAction(a.id, !a.done);
    const next = actions.map((x) => (x.id === a.id ? { ...x, done: !x.done } : x));
    setActions(next);
    if (!showDone) onChange?.(next.filter((x) => !x.done).length);
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
