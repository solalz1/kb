import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, auth, type NotionStatus } from "../api";
import { claudePrefs } from "../claude";
import { Costs, dollars } from "../components/Costs";
import { LangSwitch } from "../components/LangSwitch";
import { lang, t } from "../i18n";
import { IconBack, IconNext } from "../icons";
import { ago, KINDS } from "../kinds";
import { useDesktop } from "../layout";

type Stats = Awaited<ReturnType<typeof api.stats>>;

const SECTIONS: [string, string][] = [
  ["langue", t("Langue")], ["couts", t("Coûts")], ["reflexion", t("Réflexion|thinking")], ["raccourcis", t("Raccourcis")],
  ["connecteur", t("Connecteur Claude")], ["notion", t("Notion")], ["donnees", t("Tes données")], ["appareil", t("Cet appareil")],
];
const LANG_NAMES: Record<string, string> = { fr: "Français", en: "English" };

/** Desktop: every section on one page, with a strip of links to them. Phone: grouped rows, each opening its section. */
export default function Settings({ onLogout }: { onLogout: () => void }) {
  const { section } = useParams();
  const desktop = useDesktop();
  const [stats, setStats] = useState<Stats | null>(null);
  const origin = auth.base || window.location.origin;

  useEffect(() => { api.stats().then(setStats).catch(() => {}); }, []);
  useEffect(() => {
    if (desktop && section) document.getElementById(section)?.scrollIntoView({ block: "start" });
  }, [desktop, section]);

  if (!desktop && section && SECTIONS.some(([id]) => id === section)) {
    const label = SECTIONS.find(([id]) => id === section)![1];
    return (
      <div className="page tight">
        <Link className="back" to="/settings"><IconBack size={20} /> {t("Réglages")}</Link>
        <h1 className="title">{section === "reflexion" ? t("Réflexion de Claude") : label}</h1>
        <section className="column" id={section} style={{ gap: 12 }}><SectionBody id={section} origin={origin} onLogout={onLogout} /></section>
      </div>
    );
  }

  if (!desktop) return <PhoneSettings stats={stats} origin={origin} onLogout={onLogout} />;

  return (
    <div className="page">
      <h1 className="title">{t("Réglages")}</h1>
      <SectionNav />
      <div className="settings-cards">
        <Card id="langue" title={t("Langue")}><SectionBody id="langue" origin={origin} onLogout={onLogout} /></Card>
        {stats && <StatTiles stats={stats} all />}
        {stats && stats.by_kind.length > 0 && (
          <p className="hint stats-note">{stats.by_kind.map((k) => `${k.n} ${((k.n > 1 ? KINDS[k.kind]?.plural : KINDS[k.kind]?.label) ?? k.kind).toLowerCase()}`).join(", ")}</p>
        )}
        {SECTIONS.slice(1).map(([id, label]) => (
          <Card key={id} id={id} title={id === "reflexion" ? t("Réflexion de Claude") : id === "raccourcis" ? t("Raccourcis iPhone et Mac")
                                       : id === "notion" ? t("Copie dans Notion") : label}>
            <SectionBody id={id} origin={origin} onLogout={onLogout} />
          </Card>
        ))}
      </div>
    </div>
  );
}

