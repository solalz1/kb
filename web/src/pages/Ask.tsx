import { ArrowUp, Check, ExternalLink, Loader2, RotateCcw, SquareArrowOutUpRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, streamChat, type ChatMode, type ModelOption, type SourceCard } from "../api";
import { claudePrompt, openInClaude } from "../claude";
import { renderAnswer } from "../markdown";
import { headLabel } from "../perso";

type Mode = ChatMode;
interface Turn {
  role: "user" | "assistant";
  mode: Mode;
  content: string;
  sources?: SourceCard[];
  status?: string;
  plan?: string[];
  error?: string;
  done?: boolean;
  model?: string;
}

const MODEL_KEY = "kb_chat_model";
const readModel = () => { try { return localStorage.getItem(MODEL_KEY) || ""; } catch { return ""; } };
const saveModel = (id: string) => { try { localStorage.setItem(MODEL_KEY, id); } catch { /* stockage indisponible */ } };

// la conversation survit à la navigation (ouvrir une source puis revenir)
let saved: { turns: Turn[]; mode: Mode } = { turns: [], mode: "ask" };

const SUGGESTIONS: Record<Mode, string[]> = {
  ask: [
    "Qu'est-ce que j'ai sauvegardé sur les agents IA ?",
    "Quels outils ai-je mis de côté récemment ?",
    "Résume ce que ma KB dit de l'évaluation des LLM",
  ],
  project: [
    "Je lance un side-project : un agent qui trie et résume mes emails",
    "Je prépare un article sur le RAG en production",
    "Je veux construire une app mobile de suivi d'entraînement",
  ],
  advice: [
    "On me propose un poste mieux payé mais qui me laisserait moins de temps pour mes proches. Qu'est-ce que je fais ?",
    "Un ami me demande de lui prêter une grosse somme. Comment je réagis ?",
    "Je repousse mon projet perso depuis des semaines. Comment m'y remettre ?",
  ],
};

const MODES: { id: Mode; label: string; tag: string; title: string; intro: string; placeholder: string; label2: string }[] = [
  { id: "ask", label: "Question", tag: "", title: "Demande à ta KB", label2: "Ta question",
    intro: "Les réponses s'appuient sur ce que tu as sauvegardé, avec un renvoi vers chaque source.",
    placeholder: "Pose une question à ta KB…" },
  { id: "project", label: "Nouveau projet", tag: "Nouveau projet", title: "Qu'est-ce qui peut servir à ton projet ?",
    label2: "Ton projet",
    intro: "Décris le projet en quelques phrases. Je cherche sous plusieurs angles dans ta KB et je te rends un dossier : ce qui sert, qui suivre, et ce qui manque.",
    placeholder: "Décris ton nouveau projet…" },
  { id: "advice", label: "Conseil", tag: "Conseil", title: "Un conseil fidèle à tes principes", label2: "Ta situation",
    intro: "Décris la situation ou la décision. Je relis tes principes et tes valeurs, puis tes leçons et tes notes perso, et je te réponds à partir de ce qui compte pour toi, en citant chaque note.",
    placeholder: "Décris la situation ou la décision…" },
];

