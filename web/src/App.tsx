import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Outlet, ScrollRestoration, useLocation } from "react-router-dom";
import { ApiError, api, auth, goSignIn, sso } from "./api";
import { cache, useQuery } from "./cache";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { EdgeSwipeBack, PullToRefresh, settleSwipe } from "./components/Gestures";
import { LangSwitch } from "./components/LangSwitch";
import { TitleBar } from "./components/TitleBar";
import { t } from "./i18n";
import { IconCalendar, IconChat, IconDigest, IconFeed, IconFolder, IconLeaf, IconPlus, IconSettings, IconTodo, IconWiki } from "./icons";
import { devCopy, pressFeedback, scrollKey, stepsToRoot, tabOf } from "./motion";
import { Link, NavLink, useNavigate } from "./nav";
import { keys, prefetchTabs } from "./queries";

// The sidebar (desktop). "Ajouter" is the primary button above it; on phones it is the + in the middle of the tab bar,
// the wiki, the folders and À faire are icons at the top of Veille, and the journal an icon at the top of Perso.
const NAV = [
  { to: "/", label: t("Veille"), Icon: IconFeed, end: true },
  { to: "/perso", label: t("Perso"), Icon: IconLeaf },
  { to: "/wiki", label: t("Wiki"), Icon: IconWiki },
  { to: "/folders", label: t("Dossiers"), Icon: IconFolder },
  { to: "/journal", label: t("Journal"), Icon: IconCalendar },
  { to: "/digest", label: "Digest", Icon: IconDigest },
  { to: "/ask", label: t("Demander"), Icon: IconChat },
  { to: "/todo", label: t("À faire"), Icon: IconTodo },
];
const TABS = [NAV[0], NAV[1], { to: "/add", label: t("Ajouter"), Icon: IconPlus }, NAV[5], NAV[6]];
const RAIL_STEP = 50;     // px between two sidebar links (44 + the 6 px gap): the selection slides by that much

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
      cache.clear();
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