function Card({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return <section className="card" id={id}><h2>{title}</h2>{children}</section>;
}

function StatTiles({ stats, all = false }: { stats: Stats; all?: boolean }) {
  const processing = (stats.by_status.pending || 0) + (stats.by_status.processing || 0);
  return (
    <section className="stat-tiles" aria-label={t("Statistiques")}>
      <div><b>{stats.total}</b><span>{t("éléments")}</span></div>
      <div><b>{stats.by_space?.perso ?? 0}</b><span>{t("en Perso")}</span></div>
      <div><b>{stats.this_week}</b><span>{t("cette semaine")}</span></div>
      <div><b>{stats.open_actions}</b><span>{t("actions en attente")}</span></div>
      {all && processing > 0 && <div><b>{processing}</b><span>{t("en traitement")}</span></div>}
      {all && stats.by_status.error > 0 && <div className="err"><b>{stats.by_status.error}</b><span>{t("en erreur")}</span></div>}
    </section>
  );
}

function PhoneSettings({ stats, origin, onLogout }: { stats: Stats | null; origin: string; onLogout: () => void }) {
  const [thinking, setThinking] = useState<boolean | null>(null);
  const [desktopApp, setDesktopApp] = useState(claudePrefs.desktop);
  const [month, setMonth] = useState<number | null>(null);
  const [notion, setNotion] = useState<NotionStatus | null>(null);
  useEffect(() => {
    api.thinking().then((r) => setThinking(r.enabled)).catch(() => {});
    api.costs().then((c) => setMonth(c.month)).catch(() => {});
    api.notion().then(setNotion).catch(() => {});
  }, []);
  const toggleThinking = async (enabled: boolean) => {
    setThinking(enabled);
    try { setThinking((await api.setThinking(enabled)).enabled); } catch { setThinking(!enabled); }
  };
  const notionLine = !notion ? "" : !notion.configured ? t("Pas encore activée")
    : `${notion.pending ? t("{n} en attente", { n: notion.pending }) : t("Tout est à jour")}${notion.last_sync_at ? ` · ${t("synchro {when}", { when: ago(notion.last_sync_at) })}` : ""}`;
  const rows: [string, string, string][] = [
    ["couts", t("Coûts"), month === null ? "" : t("{amount} ce mois-ci", { amount: dollars(month) })],
    ["raccourcis", t("Raccourcis iPhone et Mac"), t("Copier l'adresse et le jeton")],
    ["connecteur", t("Connecteur Claude"), t("URL MCP pour Claude et Claude Code")],
    ["notion", t("Copie dans Notion"), notionLine],
    ["donnees", t("Tes données"), t("Export Markdown, avec ou sans fichiers")],
  ];

  return (
    <div className="page tight">
      <Link className="back" to="/"><IconBack size={20} /> {t("Veille")}</Link>
      <h1 className="title">{t("Réglages")}</h1>
      {stats && <StatTiles stats={stats} />}
      <div className="group">
        <div className="group-row"><span className="txt"><b>{t("Langue")}</b></span><LangSwitch /></div>
        <label className="group-row">
          <span className="txt"><b>{t("Réflexion de Claude")}</b><span>{t("Réfléchir avant de répondre")}</span></span>
          <input type="checkbox" checked={thinking ?? true} disabled={thinking === null} onChange={(e) => toggleThinking(e.target.checked)}
                 aria-label={t("Laisser Claude réfléchir avant de répondre")} />
        </label>
        <label className="group-row">
          <span className="txt"><b>{t("Ouvrir dans l'app Claude pour Mac")}</b><span>{t("À laisser décoché sur iPhone")}</span></span>
          <input type="checkbox" checked={desktopApp} onChange={(e) => { claudePrefs.desktop = e.target.checked; setDesktopApp(e.target.checked); }} />
        </label>
      </div>
      <nav className="group" aria-label={t("Sections des réglages")}>
        {rows.map(([id, label, sub]) => (
          <Link key={id} className="group-row" to={`/settings/${id}`}>
            <span className="txt"><b>{label}</b>{sub && <span>{sub}</span>}</span>
            <IconNext size={18} />
          </Link>
        ))}
      </nav>
      <div className="device-box">
        <b>{t("Cet appareil")}</b>
        <span>{t("Connecté à {origin}", { origin: hostOf(origin) })}</span>
        <button className="btn small danger" onClick={onLogout}>{t("Se déconnecter")}</button>
      </div>
    </div>
  );
}

const hostOf = (url: string) => { try { return new URL(url).host; } catch { return url; } };

/** The content of one section, the same on the desktop page and on a phone's section page. */
function SectionBody({ id, origin, onLogout }: { id: string; origin: string; onLogout: () => void }) {
  switch (id) {
    case "langue":
      return (
        <>
          <LangSwitch size="sm" />
          <p className="hint" style={{ fontSize: 14 }}>{t("L'interface, les résumés et les réponses de Claude passent dans cette langue. Tes notes restent telles que tu les as écrites.")}</p>
        </>
      );
    case "couts": return <Costs />;
    case "reflexion": return <Thinking />;
    case "raccourcis": return <Shortcuts origin={origin} />;
    case "connecteur": return <Connector origin={origin} />;
    case "notion": return <Notion />;
    case "donnees": return <Export />;
    case "appareil":
      return (
        <>
          <p className="body">{t("Connecté à {origin}.", { origin: hostOf(origin) })}</p>
          <button className="btn small danger" style={{ alignSelf: "flex-start" }} onClick={onLogout}>{t("Se déconnecter")}</button>
        </>
      );
    default: return null;
  }
}

function Thinking() {
  const [thinking, setThinking] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.thinking().then((r) => setThinking(r.enabled)).catch(() => {}); }, []);
  const toggle = async (enabled: boolean) => {
    setThinking(enabled);
    setError("");
    try { setThinking((await api.setThinking(enabled)).enabled); } catch (e) { setThinking(!enabled); setError((e as Error).message); }
  };
  return (
    <>
      <label className="check-row">
        <input type="checkbox" checked={thinking ?? true} disabled={thinking === null} onChange={(e) => toggle(e.target.checked)} />
        <span>{t("Laisser Claude réfléchir avant de répondre")}</span>
      </label>
      <p className="hint" style={{ fontSize: 14 }}>{t("Pour les réponses du chat, le digest et la lecture des PDF scannés et des vidéos : plus solide, un peu plus lent et plus cher. Décoché, Haiku 5.5 et Sonnet 5.5 répondent directement ; Opus 5.5 et Fable 5.1 réfléchissent toujours.")}</p>
      {error && <div className="error-box">{error}</div>}
    </>
  );
}

