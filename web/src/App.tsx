import { BookOpen, ListChecks, MessageSquare, Newspaper, Plus, Settings as Cog, Sprout } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ApiError, api, auth } from "./api";
import { HOME_EVENT, ui } from "./ui";
import Add from "./pages/Add";
import Ask from "./pages/Ask";
import Digest from "./pages/Digest";
import Feed from "./pages/Feed";
import Interests from "./pages/Interests";
import ItemPage from "./pages/ItemPage";
import NoteEditor from "./pages/NoteEditor";
import Settings from "./pages/Settings";
import Todo from "./pages/Todo";

const NAV = [
  { to: "/", label: "Veille", icon: BookOpen, end: true, tab: true },
  { to: "/perso", label: "Perso", icon: Sprout, tab: true },
  { to: "/digest", label: "Digest", icon: Newspaper, tab: true },
  { to: "/ask", label: "Demander", icon: MessageSquare, tab: true },
  { to: "/add", label: "Ajouter", icon: Plus, tab: false },   // on phones: the + in the top bar
  { to: "/todo", label: "À faire", icon: ListChecks, tab: true },
];

function Login({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState("");
  const [base, setBase] = useState(auth.base);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(Boolean(auth.base));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    auth.base = base;
    auth.token = token;
    try {
      await api.stats();
      onDone();
    } catch (err) {
      auth.token = "";
      setError(err instanceof ApiError && err.status === 401
        ? "Ce jeton ne correspond pas à KB_API_TOKEN sur le serveur."
        : "Serveur injoignable. Vérifie l'adresse de l'API.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="fiche" data-kind="article" onSubmit={submit}>
        <div className="fiche-head"><span className="kind">KB</span><span className="when">connexion</span></div>
        <h3 style={{ fontSize: 22, lineHeight: "28px" }}>Ta knowledge base</h3>
        <p className="ruled">Colle le jeton défini dans la variable KB_API_TOKEN de ton serveur. Il reste sur cet appareil.</p>
        <label className="lbl" htmlFor="token">Jeton d'accès</label>
        <input id="token" className="field" type="password" autoComplete="current-password" value={token}
               onChange={(e) => setToken(e.target.value)} required />
        {advanced ? (
          <>
            <label className="lbl" htmlFor="base">Adresse de l'API</label>
            <input id="base" className="field" placeholder="https://kb.exemple.com" value={base} onChange={(e) => setBase(e.target.value)} />
            <div className="hint">Seulement si l'app et l'API sont hébergées séparément.</div>
          </>
        ) : null}
        {error && <div className="error-box">{error}</div>}
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 18, flexWrap: "wrap" }}>
          <button className="btn primary" disabled={busy || !token}>Se connecter</button>
          {!advanced && <button type="button" className="linkish" onClick={() => setAdvanced(true)}>API hébergée ailleurs ?</button>}
        </div>
      </form>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(Boolean(auth.token));
  const [pending, setPending] = useState(0);

  useEffect(() => {
    const out = () => { auth.token = ""; setAuthed(false); };
    window.addEventListener("kb:unauthorized", out);
    return () => window.removeEventListener("kb:unauthorized", out);
  }, []);

  useEffect(() => {
    if (!authed) return;
    const tick = () => api.stats().then((s) => setPending(s.open_actions)).catch(() => {});
    tick();
    const t = setInterval(tick, 60_000);
    return () => clearInterval(t);
  }, [authed]);

  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  const logout = () => { auth.token = ""; setAuthed(false); };

  if (ui.version === "classic") return <ClassicShell pending={pending} onLogout={logout} />;
  return <Shell pending={pending} onLogout={logout} />;
}

function Pages({ onLogout }: { onLogout: () => void }) {
  return (
    <Routes>
      <Route path="/" element={<Feed key="main" space="main" />} />
      <Route path="/perso" element={<Feed key="perso" space="perso" />} />
      <Route path="/note/new" element={<NoteEditor key="new" />} />
      <Route path="/note/:id/edit" element={<NoteEditor key="edit" />} />
      <Route path="/item/:id" element={<ItemPage />} />
      <Route path="/ask" element={<Ask />} />
      <Route path="/digest" element={<Digest />} />
      <Route path="/digest/interets" element={<Interests />} />
      <Route path="/digest/:id" element={<Digest />} />
      <Route path="/add" element={<Add />} />
      <Route path="/todo" element={<Todo />} />
      <Route path="/settings" element={<Settings onLogout={onLogout} />} />
      <Route path="*" element={<Feed key="main" space="main" />} />
    </Routes>
  );
}

/** Phone-first shell: a raised + in the middle of the tab bar, to-dos and settings at the top. */
function Shell({ pending, onLogout }: { pending: number; onLogout: () => void }) {
  const { pathname } = useLocation();
  // Tapping the tab of the page already on screen brings you back to the top (and to the search).
  const retap = (to: string, end?: boolean) => (e: React.MouseEvent) => {
    const here = end ? pathname === to : pathname === to || pathname.startsWith(`${to}/`);
    if (!here) return;
    e.preventDefault();
    window.dispatchEvent(new CustomEvent(HOME_EVENT, { detail: to }));
  };
  const tabs = NAV.filter((n) => n.tab && n.to !== "/todo");
  const left = tabs.slice(0, 2), right = tabs.slice(2);
  const tab = ({ to, label, icon: Icon, end }: (typeof NAV)[number]) => (
    <NavLink key={to} to={to} end={end} onClick={retap(to, end)}><Icon size={22} strokeWidth={1.9} /><span>{label}</span></NavLink>
  );

  return (
    <div className="shell">
      <nav className="rail" aria-label="Navigation">
        <div className="brand">KB <small>second cerveau</small></div>
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end}>
            <Icon size={18} /> {label}
            {to === "/todo" && pending > 0 && <span className="badge">{pending}</span>}
          </NavLink>
        ))}
        <div className="spacer" />
        <NavLink to="/settings"><Cog size={18} /> Réglages</NavLink>
      </nav>

      <main>
        <header className="topbar">
          <NavLink to="/" end className="brand" aria-label="Veille">KB</NavLink>
          <span className="topbar-actions">
            <NavLink to="/todo" aria-label={pending > 0 ? `À faire, ${pending} en attente` : "À faire"} className="icon-link">
              <ListChecks size={22} strokeWidth={1.9} />
              {pending > 0 && <span className="count">{pending > 99 ? "99+" : pending}</span>}
            </NavLink>
            <NavLink to="/settings" aria-label="Réglages" className="icon-link"><Cog size={22} strokeWidth={1.9} /></NavLink>
          </span>
        </header>
        <Pages onLogout={onLogout} />
      </main>

      <nav className="tabbar" aria-label="Navigation">
        {left.map(tab)}
        <NavLink to="/add" className="add" aria-label="Ajouter à ta KB"><Plus size={28} strokeWidth={2.2} /></NavLink>
        {right.map(tab)}
      </nav>
    </div>
  );
}

/** The original shell, kept as is for « Design classique » in Réglages. */
function ClassicShell({ pending, onLogout }: { pending: number; onLogout: () => void }) {
  return (
    <div className="shell">
      <nav className="rail" aria-label="Navigation">
        <div className="brand">KB <small>second cerveau</small></div>
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end}>
            <Icon size={18} /> {label}
            {to === "/todo" && pending > 0 && <span className="badge">{pending}</span>}
          </NavLink>
        ))}
        <div className="spacer" />
        <NavLink to="/settings"><Cog size={18} /> Réglages</NavLink>
      </nav>

      <main>
        <header className="topbar">
          <span className="brand">KB</span>
          <span className="topbar-actions">
            <NavLink to="/add" aria-label="Ajouter"><Plus size={22} /></NavLink>
            <NavLink to="/settings" aria-label="Réglages"><Cog size={20} /></NavLink>
          </span>
        </header>
        <Pages onLogout={onLogout} />
      </main>

      <nav className="tabbar" aria-label="Navigation">
        {NAV.filter((n) => n.tab).map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end}><Icon size={21} />{label}</NavLink>
        ))}
      </nav>
    </div>
  );
}