/** The app around every page: the sidebar or the tab bar, gestures, scroll positions, and the page (routes in main.tsx). */
export default function App() {
  // true with a token on this device; null while finding out whether the shared sign-in's cookie opens the app
  const [authed, setAuthed] = useState<boolean | null>(auth.token ? true : null);
  const location = useLocation();
  const navigate = useNavigate();
  const tab = useRef("/");
  tab.current = tabOf(location.pathname, tab.current);
  const opened = /^\/(item|note)\//.test(location.pathname);    // an item or a note: lit where it was opened from
  const stats = useQuery(authed ? keys.stats : null, api.stats, { maxAge: 20_000, poll: 60_000 });
  const pending = stats.data?.open_actions ?? 0;

  // Shared sign-in (SSO_URL on the server): without a token, the session cookie opens the app, or it's off to the
  // sign-in page. Without it, the token form as before.
  useEffect(() => {
    let live = true;
    (async () => {
      try { sso.login = (await api.health()).sso_login ?? ""; } catch { /* offline: the token form */ }
      if (!live || auth.token) return;
      if (!sso.login) { setAuthed(false); return; }
      try { await api.stats(); if (live) setAuthed(true); }
      catch (err) { if (live && !(err instanceof ApiError && err.status === 401)) setAuthed(false); }
    })();
    return () => { live = false; };
  }, []);

  // a token the server refuses, an expired session, or signing out in Settings
  useEffect(() => {
    const out = () => { auth.token = ""; cache.clear(); if (sso.login) goSignIn(); else setAuthed(false); };
    window.addEventListener("kb:unauthorized", out);
    return () => window.removeEventListener("kb:unauthorized", out);
  }, []);

  // once the first page is up, the other tabs are fetched ahead, so that opening them shows them at once
  useEffect(() => {
    if (!authed) return;
    const idle = (window as Window & { requestIdleCallback?: (cb: () => void) => number }).requestIdleCallback;
    const timer = window.setTimeout(() => (idle ? idle(prefetchTabs) : prefetchTabs()), 1200);
    return () => clearTimeout(timer);
  }, [authed]);

  // back to the app after a while (it stayed open in the background): what's on screen refreshes quietly
  useEffect(() => {
    let hiddenAt = 0;
    const onVisibility = () => {
      if (document.hidden) hiddenAt = Date.now();
      else if (hiddenAt && Date.now() - hiddenAt > 60_000) cache.staleAll();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // what a finger presses answers it once it is clearly a tap (motion.ts)
  useEffect(() => pressFeedback(), []);

  // ⌘K (or Ctrl+K, or / when nothing is being typed), from any page: the search of the feed, as in a Mac app
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof Element && e.target.closest("input, textarea, select, [contenteditable='true']");
      const wanted = ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") || (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey);
      if (!wanted) return;
      e.preventDefault();
      const field = () => document.querySelector<HTMLInputElement>(".main .search input");
      if (field()) { field()!.focus(); return; }
      navigate("/");
      window.setTimeout(() => field()?.focus(), 120);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // a page swiped away from the left edge: the one that comes back sits in place
  useLayoutEffect(() => { settleSwipe(); }, [location.key]);
  const back = useCallback(() => navigate(-1), []); // eslint-disable-line react-hooks/exhaustive-deps

  if (authed === null) return <div className="login"><div className="login-top"><b>KB</b></div></div>;
  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  // the tab bar's lens sits on the current tab; tapping the tab you're on brings its page back to the top
  const lens = TABS.findIndex((x) => x.to === tab.current && x.to !== "/add");
  const here = NAV.findIndex(({ to }) => (to === "/" ? location.pathname === "/"
    : location.pathname === to || location.pathname.startsWith(`${to}/`)));
  const railLens = here >= 0 ? here : opened ? NAV.findIndex(({ to }) => to === tab.current) : -1;
  const onTab = (e: React.MouseEvent, to: string) => {
    if (location.pathname === to && !location.search) {
      e.preventDefault();
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    // the tab you're in, from a page opened in it: back to its first screen, as on an iPhone
    const steps = tab.current === to ? stepsToRoot(to) : 0;
    if (steps) { e.preventDefault(); navigate(-steps); }
  };

  return (
    <div className="shell">
      <ScrollRestoration getKey={scrollKey} />
      <nav className="rail" aria-label="Navigation">
        <Link className="brand" to="/"><b>KB</b><small>{t("second cerveau")}</small>{devCopy() && <span className="dev-tag">DEV</span>}</Link>
        <Link className="add-btn" to="/add"><IconPlus size={18} /> {t("Ajouter")}</Link>
        <div className="rail-links" style={{ "--i": Math.max(0, railLens), "--step": `${RAIL_STEP}px` } as React.CSSProperties}>
          <span className={`rail-lens${railLens < 0 ? " off" : ""}`} aria-hidden="true" />
          {NAV.map(({ to, label, Icon, end }) => (
            <NavLink key={to} to={to} end={end} onClick={(e) => onTab(e, to)} viewTransition={false}
                     className={({ isActive }) => `nav${isActive || (opened && tab.current === to) ? " active" : ""}`}>
              <Icon size={20} /> <span className="label">{label}</span>
              {to === "/todo" && pending > 0 && <span className="badge">{pending}</span>}
            </NavLink>
          ))}
        </div>
        <div className="spacer" />
        <NavLink to="/settings" viewTransition={false} className={({ isActive }) => `nav settings${isActive ? " active" : ""}`}>
          <IconSettings size={20} /> <span className="label">{t("Réglages")}</span>
        </NavLink>
      </nav>

      <main className="main">
        <ErrorBoundary resetKey={location.key}>
          <Outlet />
        </ErrorBoundary>
      </main>

      <nav className="tabbar" aria-label="Navigation" style={{ "--i": Math.max(0, lens) } as React.CSSProperties}>
        <span className={`tab-lens${lens < 0 ? " off" : ""}`} aria-hidden="true" />
        {TABS.map(({ to, label, Icon }) => to === "/add"
          ? <Link key={to} to={to} className={`add${tab.current === to ? " active" : ""}`} aria-label={label} viewTransition={false} replace
                  aria-current={tab.current === to ? "page" : undefined}><span><Icon size={24} stroke={2.25} /></span></Link>
          : <Link key={to} to={to} className={tab.current === to ? "active" : undefined} onClick={(e) => onTab(e, to)}
                  viewTransition={false} replace
                  aria-current={tab.current === to ? "page" : undefined}><Icon size={24} />{label}</Link>)}
      </nav>
      <TitleBar />
      {devCopy() && <span className="dev-tag floating" aria-hidden="true">DEV</span>}
      <PullToRefresh />
      <EdgeSwipeBack onBack={back} />
    </div>
  );
}