function useCopy(): [string, (label: string, text: string) => void] {
  const [copied, setCopied] = useState("");
  const copy = async (label: string, text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(label); setTimeout(() => setCopied(""), 1500); } catch { /* clipboard refused */ }
  };
  return [copied, copy];
}

function Shortcuts({ origin }: { origin: string }) {
  const [copied, copy] = useCopy();
  return (
    <>
      <p className="body">{t("Les Raccourcis envoient ce que tu partages à cette adresse, avec ton jeton :")}</p>
      <code className="code">{origin}/api/ingest</code>
      <div className="btn-row">
        <button className="btn small" onClick={() => copy("url", `${origin}/api/ingest`)}>{copied === "url" ? t("Copié") : t("Copier l'adresse")}</button>
        <button className="btn small" onClick={() => copy("token", auth.token)}>{copied === "token" ? t("Copié") : t("Copier le jeton")}</button>
      </div>
      <p className="hint">{t("« Où le ranger ? » propose tes dossiers tels qu'ils sont dans l'app : un dossier ajouté ici y apparaît tout seul. Le pas-à-pas complet est dans SHORTCUT.md, à la racine du dépôt.")}</p>
    </>
  );
}

function Connector({ origin }: { origin: string }) {
  const [desktopApp, setDesktopApp] = useState(claudePrefs.desktop);
  return (
    <>
      <p className="body">{t("Dans Claude (Réglages, Connecteurs, « Ajouter un connecteur personnalisé »), colle cette URL en remplaçant la fin par ton secret")} <code>KB_MCP_SECRET</code>{t(" :")}</p>
      <code className="code">{origin}/mcp/{t("TON_SECRET_MCP")}</code>
      <p className="hint">{t("Tu pourras alors interroger ta KB depuis Claude sur iPhone, Mac et le web, et depuis Claude Code.")}</p>
      <label className="check-row top">
        <input type="checkbox" checked={desktopApp} onChange={(e) => { claudePrefs.desktop = e.target.checked; setDesktopApp(e.target.checked); }} />
        <span className="txt"><span style={{ fontSize: 15, color: "var(--ink)" }}>{t("Ouvrir « Demander dans Claude » dans l'app Claude pour Mac plutôt que dans le navigateur")}</span>
          <span>{t("Réglage propre à cet appareil. À laisser décoché sur iPhone.")}</span></span>
      </label>
    </>
  );
}

function LangPicker({ value, options, onChange, disabled }: { value: string; options: string[]; onChange: (l: string) => void; disabled?: boolean }) {
  return (
    <div className="seg sm" role="group">
      {options.map((l) => (
        <button key={l} type="button" lang={l} aria-pressed={value === l} disabled={disabled} onClick={() => onChange(l)}>{LANG_NAMES[l] ?? l}</button>
      ))}
    </div>
  );
}

