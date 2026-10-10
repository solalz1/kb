import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ApiError, api, auth } from "./api";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { LangSwitch } from "./components/LangSwitch";
import { t } from "./i18n";
import { IconCalendar, IconChat, IconDigest, IconFeed, IconFolder, IconLeaf, IconPlus, IconSettings, IconTodo } from "./icons";
import Add from "./pages/Add";
import Ask from "./pages/Ask";
import Digest from "./pages/Digest";
import Feed from "./pages/Feed";
import Folders from "./pages/Folders";
import Interests from "./pages/Interests";
import ItemPage from "./pages/ItemPage";
import Journal from "./pages/Journal";
import NoteEditor from "./pages/NoteEditor";
import Settings from "./pages/Settings";
import Todo from "./pages/Todo";

// The sidebar (desktop). "Ajouter" is the primary button above it; on phones it is the + in the middle of the tab bar,
// the folders and À faire are icons at the top of Veille, and the journal an icon at the top of Perso.
const NAV = [
  { to: "/", label: t("Veille"), Icon: IconFeed, end: true },
  { to: "/perso", label: t("Perso"), Icon: IconLeaf },
  { to: "/folders", label: t("Dossiers"), Icon: IconFolder },
  { to: "/journal", label: t("Journal"), Icon: IconCalendar },
  { to: "/digest", label: "Digest", Icon: IconDigest },
  { to: "/ask", label: t("Demander"), Icon: IconChat },
  { to: "/todo", label: t("À faire"), Icon: IconTodo },
];
const TABS = [NAV[0], NAV[1], { to: "/add", label: t("Ajouter"), Icon: IconPlus }, NAV[4], NAV[5]];

/** The tab a page belongs to. An item page or a note belongs to the tab it was opened from; Réglages to none. */
function tabOf(path: string, previous: string): string {
  if (path === "/" || path.startsWith("/todo") || path.startsWith("/folders")) return "/";
  if (path.startsWith("/perso") || path.startsWith("/journal")) return "/perso";
  if (path.startsWith("/digest")) return "/digest";
  if (path.startsWith("/ask")) return "/ask";
  if (path.startsWith("/add")) return "/add";
  if (path.startsWith("/item/") || path.startsWith("/note/")) return previous;
  return "";
}

const hostOf = (base: string) => { try { return new URL(base).host; } catch { return base; } };

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
        ? t("Ce jeton ne correspond pas à KB_API_TOKEN sur le serveur.")
        : t("Serveur injoignable. Vérifie l'adresse de l'API."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <div className="login-top"><b>KB</b><LangSwitch /></div>
      <form onSubmit={submit}>
        <div className="intro">
          <h1>{t("Ta knowledge base")}</h1>
          <p>{t("Colle le jeton défini dans la variable KB_API_TOKEN de ton serveur. Il reste sur cet appareil.")}</p>
        </div>
        <label className="stack">
          <span className="label">{t("Jeton d'accès")}</span>
          <input className="field" type="password" autoComplete="current-password" placeholder="••••••••••••" value={token}
                 onChange={(e) => setToken(e.target.value)} required />
        </label>
        {advanced && (
          <label className="stack">
            <span className="label">{t("Adresse de l'API")}</span>
            <input className="field" placeholder="https://kb.exemple.com" value={base} onChange={(e) => setBase(e.target.value)} />
            <span className="hint">{t("Seulement si l'app et l'API sont hébergées séparément.")}</span>
          </label>
        )}
        {error && <div className="error-box">{error}</div>}
        <button className="btn primary" disabled={busy}>{t("Se connecter")}</button>
        {!advanced && <button type="button" className="linkish" onClick={() => setAdvanced(true)}>{t("API hébergée ailleurs ?")}</button>}
      </form>
      <p className="login-foot">{t("second cerveau")} · {hostOf(auth.base || window.location.origin)}</p>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(Boolean(auth.token));
  const location = useLocation();
  const [pending, setPending] = useState(0);
  const tab = useRef("/");
  tab.current = tabOf(location.pathname, tab.current);
  const opened = /^\/(item|note)\//.test(location.pathname);    // an item or a note: lit where it was opened from

  useEffect(() => {
    const out = () => { auth.token = ""; setAuthed(false); };
    window.addEventListener("kb:unauthorized", out);
    return () => window.removeEventListener("kb:unauthorized", out);
  }, []);

  useEffect(() => {
    if (!authed) return;
    const tick = () => api.stats().then((s) => setPending(s.open_actions)).catch(() => {});
    tick();
    const timer = setInterval(tick, 60_000);
    return () => clearInterval(timer);
  }, [authed, location.pathname === "/todo"]);

  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  const logout = () => { auth.token = ""; setAuthed(false); };

  return (
    <div className="shell">
      <nav className="rail" aria-label="Navigation">
        <Link className="brand" to="/"><b>KB</b><small>{t("second cerveau")}</small></Link>
        <Link className="add-btn" to="/add"><IconPlus size={18} /> {t("Ajouter")}</Link>
        {NAV.map(({ to, label, Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav${isActive || (opened && tab.current === to) ? " active" : ""}`}>
            <Icon size={20} /> <span className="label">{label}</span>
            {to === "/todo" && pending > 0 && <span className="badge">{pending}</span>}
          </NavLink>
        ))}
        <div className="spacer" />
        <NavLink to="/settings" className={({ isActive }) => `nav settings${isActive ? " active" : ""}`}>
          <IconSettings size={20} /> <span className="label">{t("Réglages")}</span>
        </NavLink>
      </nav>

      <main className="main">
        <ErrorBoundary resetKey={location.key}>
          <Routes>
            <Route path="/" element={<Feed key="main" space="main" pending={pending} />} />
            <Route path="/perso" element={<Feed key="perso" space="perso" />} />
            <Route path="/folders" element={<Folders />} />
            <Route path="/folders/:folderId" element={<Feed key="folder" />} />
            <Route path="/journal" element={<Journal />} />
            <Route path="/journal/:day" element={<Journal />} />
            <Route path="/note/new" element={<NoteEditor key="new" />} />
            <Route path="/note/:id/edit" element={<NoteEditor key="edit" />} />
            <Route path="/item/:id" element={<ItemPage />} />
            <Route path="/ask" element={<Ask />} />
            <Route path="/digest" element={<Digest />} />
            <Route path="/digest/interets" element={<Interests />} />
            <Route path="/digest/:id" element={<Digest />} />
            <Route path="/add" element={<Add />} />
            <Route path="/todo" element={<Todo onChange={setPending} />} />
            <Route path="/settings" element={<Settings onLogout={logout} />} />
            <Route path="/settings/:section" element={<Settings onLogout={logout} />} />
            <Route path="*" element={<Feed key="main" space="main" pending={pending} />} />
          </Routes>
        </ErrorBoundary>
      </main>

      <nav className="tabbar" aria-label="Navigation">
        {TABS.map(({ to, label, Icon }) => to === "/add"
          ? <Link key={to} to={to} className={`add${tab.current === to ? " active" : ""}`} aria-label={label}
                  aria-current={tab.current === to ? "page" : undefined}><span><Icon size={26} stroke={2.25} /></span></Link>
          : <Link key={to} to={to} className={tab.current === to ? "active" : undefined}
                  aria-current={tab.current === to ? "page" : undefined}><Icon size={24} />{label}</Link>)}
      </nav>
    </div>
  );
}
