import { Copy, Download, ExternalLink, LogOut, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api, auth, type NotionStatus } from "../api";
import { claudePrefs } from "../claude";
import { ago, KINDS } from "../kinds";
import { ui, type UiVersion } from "../ui";

export default function Settings({ onLogout }: { onLogout: () => void }) {
  const [stats, setStats] = useState<Awaited<ReturnType<typeof api.stats>> | null>(null);
  const [copied, setCopied] = useState("");
  const [exporting, setExporting] = useState<"" | "md" | "files">("");
  const [desktop, setDesktop] = useState(claudePrefs.desktop);
  const [notion, setNotion] = useState<NotionStatus | null>(null);
  const [syncing, setSyncing] = useState(false);
  const origin = auth.base || window.location.origin;
  const [look, setLook] = useState<UiVersion>(ui.version);
  const switchLook = (v: UiVersion) => { if (v === look) return; ui.version = v; setLook(v); window.location.reload(); };

  const loadNotion = () => api.notion().then(setNotion).catch(() => {});
  useEffect(() => { api.stats().then(setStats).catch(() => {}); loadNotion(); }, []);

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
      const blob = await api.exportZip(files);
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
    <div className="page">
      <h1 className="title">Réglages</h1>

      {stats && (
        <div className="stats">
          <div><b>{stats.total}</b>éléments</div>
          <div><b>{stats.by_space?.perso ?? 0}</b>en Perso</div>
          <div><b>{stats.this_week}</b>cette semaine</div>
          <div><b>{stats.open_actions}</b>actions en attente</div>
          {(stats.by_status.pending || 0) + (stats.by_status.processing || 0) > 0 && (
            <div><b>{(stats.by_status.pending || 0) + (stats.by_status.processing || 0)}</b>en traitement</div>
          )}
          {stats.by_status.error > 0 && <div><b>{stats.by_status.error}</b>en erreur</div>}
        </div>
      )}
      {stats && stats.by_kind.length > 0 && (
        <p className="muted">{stats.by_kind.map((k) => `${k.n} ${(KINDS[k.kind]?.plural ?? k.kind).toLowerCase()}`).join(", ")}</p>
      )}

      <section className="section">
        <h2>Apparence</h2>
        <div className="modes" role="group" aria-label="Interface">
          <button aria-pressed={look === "v2"} onClick={() => switchLook("v2")}>Nouveau design</button>
          <button aria-pressed={look === "classic"} onClick={() => switchLook("classic")}>Design classique</button>
        </div>
        <p className="hint">Le nouveau design est pensé pour le téléphone : le bouton Ajouter au centre, des fiches plus lisibles, tout à portée du pouce. Le classique est l'interface d'origine. Réglage propre à cet appareil.</p>
      </section>

      <section className="section">
        <h2>Raccourci iPhone et Mac</h2>
        <p>Le Raccourci « Ajouter à ma KB » envoie ce que tu partages à cette adresse, avec ton jeton :</p>
        <div className="code">{origin}/api/ingest</div>
        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <button className="btn small" onClick={() => copy("url", `${origin}/api/ingest`)}><Copy size={14} /> {copied === "url" ? "Copié" : "Copier l'adresse"}</button>
          <button className="btn small" onClick={() => copy("token", auth.token)}><Copy size={14} /> {copied === "token" ? "Copié" : "Copier le jeton"}</button>
        </div>
        <p className="hint">Le pas-à-pas complet est dans SHORTCUT.md, à la racine du dépôt.</p>
      </section>

      <section className="section">
        <h2>Connecteur Claude</h2>
        <p>Dans Claude (Réglages, Connecteurs, « Ajouter un connecteur personnalisé »), colle cette URL en remplaçant la fin par ton secret <code>KB_MCP_SECRET</code> :</p>
        <div className="code">{origin}/mcp/TON_SECRET_MCP</div>
        <p className="hint">Tu pourras alors interroger ta KB depuis Claude sur iPhone, Mac et le web, et depuis Claude Code.</p>
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", marginTop: 14 }}>
          <input type="checkbox" checked={desktop} style={{ marginTop: 4 }}
                 onChange={(e) => { claudePrefs.desktop = e.target.checked; setDesktop(e.target.checked); }} />
          <span>Ouvrir « Demander dans Claude » dans l'app Claude pour Mac plutôt que dans le navigateur
            <span className="hint" style={{ display: "block", marginTop: 2 }}>Réglage propre à cet appareil. À laisser décoché sur iPhone.</span></span>
        </label>
      </section>

      <section className="section">
        <h2>Copie dans Notion</h2>
        {!notion ? null : notion.configured ? (
          <>
            <div className="notion-state">
              <span>{notion.synced ?? 0} élément{(notion.synced ?? 0) > 1 ? "s" : ""} copié{(notion.synced ?? 0) > 1 ? "s" : ""}
                {notion.pending ? `, ${notion.pending} en attente` : ", tout est à jour"}
                {notion.spaces.length === 1 ? ` (espace ${notion.spaces[0] === "perso" ? "Perso" : "Veille"} seulement)` : ""}</span>
              {notion.last_sync_at && <span className="muted">Dernière synchro {ago(notion.last_sync_at)}</span>}
              {notion.last_error && <span className="err">Dernière erreur : {notion.last_error}</span>}
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {notion.url && <a className="btn small" href={notion.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Ouvrir la base Notion</a>}
              <button className="btn small" onClick={syncNow} disabled={syncing}>
                <RefreshCw size={14} className={syncing ? "spin" : undefined} /> {syncing ? "Synchronisation…" : "Synchroniser maintenant"}</button>
            </div>
            <p className="hint">Chaque élément a sa page dans une base Notion, mise à jour à chaque modification : une deuxième copie de ta KB, lisible partout. Modifie tes notes dans l'app : les retouches faites dans Notion sont écrasées.</p>
          </>
        ) : (
          <p className="muted">Pas encore activée. Ajoute {(notion.missing ?? []).map((m, i) => <span key={m}>{i ? " et " : ""}<code>{m}</code></span>)} dans les variables du serveur (voir SETUP.md, étape Notion) : chaque élément sera recopié dans une base Notion privée, en continu.</p>
        )}
      </section>

      <section className="section">
        <h2>Tes données</h2>
        <p>Export complet en Markdown : une note par élément (source, résumé, liens), rangée dans Veille ou Perso. Il s'ouvre dans Obsidian et s'importe dans Notion (Importer, puis Texte et Markdown).</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn" onClick={() => download(false)} disabled={Boolean(exporting)}><Download size={16} /> {exporting === "md" ? "Préparation…" : "Télécharger l'export"}</button>
          <button className="btn ghost" onClick={() => download(true)} disabled={Boolean(exporting)}><Download size={16} /> {exporting === "files" ? "Préparation…" : "Avec les fichiers d'origine"}</button>
        </div>
        <p className="hint">La version avec les fichiers (PDF, images, audio) peut être lourde. Garde-en une copie de temps en temps : c'est ta sauvegarde hors ligne.</p>
      </section>

      <section className="section">
        <h2>Cet appareil</h2>
        <p className="muted">Connecté à {origin}.</p>
        <button className="btn danger" onClick={onLogout}><LogOut size={16} /> Se déconnecter</button>
      </section>
    </div>
  );
}
