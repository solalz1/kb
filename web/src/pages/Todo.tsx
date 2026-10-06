import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type TodoAction } from "../api";

const GROUPS: Record<string, string> = {
  try: "À tester", read: "À lire", watch: "À regarder", follow: "À suivre", buy: "À acheter", do: "À faire",
};

export default function Todo() {
  const [actions, setActions] = useState<TodoAction[]>([]);
  const [showDone, setShowDone] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => { api.actions(showDone).then((a) => { setActions(a); setLoaded(true); }); }, [showDone]);

  const toggle = async (a: TodoAction) => {
    await api.setAction(a.id, !a.done);
    setActions((list) => list.map((x) => (x.id === a.id ? { ...x, done: !x.done } : x)));
  };

  const groups = useMemo(() => {
    const g: Record<string, TodoAction[]> = {};
    actions.forEach((a) => (g[a.kind || "do"] ??= []).push(a));
    return Object.entries(g).sort(([a], [b]) => Object.keys(GROUPS).indexOf(a) - Object.keys(GROUPS).indexOf(b));
  }, [actions]);

  return (
    <div className="page">
      <h1 className="title">À faire</h1>
      <p className="muted" style={{ maxWidth: "60ch" }}>
        Ce que tes sauvegardes suggèrent de faire : outils à tester, papiers à lire, comptes à suivre.
      </p>
      <label style={{ display: "inline-flex", gap: 8, alignItems: "center", marginTop: 6 }}>
        <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} /> Afficher aussi ce qui est fait
      </label>

      {loaded && actions.length === 0 && (
        <div className="empty"><p>Rien en attente. Les actions apparaissent ici quand un élément sauvegardé en suggère.</p></div>
      )}

      {groups.map(([kind, list]) => (
        <div key={kind} className="todo-group">
          <h2>{GROUPS[kind] ?? "Autres"}</h2>
          {list.map((a) => (
            <div key={a.id} className={`todo-item${a.done ? " done" : ""}`}>
              <input type="checkbox" checked={a.done} onChange={() => toggle(a)} aria-label={a.text} />
              <div>
                <div className="txt">{a.text}</div>
                <div className="from">
                  <span className="dot" data-kind={a.item_kind ?? undefined} />
                  <Link to={`/item/${a.item_id}`}>{a.item_title || "Source"}</Link>
                </div>
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
