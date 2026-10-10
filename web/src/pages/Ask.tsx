import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, streamChat, type ChatMode, type ModelOption, type SourceCard } from "../api";
import { claudePrompt, openInClaude } from "../claude";
import { localized, t, tServer } from "../i18n";
import { IconNext, IconReset, IconSend, IconSpinner } from "../icons";
import { useDesktop } from "../layout";
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
const saveModel = (id: string) => { try { localStorage.setItem(MODEL_KEY, id); } catch { /* storage unavailable */ } };

// the conversation survives navigation (open a source, come back)
let saved: { turns: Turn[]; mode: Mode } = { turns: [], mode: "ask" };

const SUGGESTIONS: Record<Mode, string[]> = {
  ask: [
    t("Qu'est-ce que j'ai sauvegardé sur les agents IA ?"),
    t("Quels outils ai-je mis de côté récemment ?"),
    t("Résume ce que ma KB dit de l'évaluation des LLM"),
  ],
  project: [
    t("Je lance un side-project : un agent qui trie et résume mes emails"),
    t("Je prépare un article sur le RAG en production"),
    t("Je veux construire une app mobile de suivi d'entraînement"),
  ],
  advice: [
    t("On me propose un poste mieux payé mais qui me laisserait moins de temps pour mes proches. Qu'est-ce que je fais ?"),
    t("Un ami me demande de lui prêter une grosse somme. Comment je réagis ?"),
    t("Je repousse mon projet perso depuis des semaines. Comment m'y remettre ?"),
  ],
};

const MODES: { id: Mode; label: string; short: string; tag: string; title: string; intro: string; placeholder: string; label2: string }[] = [
  { id: "ask", label: t("Question"), short: t("Question"), tag: t("Toi"), title: t("Demande à ta KB"), label2: t("Ta question"),
    intro: t("Les réponses s'appuient sur ce que tu as sauvegardé, avec un renvoi vers chaque source."),
    placeholder: t("Pose une question à ta KB…") },
  { id: "project", label: t("Nouveau projet"), short: t("Projet"), tag: t("Nouveau projet"), title: t("Qu'est-ce qui peut servir à ton projet ?"),
    label2: t("Ton projet"),
    intro: t("Décris le projet en quelques phrases. Je cherche sous plusieurs angles dans ta KB et je te rends un dossier : ce qui sert, qui suivre, et ce qui manque."),
    placeholder: t("Décris ton nouveau projet…") },
  { id: "advice", label: t("Conseil"), short: t("Conseil"), tag: t("Conseil"), title: t("Un conseil fidèle à tes principes"), label2: t("Ta situation"),
    intro: t("Décris la situation ou la décision. Je relis tes principes et tes valeurs, puis tes leçons et tes notes perso, et je te réponds à partir de ce qui compte pour toi, en citant chaque note."),
    placeholder: t("Décris la situation ou la décision…") },
];

