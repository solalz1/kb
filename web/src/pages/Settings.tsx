import { Copy, Download, ExternalLink, LogOut, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, auth, type NotionStatus } from "../api";
import { claudePrefs } from "../claude";
import { Costs } from "../components/Costs";
import { lang, setLang, t } from "../i18n";
import { ago, KINDS } from "../kinds";

export default function Settings({ onLogout }: { onLogout: () => void }) {
  const [stats, setStats] = useState<Awaited<ReturnType<typeof api.stats>> | null>(null);
  const [copied, setCopied] = useState("");
  const [exporting, setExporting] = useState<"" | "md" | "files">("");
  const [desktop, setDesktop] = useState(claudePrefs.desktop);
  const [notion, setNotion] = useState<NotionStatus | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [exportLang, setExportLang] = useState<string>(lang);
  const [thinking, setThinking] = useState<boolean | null>(null);
  const [thinkingError, setThinkingError] = useState("");
  const origin = auth.base || window.location.origin;

  const loadNotion = () => api.notion().then(setNotion).catch(() => {});
  useEffect(() => {
    api.stats().then(setStats).catch(() => {});
    loadNotion();
    api.thinking().then((r) => setThinking(r.enabled)).catch(() => {});
  }, []);

  const toggleThinking = async (enabled: boolean) => {
    setThinking(enabled);
    setThinkingError("");
    try { setThinking((await api.setThinking(enabled)).enabled); } catch (e) { setThinking(!enabled); setThinkingError((e as Error).message); }
  };

  const syncNow = async () => {
    setSyncing(true);
    try {
      await api.notionSync();
      await new Promise((r) => setTimeout(r, 2500));
      await loadNotion();
    } finally {
      setSyncing(false);
    }
  };

  const copy = async (label: string, text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(label); setTimeout(() => setCopied(""), 1500); } catch { /* presse-papiers refusé */ }
  };

  const download = async (files: boolean) => {
    setExporting(files ? "files" : "md");
    try {
      const blob = await api.exportZip(files, exportLang);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `kb-export-${new Date().toISOString().slice(0, 10)}.zip`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 30_000);   // Safari iOS lit le fichier après le clic
    } finally {
      setExporting("");
    }
  };

  return (
    <div className="page settings-page">
      <h1 className="title">{t("Réglages")}</h1>
      <SectionNav />

      <section className="section" id="langue">
        <h2>{t("Langue")}</h2>
        <div className="modes small" role="group" aria-label={t("Langue")}>
          <button type="button" lang="fr" aria-pressed={lang === "fr"} onClick={() => lang !== "fr" && setLang("fr")}>Français</button>
          <button type="button" lang="en" aria-pressed={lang === "en"} onClick={() => lang !== "en" && setLang("en")}>English</button>
        </div>
        <p className="hint">{t("L'interface, les résumés et les réponses de Claude passent dans cette langue. Tes notes restent telles que tu les as écrites.")}</p>
      </section>

      {stats && (
        <div className="stats">
          <div><b>{stats.total}</b>{t("éléments")}</div>
          <div><b>{stats.by_space?.perso ?? 0}</b>{t("en Perso")}</div>
          <div><b>{stats.this_week}</b>{t("cette semaine")}</div>
          <div><b>{stats.open_actions}</b>{t("actions en attente")}</div>
          {(stats.by_status.pending || 0) + (stats.by_status.processing || 0) > 0 && (
            <div><b>{(stats.by_status.pending || 0) + (stats.by_status.processing || 0)}</b>{t("en traitement")}</div>
          )}
          {stats.by_status.error > 0 && <div><b>{stats.by_status.error}</b>{t("en erreur")}</div>}
        </div>
      )}
      {stats && stats.by_kind.length > 0 && (
        <p className="muted">{stats.by_kind.map((k) => `${k.n} ${((k.n > 1 ? KINDS[k.kind]?.plural : KINDS[k.kind]?.label) ?? k.kind).toLowerCase()}`).join(", ")}</p>
      )}

      <section className="section" id="couts">
        <h2>{t("Coûts")}</h2>
        <Costs />
      </section>

      <section className="section" id="reflexion">
        <h2>{t("Réflexion de Claude")}</h2>
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
          <input type="checkbox" checked={thinking ?? true} disabled={thinking === null} style={{ marginTop: 4 }}
                 onChange={(e) => toggleThinking(e.target.checked)} />
          <span>{t("Laisser Claude réfléchir avant de répondre")}
            <span className="hint" style={{ display: "block", marginTop: 2 }}>
              {t("Pour les réponses du chat, le digest et la lecture des PDF scannés et des vidéos : plus solide, un peu plus lent et plus cher. Décoché, Haiku 5.5 et Sonnet 5.5 répondent directement ; Opus 5.5 et Fable 5.1 réfléchissent toujours.")}
            </span></span>
        </label>
        {thinkingError && <div className="error-box">{thinkingError}</div>}
      </section>

      <section className="section" id="raccourcis">
        <h2>{t("Raccourcis iPhone et Mac")}</h2>
        <p>{t("Les Raccourcis envoient ce que tu partages à cette adresse, avec ton jeton :")}</p>
        <div className="code">{origin}/api/ingest</div>
        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <button className="btn small" onClick={() => copy("url", `${origin}/api/ingest`)}><Copy size={14} /> {copied === "url" ? t("Copié") : t("Copier l'adresse")}</button>
          <button className="btn small" onClick={() => copy("token", auth.token)}><Copy size={14} /> {copied === "token" ? t("Copié") : t("Copier le jeton")}</button>
        </div>
        <p className="hint">{t("Le pas-à-pas complet est dans SHORTCUT.md, à la racine du dépôt.")}</p>
      </section>

      <section className="section" id="connecteur">
        <h2>{t("Connecteur Claude")}</h2>
        <p>{t("Dans Claude (Réglages, Connecteurs, « Ajouter un connecteur personnalisé »), colle cette URL en remplaçant la fin par ton secret")} <code>KB_MCP_SECRET</code>{t(" :")}</p>
        <div className="code">{origin}/mcp/{t("TON_SECRET_MCP")}</div>
        <p className="hint">{t("Tu pourras alors interroger ta KB depuis Claude sur iPhone, Mac et le web, et depuis Claude Code.")}</p>
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", marginTop: 14 }}>
          <input type="checkbox" checked={desktop} style={{ marginTop: 4 }}
                 onChange={(e) => { claudePrefs.desktop = e.target.checked; setDesktop(e.target.checked); }} />
          <span>{t("Ouvrir « Demander dans Claude » dans l'app Claude pour Mac plutôt que dans le navigateur")}
            <span className="hint" style={{ display: "block", marginTop: 2 }}>{t("Réglage propre à cet appareil. À laisser décoché sur iPhone.")}</span></span>
        </label>
      </section>

      <section className="section" id="notion">
        <h2>{t("Copie dans Notion")}</h2>
        {!notion ? null : notion.configured ? (
          <>
            <div className="notion-state">
              <span>{t((notion.synced ?? 0) > 1 ? "{n} éléments copiés" : "{n} élément copié", { n: notion.synced ?? 0 })}
                {notion.pending ? t(", {n} en attente", { n: notion.pending }) : t(", tout est à jour")}
                {notion.spaces.length === 1 ? t(" (espace {space} seulement)", { space: notion.spaces[0] === "perso" ? t("Perso") : t("Veille") }) : ""}</span>
              {notion.last_sync_at && <span className="muted">{t("Dernière synchro {when}", { when: ago(notion.last_sync_at) })}</span>}
              {notion.last_error && <span className="err">{t("Dernière erreur :")} {notion.last_error}</span>}
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {notion.url && <a className="btn small" href={notion.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> {t("Ouvrir la base Notion")}</a>}
              <button className="btn small" onClick={syncNow} disabled={syncing}>
                <RefreshCw size={14} className={syncing ? "spin" : undefined} /> {syncing ? t("Synchronisation…") : t("Synchroniser maintenant")}</button>
            </div>
            {(notion.languages?.length ?? 0) > 1 && (
              <div className="segmented-row">
                <span className="muted">{t("Langue de la copie")}</span>
                <LangPicker value={notion.language ?? "fr"} options={notion.languages!} disabled={syncing}
                            onChange={async (l) => { if (l !== notion.language && window.confirm(t("Recopier ta KB dans une nouvelle base Notion en {language} ? L'ancienne base reste dans Notion, tu pourras la supprimer.", { language: LANG_NAMES[l] ?? l }))) setNotion(await api.notionLanguage(l)); }} />
              </div>
            )}
            <p className="hint">{t("Chaque élément a sa page dans une base Notion, mise à jour à chaque modification : une deuxième copie de ta KB, lisible partout. Modifie tes notes dans l'app : les retouches faites dans Notion sont écrasées.")}</p>
          </>
        ) : (
          <p className="muted">{t("Pas encore activée. Ajoute")} {(notion.missing ?? []).map((m, i) => <span key={m}>{i ? t(" et ") : ""}<code>{m}</code></span>)} {t("dans les variables du serveur (voir SETUP.md, étape Notion) : chaque élément sera recopié dans une base Notion privée, en continu.")}</p>
        )}
      </section>

      <section className="section" id="donnees">
        <h2>{t("Tes données")}</h2>
        <p>{t("Export complet en Markdown : une note par élément (source, résumé, liens), rangée dans Veille ou Perso. Il s'ouvre dans Obsidian et s'importe dans Notion (Importer, puis Texte et Markdown).")}</p>
        <div className="segmented-row" style={{ marginBottom: 12 }}>
          <span className="muted">{t("Langue de l'export")}</span>
          <LangPicker value={exportLang} options={["fr", "en"]} onChange={setExportLang} />
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn" onClick={() => download(false)} disabled={Boolean(exporting)}><Download size={16} /> {exporting === "md" ? t("Préparation…") : t("Télécharger l'export")}</button>
          <button className="btn ghost" onClick={() => download(true)} disabled={Boolean(exporting)}><Download size={16} /> {exporting === "files" ? t("Préparation…") : t("Avec les fichiers d'origine")}</button>
        </div>
        <p className="hint">{t("La version avec les fichiers (PDF, images, audio) peut être lourde. Garde-en une copie de temps en temps : c'est ta sauvegarde hors ligne.")}</p>
      </section>

      <section className="section" id="appareil">
        <h2>{t("Cet appareil")}</h2>
        <p className="muted">{t("Connecté à {origin}.", { origin })}</p>
        <button className="btn danger" onClick={onLogout}><LogOut size={16} /> {t("Se déconnecter")}</button>
      </section>
    </div>
  );
}