function Notion() {
  const [notion, setNotion] = useState<NotionStatus | null>(null);
  const [syncing, setSyncing] = useState(false);
  const load = () => api.notion().then(setNotion).catch(() => {});
  useEffect(() => { load(); }, []);
  const syncNow = async () => {
    setSyncing(true);
    try { await api.notionSync(); await new Promise((r) => setTimeout(r, 2500)); await load(); } finally { setSyncing(false); }
  };
  if (!notion) return null;
  if (!notion.configured) {
    return <p className="body">{t("Pas encore activée. Ajoute")} {(notion.missing ?? []).map((m, i) => <span key={m}>{i ? t(" et ") : ""}<code>{m}</code></span>)} {t("dans les variables du serveur (voir SETUP.md, étape Notion) : chaque élément sera recopié dans une base Notion privée, en continu.")}</p>;
  }
  return (
    <>
      <p className="body">
        {t((notion.synced ?? 0) > 1 ? "{n} éléments copiés" : "{n} élément copié", { n: notion.synced ?? 0 })}
        {notion.pending ? t(", {n} en attente", { n: notion.pending }) : t(", tout est à jour")}
        {notion.spaces.length === 1 ? t(" (espace {space} seulement)", { space: notion.spaces[0] === "perso" ? t("Perso") : t("Veille") }) : ""}
        {notion.last_sync_at && <> · {t("Dernière synchro {when}", { when: ago(notion.last_sync_at) })}</>}
      </p>
      {notion.last_error && <p className="hint" style={{ color: "var(--accent-ink)" }}>{t("Dernière erreur :")} {notion.last_error}</p>}
      <div className="btn-row">
        {notion.url && <a className="btn small" href={notion.url} target="_blank" rel="noreferrer">{t("Ouvrir la base Notion")}</a>}
        <button className="btn small" onClick={syncNow} disabled={syncing}>{syncing ? t("Synchronisation…") : t("Synchroniser maintenant")}</button>
      </div>
      {(notion.languages?.length ?? 0) > 1 && (
        <div className="inline-row">
          <span>{t("Langue de la copie")}</span>
          <LangPicker value={notion.language ?? "fr"} options={notion.languages!} disabled={syncing}
                      onChange={async (l) => { if (l !== notion.language && window.confirm(t("Recopier ta KB dans une nouvelle base Notion en {language} ? L'ancienne base reste dans Notion, tu pourras la supprimer.", { language: LANG_NAMES[l] ?? l }))) setNotion(await api.notionLanguage(l)); }} />
        </div>
      )}
      <p className="hint">{t("Chaque élément a sa page dans une base Notion, mise à jour à chaque modification. Modifie tes notes dans l'app : les retouches faites dans Notion sont écrasées.")}</p>
    </>
  );
}

function Export() {
  const [exporting, setExporting] = useState<"" | "md" | "files">("");
  const [exportLang, setExportLang] = useState<string>(lang);
  const download = async (files: boolean) => {
    setExporting(files ? "files" : "md");
    try {
      const blob = await api.exportZip(files, exportLang);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `kb-export-${new Date().toISOString().slice(0, 10)}.zip`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 30_000);   // Safari on iOS reads the file after the click
    } finally {
      setExporting("");
    }
  };
  return (
    <>
      <p className="body">{t("Export complet en Markdown : une note par élément (source, résumé, liens), rangée dans Veille ou Perso. Il s'ouvre dans Obsidian et s'importe dans Notion.")}</p>
      <div className="inline-row">
        <span>{t("Langue de l'export")}</span>
        <LangPicker value={exportLang} options={["fr", "en"]} onChange={setExportLang} />
      </div>
      <div className="btn-row">
        <button className="btn small dark" onClick={() => download(false)} disabled={Boolean(exporting)}>{exporting === "md" ? t("Préparation…") : t("Télécharger l'export")}</button>
        <button className="btn small" onClick={() => download(true)} disabled={Boolean(exporting)}>{exporting === "files" ? t("Préparation…") : t("Avec les fichiers d'origine")}</button>
      </div>
      <p className="hint">{t("La version avec les fichiers (PDF, images, audio) peut être lourde. Garde-en une copie de temps en temps : c'est ta sauvegarde hors ligne.")}</p>
    </>
  );
}

/** The strip of links to the page's sections; the one being read is lit. */
function SectionNav() {
  const [current, setCurrent] = useState(SECTIONS[0][0]);
  const picked = useRef(0);       // time of the last click: the clicked section stays lit while the page scrolls to it
  useEffect(() => {
    const onScroll = () => {
      if (Date.now() - picked.current < 1200) return;
      let id = SECTIONS[0][0];
      for (const [sid] of SECTIONS) {
        const el = document.getElementById(sid);
        if (el && el.getBoundingClientRect().top < 160) id = sid;
      }
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) id = SECTIONS[SECTIONS.length - 1][0];
      setCurrent(id);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return (
    <nav className="settings-nav" aria-label={t("Sections des réglages")}>
      {SECTIONS.map(([id, label]) => (
        <a key={id} href={`#${id}`} aria-current={current === id ? "true" : undefined}
           onClick={(e) => {
             e.preventDefault();
             picked.current = Date.now();
             setCurrent(id);
             document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
           }}>{label}</a>
      ))}
    </nav>
  );
}