export default function Ask() {
  const [params] = useSearchParams();
  const desktop = useDesktop();
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
      // the remembered choice stays only while it's still offered
      setModel((cur) => (list.some((m) => m.id === cur) ? cur : list.find((m) => m.default)?.id ?? ""));
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
    if (about) api.item(about).then((it) => setInput(t("À propos de « {title} » : ", { title: localized(it).title ?? "" }))).catch(() => {});
  }, [params]);

  useEffect(() => {
    const el = box.current;
    if (el) { el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 180)}px`; }
  }, [input]);

  const patchLast = (fn: (turn: Turn) => Turn) =>
    setTurns((prev) => [...prev.slice(0, -1), fn(prev[prev.length - 1])]);

  const send = async (text = input) => {
    const content = text.trim();
    if (!content || busy) return;
    setInput("");
    setBusy(true);
    const history = [...turns, { role: "user" as const, mode, content }];
    setTurns([...history, { role: "assistant", mode, content: "",
                            status: mode === "advice" ? t("Je relis tes principes…") : t("Je cherche dans ta KB…"), model: modelInfo?.label }]);
    requestAnimationFrame(() => bottom.current?.scrollIntoView({ behavior: "smooth" }));

    const messages = mode === "project"
      ? [{ role: "user" as const, content }]
      : history.filter((x) => x.mode === mode && x.content && !x.error).map((x) => ({ role: x.role, content: x.content }));

    abort.current = new AbortController();
    try {
      await streamChat({ messages, mode, model: model || undefined }, (e) => {
        if (e.type === "status") patchLast((x) => ({ ...x, status: tServer(e.text) }));
        if (e.type === "plan") patchLast((x) => ({ ...x, plan: e.queries, status: t("Je croise les résultats…") }));
        if (e.type === "sources") patchLast((x) => ({ ...x, sources: e.sources, status: e.sources.length ? t("Je rédige…") : t("Je rédige (rien de pertinent trouvé)…") }));
        if (e.type === "delta") patchLast((x) => ({ ...x, content: x.content + e.text, status: undefined }));
        if (e.type === "error") patchLast((x) => ({ ...x, error: e.text, status: undefined }));
        if (e.type === "done") patchLast((x) => ({ ...x, done: true, status: undefined }));
      }, abort.current.signal);
    } catch (err) {
      if ((err as Error).name !== "AbortError") patchLast((x) => ({ ...x, error: (err as Error).message, status: undefined }));
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

  // the question being typed, else the last one asked in this thread
  const lastQuestion = [...turns].reverse().find((x) => x.role === "user");
  const claudeText = input.trim() || lastQuestion?.content || "";
  const claudeMode: Mode = input.trim() ? mode : (lastQuestion?.mode ?? mode);
  const [notice, setNotice] = useState("");
  const askClaude = async () => {
    if (!claudeText) return;
    const copied = await openInClaude(claudePrompt(claudeText, claudeMode));
    setNotice(copied ? t("Question aussi copiée, au cas où") : t("Ouverture de Claude…"));
    setTimeout(() => setNotice(""), 4000);
  };

  const modelPicker = models.length > 1 && (
    <label className="model-pick">
      {t("Modèle")}
      <select id="chat-model" aria-label={t("Modèle")} value={model} onChange={(e) => pickModel(e.target.value)}>
        {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
      </select>
    </label>
  );

  return (
    <div className="page ask">
      <div className="ask-bar">
        <div className="seg" role="group" aria-label={t("Mode")}>
          {MODES.map((m) => <button key={m.id} type="button" aria-pressed={mode === m.id} onClick={() => setMode(m.id)}>{desktop ? m.label : m.short}</button>)}
        </div>
        {desktop && modelPicker}
        <span style={{ flex: 1 }} />
        {turns.length > 0 && (desktop
          ? <button className="btn small ghost" onClick={reset}><IconReset size={18} /> {t("Nouvelle conversation")}</button>
          : <button className="icon-link" style={{ border: 0, background: "none" }} onClick={reset} aria-label={t("Nouvelle conversation")}><IconReset size={20} /></button>)}
      </div>
      {!desktop && modelPicker}
      {modelInfo?.note && turns.length === 0 && <p className="hint">{t("{model} : {note}", { model: modelInfo.label, note: tServer(modelInfo.note) })}</p>}

      <div className="thread">
        {turns.length === 0 && (
          <div className="ask-intro">
            <h1>{current.title}</h1>
            <p className="lede">{current.intro}</p>
            {mode === "advice" && (
              <p className="hint">
                {t("Tes principes et tes valeurs viennent de l'espace")} <Link to="/perso">{t("Perso")}</Link>
                {t(". Pour une situation de crise ou de santé, parle aussi à un professionnel ou à une personne de confiance.")}
              </p>
            )}
            <div className="suggestions">
              {SUGGESTIONS[mode].map((s) => <button key={s} type="button" onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}

        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <div key={i} className="msg-user">
              {(turn.mode !== "ask" || desktop) && <span className="tag">{MODES.find((m) => m.id === turn.mode)?.tag}</span>}
              <p>{turn.content}</p>
            </div>
          ) : (
            <div key={i} className="turn">
              {turn.plan && turn.plan.length > 0 && <p className="plan">{t("Pistes explorées :")} {turn.plan.join(" · ")}</p>}
              {turn.status && <div className="status-line"><IconSpinner /> {turn.status}</div>}
              {turn.content && (
                <div className="answer" role="article"
                     onClick={(e) => onAnswerClick(e, i)} onKeyDown={(e) => e.key === "Enter" && onAnswerClick(e, i)}
                     dangerouslySetInnerHTML={{ __html: renderAnswer(turn.content) }} />
              )}
              {turn.error && <div className="error-box">{turn.error}</div>}
              {turn.done && turn.model && <p className="answered-by">{t("Réponse de {model}", { model: turn.model })}</p>}
              {turn.done && turn.sources && turn.sources.length > 0 && (() => {
                const cited = new Set(Array.from(turn.content.matchAll(/\[(\d{1,2})\]/g), (x) => Number(x[1])));
                const used = turn.sources.filter((s) => cited.has(s.n));
                const rest = turn.sources.filter((s) => !cited.has(s.n));
                const card = (s: SourceCard) => (
                  <div key={s.n} id={`src-${i}-${s.n}`} className={`source${flash === `${i}-${s.n}` ? " flash" : ""}`} data-kind={s.kind ?? undefined}
                       data-space={s.space === "perso" ? "perso" : undefined}>
                    <span className="n">{s.n}</span>
                    <span className="body">
                      <Link to={`/item/${s.id}`} className="st">{localized(s).title || t("Sans titre")}</Link>
                      <span className="sm">{headLabel(s)}{s.author ? ` · ${s.author}` : ""}
                        {s.source_url && <> · <a href={s.source_url} target="_blank" rel="noreferrer">{t("source ↗")}</a></>}</span>
                    </span>
                  </div>
                );
                return (
                  <>
                    {used.length > 0 && (
                      <div className="sources" aria-label={t("Sources citées")}>
                        <h2 className="mini-h">{t("Sources")}</h2>
                        {used.map(card)}
                      </div>
                    )}
                    {rest.length > 0 && (
                      <details className="more-sources" open={used.length === 0}>
                        <summary><IconNext size={14} /> {used.length ? t("Autres éléments consultés ({n})", { n: rest.length }) : t("Éléments consultés ({n})", { n: rest.length })}</summary>
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

      <div className="composer-wrap">
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <label className="sr-only" htmlFor="ask-input">{current.label2}</label>
          <textarea id="ask-input" ref={box} rows={1} value={input} onChange={(e) => setInput(e.target.value)}
                    placeholder={current.placeholder}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
          <button className="send" type="submit" disabled={busy} aria-label={t("Envoyer")}>
            {busy ? <IconSpinner size={18} /> : <IconSend size={20} />}
          </button>
        </form>
        {(desktop || turns.length === 0) && (
          <p className="in-claude">
            <button type="button" className="linkish" onClick={askClaude} disabled={!claudeText}>{t("Demander dans Claude")}</button>
            {" · "}{t("réponse via ton abonnement et le connecteur KB, sans coût API")}
          </p>
        )}
      </div>
      {notice && <div className="toast" role="status">{notice}</div>}
    </div>
  );
}