export default function Ask() {
  const [params] = useSearchParams();
  const asked = params.get("mode");
  const [mode, setMode] = useState<Mode>(asked === "advice" || asked === "project" || asked === "ask" ? asked : saved.mode);
  const current = MODES.find((m) => m.id === mode)!;
  const [turns, setTurns] = useState<Turn[]>(saved.turns);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState(readModel());

  useEffect(() => {
    api.models().then((list) => {
      setModels(list);
      // le choix mémorisé n'est gardé que s'il fait encore partie des modèles proposés
      setModel((current) => (list.some((m) => m.id === current) ? current : list.find((m) => m.default)?.id ?? ""));
    }).catch(() => {});
  }, []);
  const modelInfo = models.find((m) => m.id === model);
  const pickModel = (id: string) => { setModel(id); saveModel(id); };
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { saved = { turns, mode }; }, [turns, mode]);

  useEffect(() => {
    const about = params.get("about");
    if (about) api.item(about).then((it) => setInput(`À propos de « ${it.title} » : `)).catch(() => {});
  }, [params]);

  useEffect(() => {
    const el = box.current;
    if (el) { el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 180)}px`; }
  }, [input]);

  const patchLast = (fn: (t: Turn) => Turn) =>
    setTurns((prev) => [...prev.slice(0, -1), fn(prev[prev.length - 1])]);

  const send = async (text = input) => {
    const content = text.trim();
    if (!content || busy) return;
    setInput("");
    setBusy(true);
    const history = [...turns, { role: "user" as const, mode, content }];
    setTurns([...history, { role: "assistant", mode, content: "",
                            status: mode === "advice" ? "Je relis tes principes…" : "Je cherche dans ta KB…", model: modelInfo?.label }]);
    requestAnimationFrame(() => bottom.current?.scrollIntoView({ behavior: "smooth" }));

    const messages = mode === "project"
      ? [{ role: "user" as const, content }]
      : history.filter((t) => t.mode === mode && t.content && !t.error).map((t) => ({ role: t.role, content: t.content }));

    abort.current = new AbortController();
    try {
      await streamChat({ messages, mode, model: model || undefined }, (e) => {
        if (e.type === "status") patchLast((t) => ({ ...t, status: e.text }));
        if (e.type === "plan") patchLast((t) => ({ ...t, plan: e.queries, status: "Je croise les résultats…" }));
        if (e.type === "sources") patchLast((t) => ({ ...t, sources: e.sources, status: e.sources.length ? "Je rédige…" : "Je rédige (rien de pertinent trouvé)…" }));
        if (e.type === "delta") patchLast((t) => ({ ...t, content: t.content + e.text, status: undefined }));
        if (e.type === "error") patchLast((t) => ({ ...t, error: e.text, status: undefined }));
        if (e.type === "done") patchLast((t) => ({ ...t, done: true, status: undefined }));
      }, abort.current.signal);
    } catch (err) {
      if ((err as Error).name !== "AbortError") patchLast((t) => ({ ...t, error: (err as Error).message, status: undefined }));
    } finally {
      setBusy(false);
    }
  };

  const onAnswerClick = (e: React.MouseEvent | React.KeyboardEvent, turnIdx: number) => {
    const el = (e.target as HTMLElement).closest(".cite") as HTMLElement | null;
    if (!el) return;
    const key = `${turnIdx}-${el.dataset.n}`;
    setFlash(key);
    document.getElementById(`src-${key}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => setFlash(null), 1600);
  };

  const reset = () => { abort.current?.abort(); setTurns([]); setBusy(false); };

  // La question en cours, sinon la dernière posée dans ce fil
  const lastQuestion = [...turns].reverse().find((t) => t.role === "user");
  const claudeText = input.trim() || lastQuestion?.content || "";
  const claudeMode: Mode = input.trim() ? mode : (lastQuestion?.mode ?? mode);
  const [notice, setNotice] = useState("");
  const askClaude = async () => {
    if (!claudeText) return;
    const copied = await openInClaude(claudePrompt(claudeText, claudeMode));
    setNotice(copied ? "Question aussi copiée, au cas où" : "Ouverture de Claude…");
    setTimeout(() => setNotice(""), 4000);
  };

  return (
    <div className="page ask-wrap">
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div className="modes" role="group" aria-label="Mode">
          {MODES.map((m) => <button key={m.id} aria-pressed={mode === m.id} onClick={() => setMode(m.id)}>{m.label}</button>)}
        </div>
        {models.length > 1 && (
          <label className="model-pick">
            <span>Modèle</span>
            <select id="chat-model" value={model} onChange={(e) => pickModel(e.target.value)}>
              {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
        )}
        {turns.length > 0 && <button className="btn small ghost" onClick={reset} aria-label="Nouvelle conversation"><RotateCcw size={14} /><span className="hide-sm">Nouvelle conversation</span></button>}
      </div>
      {modelInfo?.note && <div className="hint" style={{ margin: "8px 4px 0" }}>{modelInfo.label} : {modelInfo.note}</div>}

      <div className="thread">
        {turns.length === 0 && (
          <>
            <h1 className="title" style={{ marginTop: 22 }}>{current.title}</h1>
            <p className="muted" style={{ maxWidth: "58ch" }}>{current.intro}</p>
            {mode === "advice" && (
              <p className="hint" style={{ maxWidth: "58ch" }}>
                Tes principes et tes valeurs viennent de l'espace <Link to="/perso">Perso</Link>. Pour une situation de crise ou de santé,
                parle aussi à un professionnel ou à une personne de confiance.
              </p>
            )}
            <div className="suggestions">
              {SUGGESTIONS[mode].map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}
            </div>
          </>
        )}

        {turns.map((t, i) =>
          t.role === "user" ? (
            <div key={i} className="msg-user">
              {t.mode !== "ask" && <span className="mode-tag">{MODES.find((m) => m.id === t.mode)?.tag}</span>}
              {t.content}
            </div>
          ) : (
            <div key={i}>
              {t.plan && t.plan.length > 0 && (
                <div className="plan">Pistes explorées : {t.plan.map((q) => <span key={q}>{q}</span>)}</div>
              )}
              {t.status && <div className="status-line"><Loader2 size={15} className="spin" /> {t.status}</div>}
              {t.content && (
                <div className="answer" role="article"
                     onClick={(e) => onAnswerClick(e, i)} onKeyDown={(e) => e.key === "Enter" && onAnswerClick(e, i)}
                     dangerouslySetInnerHTML={{ __html: renderAnswer(t.content) }} />
              )}
              {t.error && <div className="error-box">{t.error}</div>}
              {t.done && t.model && <div className="answered-by">Réponse de {t.model}</div>}
              {t.done && t.sources && t.sources.length > 0 && (() => {
                const cited = new Set(Array.from(t.content.matchAll(/\[(\d{1,2})\]/g), (m) => Number(m[1])));
                const used = t.sources.filter((s) => cited.has(s.n));
                const rest = t.sources.filter((s) => !cited.has(s.n));
                const card = (s: SourceCard) => (
                  <div key={s.n} id={`src-${i}-${s.n}`} className={`source${flash === `${i}-${s.n}` ? " flash" : ""}`} data-kind={s.kind ?? undefined}
                       data-space={s.space === "perso" ? "perso" : undefined}>
                    <span className="n">{s.n}</span>
                    <Link to={`/item/${s.id}`} className="st" style={{ color: "inherit", textDecoration: "none" }}>{s.title || "Sans titre"}</Link>
                    <span className="sm">
                      <span>{headLabel(s)}{s.author ? `, ${s.author}` : ""}</span>
                      {s.source_url && <a href={s.source_url} target="_blank" rel="noreferrer"><ExternalLink size={12} /> source</a>}
                    </span>
                  </div>
                );
                return (
                  <>
                    {used.length > 0 && <div className="sources" aria-label="Sources citées">{used.map(card)}</div>}
                    {rest.length > 0 && (
                      <details className="more-sources" open={used.length === 0}>
                        <summary>{used.length ? `Autres éléments consultés (${rest.length})` : `Éléments consultés (${rest.length})`}</summary>
                        <div className="sources">{rest.map(card)}</div>
                      </details>
                    )}
                  </>
                );
              })()}
            </div>
          ),
        )}
        <div ref={bottom} />
      </div>

      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <label className="sr-only" htmlFor="ask-input">{current.label2}</label>
        <textarea id="ask-input" ref={box} rows={1} value={input} onChange={(e) => setInput(e.target.value)}
                  placeholder={current.placeholder}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
        <button className="send" type="submit" disabled={busy || !input.trim()} aria-label="Envoyer">
          {busy ? <Loader2 size={18} className="spin" /> : <ArrowUp size={19} />}
        </button>
      </form>
      <div className="in-claude">
        <button type="button" className="linkish" onClick={askClaude} disabled={!claudeText}>
          <SquareArrowOutUpRight size={14} /> Demander dans Claude
        </button>
        <span className="hint">Réponse via ton abonnement et le connecteur KB, sans coût API</span>
      </div>
      {notice && <div className="toast" role="status"><Check size={16} /> {notice}</div>}
    </div>
  );
}
