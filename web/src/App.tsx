import { BookOpen, CalendarDays, ListChecks, MessageSquare, Newspaper, Plus, Settings as Cog, Sprout } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { ApiError, api, auth } from "./api";
import { lang, setLang, t } from "./i18n";
import Add from "./pages/Add";
import Ask from "./pages/Ask";
import Digest from "./pages/Digest";
import Feed from "./pages/Feed";
import Interests from "./pages/Interests";
import ItemPage from "./pages/ItemPage";
import Journal from "./pages/Journal";
import NoteEditor from "./pages/NoteEditor";
import Settings from "./pages/Settings";
import Todo from "./pages/Todo";

const NAV = [
  { to: "/", label: t("Veille"), icon: BookOpen, end: true, tab: true },
  { to: "/perso", label: t("Perso"), icon: Sprout, tab: true },
  { to: "/journal", label: t("Journal"), icon: CalendarDays, tab: false },   // on phones: from the Personal page
  { to: "/digest", label: "Digest", icon: Newspaper, tab: true },
  { to: "/ask", label: t("Demander"), icon: MessageSquare, tab: true },
  { to: "/add", label: t("Ajouter"), icon: Plus, tab: false },   // on phones: the + in the top bar
  { to: "/todo", label: t("À faire"), icon: ListChecks, tab: true },
];

/** FR / EN switch, as in solalzana.com's header. */
function LangSwitch() {
  return (
    <div className="lang" role="group" aria-label={t("Langue")}>
      {(["fr", "en"] as const).map((l) => (
        <button key={l} type="button" lang={l} aria-pressed={lang === l} onClick={() => lang !== l && setLang(l)}>
          {l.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

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
      <form className="fiche" data-kind="article" onSubmit={submit}>
        <div className="fiche-head"><span className="kind">KB</span><span className="when">{t("connexion")}</span></div>
        <div style={{ float: "right", marginTop: -2 }}><LangSwitch /></div>
        <h3 style={{ fontSize: 22, lineHeight: "28px" }}>{t("Ta knowledge base")}</h3>
        <p className="ruled">{t("Colle le jeton défini dans la variable KB_API_TOKEN de ton serveur. Il reste sur cet appareil.")}</p>
        <label className="lbl" htmlFor="token">{t("Jeton d'accès")}</label>
        <input id="token" className="field" type="password" autoComplete="current-password" value={token}
               onChange={(e) => setToken(e.target.value)} required />
        {advanced ? (
          <>
            <label className="lbl" htmlFor="base">{t("Adresse de l'API")}</label>
            <input id="base" className="field" placeholder="https://kb.exemple.com" value={base} onChange={(e) => setBase(e.target.value)} />
            <div className="hint">{t("Seulement si l'app et l'API sont hébergées séparément.")}</div>
          </>
        ) : null}
        {error && <div className="error-box">{error}</div>}
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 18, flexWrap: "wrap" }}>
          <button className="btn primary" disabled={busy || !token}>{t("Se connecter")}</button>
          {!advanced && <button type="button" className="linkish" onClick={() => setAdvanced(true)}>{t("API hébergée ailleurs ?")}</button>}
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

  return (
    <div className="shell">
      <nav className="rail" aria-label="Navigation">
        <div className="brand"><span className="brand-mark">KB</span><span>Knowledge base<small>{t("second cerveau")}</small></span></div>
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end}>
            <Icon size={18} /> {label}
            {to === "/todo" && pending > 0 && <span className="badge">{pending}</span>}
          </NavLink>
        ))}
        <div className="spacer" />
        <NavLink to="/settings"><Cog size={18} /> {t("Réglages")}</NavLink>
        <div className="rail-foot"><LangSwitch /></div>
      </nav>

      <main>
        <header className="topbar">
          <span className="brand"><span className="brand-mark">KB</span>Knowledge base</span>
          <span className="topbar-actions">
            <LangSwitch />
            <NavLink to="/add" aria-label={t("Ajouter")}><Plus size={22} /></NavLink>
            <NavLink to="/settings" aria-label={t("Réglages")}><Cog size={20} /></NavLink>
          </span>
        </header>
        <Routes>
          <Route path="/" element={<Feed key="main" space="main" />} />
          <Route path="/perso" element={<Feed key="perso" space="perso" />} />
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
          <Route path="/todo" element={<Todo />} />
          <Route path="/settings" element={<Settings onLogout={() => { auth.token = ""; setAuthed(false); }} />} />
          <Route path="*" element={<Feed key="main" space="main" />} />
        </Routes>
      </main>

      <nav className="tabbar" aria-label="Navigation">
        {NAV.filter((n) => n.tab).map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end}><Icon size={21} />{label}</NavLink>
        ))}
      </nav>
    </div>
  );
}