const LANG_NAMES: Record<string, string> = { fr: "Français", en: "English" };

const SECTIONS: [string, string][] = [
  ["langue", t("Langue")], ["couts", t("Coûts")], ["reflexion", t("Réflexion|thinking")], ["raccourcis", t("Raccourcis")], ["connecteur", t("Connecteur Claude")],
  ["notion", t("Notion")], ["donnees", t("Tes données")], ["appareil", t("Cet appareil")],
];

/** A strip of the page's sections that stays under the top bar: one tap to reach any of them. */
function SectionNav() {
  const [current, setCurrent] = useState(SECTIONS[0][0]);
  const picked = useRef(0);       // time of the last tap: the tapped section stays lit while the page scrolls to it
  useEffect(() => {
    // the section whose top last went past the strip is the one being read
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
  useEffect(() => {
    document.querySelector(`.settings-nav [data-to="${current}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [current]);
  return (
    <nav className="settings-nav" aria-label={t("Sections des réglages")}>
      {SECTIONS.map(([id, label]) => (
        <a key={id} href={`#${id}`} data-to={id} className="chip" aria-current={current === id ? "true" : undefined}
           onClick={(e) => {
             e.preventDefault();
             picked.current = Date.now();
             setCurrent(id);
             document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
           }}>
          {label}
        </a>
      ))}
    </nav>
  );
}

function LangPicker({ value, options, onChange, disabled }: {
  value: string; options: string[]; onChange: (lang: string) => void; disabled?: boolean;
}) {
  return (
    <div className="modes small" role="group">
      {options.map((l) => (
        <button key={l} type="button" lang={l} aria-pressed={value === l} disabled={disabled} onClick={() => onChange(l)}>
          {LANG_NAMES[l] ?? l}
        </button>
      ))}
    </div>
  );
}
