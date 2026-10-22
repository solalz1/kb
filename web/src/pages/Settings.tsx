import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, auth, signOut } from "../api";
import { cache, useQuery } from "../cache";
import { claudePrefs } from "../claude";
import { Costs, dollars } from "../components/Costs";
import { LangSwitch } from "../components/LangSwitch";
import { lang, t } from "../i18n";
import { IconBack, IconNext } from "../icons";
import { ago, KINDS } from "../kinds";
import { useDesktop } from "../layout";
import { Link } from "../nav";
import { DEFAULT_VAULT, setVaultName, vaultName } from "../obsidian";
import { keys } from "../queries";
import { realOrigin } from "../motion";

type Stats = Awaited<ReturnType<typeof api.stats>>;

const SECTIONS: [string, string][] = [
  ["langue", t("Langue")], ["couts", t("Coûts")], ["reflexion", t("Réflexion|thinking")], ["raccourcis", t("Raccourcis")],
  ["connecteur", t("Connecteur Claude")], ["wiki", t("Wiki et Obsidian")], ["notion", t("Notion")], ["donnees", t("Tes données")],
  ["appareil", t("Cet appareil")],
];
const LANG_NAMES: Record<string, string> = { fr: "Français", en: "English" };

/** Desktop: every section on one page, with a strip of links to them. Phone: grouped rows, each opening its section. */
export default function Settings() {
  const onLogout = signOut;
  const { section } = useParams();
  const desktop = useDesktop();
  const stats = useQuery(keys.stats, api.stats).data ?? null;
  const origin = auth.base || window.location.origin;

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
        {stats ? <StatTiles stats={stats} all /> : <StatTilesSkeleton />}
        {stats && stats.by_kind.length > 0 && (
          <p className="hint stats-note">{stats.by_kind.map((k) => `${k.n} ${((k.n > 1 ? KINDS[k.kind]?.plural : KINDS[k.kind]?.label) ?? k.kind).toLowerCase()}`).join(", ")}</p>
        )}
        {SECTIONS.slice(1).map(([id, label]) => (
          <Card key={id} id={id} title={id === "reflexion" ? t("Réflexion de Claude") : id === "raccourcis" ? t("Raccourcis iPhone et Mac")
                                       : id === "notion" ? t("Copie dans Notion") : id === "wiki" ? t("Wiki et Obsidian") : label}>
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

/** The four tiles' place, kept while their figures arrive: the page doesn't jump when they do. */
// iPhone (Safari 17.4+): the checkbox is drawn as the system's switch, with its haptic; elsewhere a checkbox.
const SWITCH = { switch: "" } as Record<string, string>;

function StatTilesSkeleton() {
  return (
    <section className="stat-tiles" aria-hidden="true">
      {[0, 1, 2, 3].map((i) => <div key={i}><b><span className="sk" style={{ width: 44, height: 22 }} /></b><span>&nbsp;</span></div>)}
    </section>
  );
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
  const [thinking, toggleThinking] = useThinking();
  const [desktopApp, setDesktopApp] = useState(claudePrefs.desktop);
  const month = useQuery(keys.costs, api.costs).data?.month ?? null;
  const notion = useQuery(keys.notion, api.notion).data ?? null;
  const obsidian = useQuery(keys.wiki, api.wiki).data?.obsidian ?? null;
  const wikiLine = !obsidian ? "" : !obsidian.configured ? t("Synchro Obsidian pas encore activée")
    : obsidian.last_sync_at ? t("synchro {when}", { when: ago(obsidian.last_sync_at) }) : t("Synchro Obsidian activée");
  const notionLine = !notion ? "" : !notion.configured ? t("Pas encore activée")
    : `${notion.pending ? t("{n} en attente", { n: notion.pending }) : t("Tout est à jour")}${notion.last_sync_at ? ` · ${t("synchro {when}", { when: ago(notion.last_sync_at) })}` : ""}`;
  const rows: [string, string, string][] = [
    ["couts", t("Coûts"), month === null ? "" : t("{amount} ce mois-ci", { amount: dollars(month) })],
    ["raccourcis", t("Raccourcis iPhone et Mac"), t("Copier l'adresse et le jeton")],
    ["connecteur", t("Connecteur Claude"), t("URL MCP pour Claude et Claude Code")],
    ["wiki", t("Wiki et Obsidian"), wikiLine],
    ["notion", t("Copie dans Notion"), notionLine],
    ["donnees", t("Tes données"), t("Export Markdown, avec ou sans fichiers")],
  ];

  return (
    <div className="page tight">
      <Link className="back" to="/"><IconBack size={20} /> {t("Veille")}</Link>
      <h1 className="title">{t("Réglages")}</h1>
      {stats ? <StatTiles stats={stats} /> : <StatTilesSkeleton />}
      <div className="group">
        <div className="group-row"><span className="txt"><b>{t("Langue")}</b></span><LangSwitch /></div>
        <label className="group-row">
          <span className="txt"><b>{t("Réflexion de Claude")}</b><span>{t("Réfléchir avant de répondre")}</span></span>
          <input type="checkbox" {...SWITCH} checked={thinking ?? true} disabled={thinking === null} onChange={(e) => toggleThinking(e.target.checked)}
                 aria-label={t("Laisser Claude réfléchir avant de répondre")} />
        </label>
        <label className="group-row">
          <span className="txt"><b>{t("Ouvrir dans l'app Claude pour Mac")}</b><span>{t("À laisser décoché sur iPhone")}</span></span>
          <input type="checkbox" {...SWITCH} checked={desktopApp} onChange={(e) => { claudePrefs.desktop = e.target.checked; setDesktopApp(e.target.checked); }} />
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
    case "wiki": return <WikiSettings />;
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

/** Whether Claude thinks before answering, and a switch for it (shown at once, then saved). */
function useThinking(): [boolean | null, (enabled: boolean) => Promise<void>, string] {
  const thinking = useQuery(keys.thinking, api.thinking).data?.enabled ?? null;
  const [error, setError] = useState("");
  const toggle = async (enabled: boolean) => {
    cache.set(keys.thinking, { enabled });
    setError("");
    try { cache.set(keys.thinking, await api.setThinking(enabled)); } catch (e) { setError((e as Error).message); }
  };
  return [thinking, toggle, error];
}

function Thinking() {
  const [thinking, toggle, error] = useThinking();
  return (
    <>
      <label className="check-row">
        <input type="checkbox" {...SWITCH} checked={thinking ?? true} disabled={thinking === null} onChange={(e) => toggle(e.target.checked)} />
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

// The Shortcut and the Claude connector always go to the real KB, never to a copy that will be deleted (motion.ts).
const forTools = (origin: string) => (origin === window.location.origin ? realOrigin() : origin);

function Shortcuts({ origin: here }: { origin: string }) {
  const origin = forTools(here);
  const [copied, copy] = useCopy();
  return (
    <>
      <p className="body">{t("Les Raccourcis envoient ce que tu partages à cette adresse, avec ton jeton :")}</p>
      <code className="code">{origin}/api/ingest</code>
      <div className="btn-row">
        <button className="btn small" onClick={() => copy("url", `${origin}/api/ingest`)}>{copied === "url" ? t("Copié") : t("Copier l'adresse")}</button>
        {auth.token && <button className="btn small" onClick={() => copy("token", auth.token)}>{copied === "token" ? t("Copié") : t("Copier le jeton")}</button>}
      </div>
      {!auth.token && <p className="hint">{t("Le jeton est la variable KB_API_TOKEN de ton serveur : cet appareil n'en a pas besoin, il passe par la connexion commune.")}</p>}
      <p className="hint">{t("« Où le ranger ? » propose tes dossiers tels qu'ils sont dans l'app : un dossier ajouté ici y apparaît tout seul. Le pas-à-pas complet est dans SHORTCUT.md, à la racine du dépôt.")}</p>
    </>
  );
}

function Connector({ origin: here }: { origin: string }) {
  const origin = forTools(here);
  const [desktopApp, setDesktopApp] = useState(claudePrefs.desktop);
  return (
    <>
      <p className="body">{t("Dans Claude (Réglages, Connecteurs, « Ajouter un connecteur personnalisé »), colle cette URL en remplaçant la fin par ton secret")} <code>KB_MCP_SECRET</code>{t(" :")}</p>
      <code className="code">{origin}/mcp/{t("TON_SECRET_MCP")}</code>
      <p className="hint">{t("Tu pourras alors interroger ta KB depuis Claude sur iPhone, Mac et le web, et depuis Claude Code.")}</p>
      <label className="check-row top">
        <input type="checkbox" {...SWITCH} checked={desktopApp} onChange={(e) => { claudePrefs.desktop = e.target.checked; setDesktopApp(e.target.checked); }} />
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
  const query = useQuery(keys.notion, api.notion);
  const notion = query.data ?? null;
  const [syncing, setSyncing] = useState(false);
  const load = query.refresh;
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
                      onChange={async (l) => { if (l !== notion.language && window.confirm(t("Recopier ta KB dans une nouvelle base Notion en {language} ? L'ancienne base reste dans Notion, tu pourras la supprimer.", { language: LANG_NAMES[l] ?? l }))) cache.set(keys.notion, await api.notionLanguage(l)); }} />
        </div>
      )}
      <p className="hint">{t("Chaque élément a sa page dans une base Notion, mise à jour à chaque modification. Modifie tes notes dans l'app : les retouches faites dans Notion sont écrasées.")}</p>
    </>
  );
}

function WikiSettings() {
  const query = useQuery(keys.wiki, api.wiki);
  const obsidian = query.data?.obsidian ?? null;
  const schema = useQuery(keys.wikiSchema, api.wikiSchema);
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "sync" | "zip" | "save">("");
  const [notice, setNotice] = useState("");
  const [vault, setVault] = useState(vaultName());
  const shown = text ?? schema.data?.text ?? "";

  const syncNow = async () => {
    setBusy("sync");
    try { await api.wikiSync(); await new Promise((r) => setTimeout(r, 3000)); await query.refresh(); } finally { setBusy(""); }
  };
  const zip = async () => {
    setBusy("zip");
    try {
      const blob = await api.wikiZip();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `kb-wiki-${new Date().toISOString().slice(0, 10)}.zip`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 30_000);
    } finally { setBusy(""); }
  };
  const save = async () => {
    setBusy("save");
    try {
      const r = await api.setWikiSchema(shown);
      cache.set(keys.wikiSchema, r);
      setText(null);
      setNotice(t("Enregistré"));
      setTimeout(() => setNotice(""), 2500);
    } finally { setBusy(""); }
  };

  return (
    <>
      <p className="body">{t("Claude intègre chaque élément au wiki dès qu'il est prêt, et le relit chaque nuit. Le wiki s'ouvre aussi dans Obsidian, avec son graphe.")}</p>
      {obsidian && !obsidian.configured && (
        <p className="body">{t("Synchro Obsidian pas encore activée. Ajoute")} {(obsidian.missing ?? []).map((m, i) => <span key={m}>{i ? t(" et ") : ""}<code>{m}</code></span>)} {t("dans les variables du serveur (voir SETUP.md, étape Obsidian) : le wiki ira dans un dépôt GitHub privé que ton coffre Obsidian clone, et tes modifications faites dans Obsidian reviendront dans l'app.")}</p>
      )}
      {obsidian?.configured && (
        <>
          <p className="body">
            {t("Synchronisé avec {repo}", { repo: obsidian.repo ?? "" })}
            {obsidian.last_sync_at && <> · {t("Dernière synchro {when}", { when: ago(obsidian.last_sync_at) })}</>}
          </p>
          {obsidian.last_error && <p className="hint" style={{ color: "var(--accent-ink)" }}>{t("Dernière erreur :")} {obsidian.last_error}</p>}
          {(obsidian.skipped?.length ?? 0) > 0 && (
            <div className="warn">
              {t("Notes laissées de côté (renomme-les dans Obsidian pour qu'elles deviennent des pages) :")}
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{obsidian.skipped!.map((x) => <li key={x.path}><code>{x.path}</code> : {x.reason}</li>)}</ul>
            </div>
          )}
        </>
      )}
      <div className="btn-row">
        {obsidian?.configured && <button className="btn small" onClick={syncNow} disabled={Boolean(busy)}>{busy === "sync" ? t("Synchronisation…") : t("Synchroniser maintenant")}</button>}
        {obsidian?.url && <a className="btn small" href={obsidian.url} target="_blank" rel="noreferrer">{t("Ouvrir le dépôt")}</a>}
        <button className="btn small" onClick={zip} disabled={Boolean(busy)}>{busy === "zip" ? t("Préparation…") : t("Télécharger le coffre (zip)")}</button>
      </div>
      <p className="hint">{t("Dans Obsidian, tes modifications d'une page remplacent la version de l'app ; index.md, log.md et les notes de Sources sont réécrits par la KB.")}</p>
      <label className="stack">
        <span className="label">{t("Nom du coffre sur cet appareil")}</span>
        <input className="field" value={vault} placeholder={DEFAULT_VAULT} aria-label={t("Nom du coffre sur cet appareil")}
               onChange={(e) => setVault(e.target.value)} onBlur={() => { setVaultName(vault); setVault(vaultName()); }} />
        <span className="hint">{t("Les boutons « Ouvrir dans Obsidian » et « Chercher dans Obsidian » ouvrent le coffre qui porte ce nom (le nom du dossier ouvert comme coffre). Dans Obsidian, Cmd+G ouvre le graphe.")}</span>
      </label>
      <label className="stack">
        <span className="label">{t("Tes conventions pour Claude")} {notice && <span className="saved">· {notice}</span>}</span>
        <textarea className="field" rows={4} value={shown} onChange={(e) => setText(e.target.value)} aria-label={t("Tes conventions pour Claude")}
                  placeholder={t("ex. Une page par livre avec ses meilleures citations. En Recherche, finis chaque page par les questions ouvertes.")} />
        <span className="hint">{t("Claude les suit en écrivant chaque page. Tu peux aussi les modifier dans SCHEMA.md, dans Obsidian.")}</span>
      </label>
      <div className="btn-row">
        <button className="btn small dark" onClick={save} disabled={busy === "save" || text === null}>{t("Enregistrer les conventions")}</button>
      </div>
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
