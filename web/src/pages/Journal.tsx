import { CalendarDays, ChevronLeft, ChevronRight, Loader2, Pencil, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type JournalEntry } from "../api";
import { lang, locale, t } from "../i18n";

/** "2026-10-07" for a local date (never toISOString: that would be the UTC day). */
const keyOf = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parseKey = (key: string) => {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d);
};
/** A real calendar day ("2026-02-30" is not one: Date would roll it over to March). */
const isDayKey = (s: string | undefined): s is string => Boolean(s && /^\d{4}-\d{2}-\d{2}$/.test(s) && keyOf(parseKey(s)) === s);

const capitalize = (s: string) => (lang === "fr" ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const monthLabel = (d: Date) => capitalize(d.toLocaleDateString(locale, { month: "long", year: "numeric" }));
const dayLabel = (d: Date) => capitalize(d.toLocaleDateString(locale, { weekday: "long", day: "numeric", month: "long", year: "numeric" }));
const timeLabel = (iso: string) => new Date(iso).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });

/** Monday-first weekday initials in the interface language. */
const WEEKDAYS = Array.from({ length: 7 }, (_, i) =>
  new Date(2026, 0, 5 + i).toLocaleDateString(locale, { weekday: "short" }).replace(".", ""));

/** The weeks of a month, Monday first; days outside the month are null. */
function weeksOf(month: Date): (Date | null)[][] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const cells: (Date | null)[] = Array((first.getDay() + 6) % 7).fill(null);
  for (let d = 1; d <= days; d++) cells.push(new Date(month.getFullYear(), month.getMonth(), d));
  while (cells.length % 7) cells.push(null);
  return Array.from({ length: cells.length / 7 }, (_, i) => cells.slice(i * 7, i * 7 + 7));
}

export default function Journal() {
  const params = useParams();
  const navigate = useNavigate();
  // computed once: a tab left open past midnight keeps showing the day it was on
  const [todayKey] = useState(() => keyOf(new Date()));
  const selected = isDayKey(params.day) ? params.day : todayKey;
  const [month, setMonth] = useState(() => { const d = parseKey(selected); return new Date(d.getFullYear(), d.getMonth(), 1); });
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [entries, setEntries] = useState<JournalEntry[] | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const monthKey = keyOf(month).slice(0, 7);

  // Only the latest request of each kind may update the page: clicking through days or months quickly must not let
  // a slow, older answer replace the one for the day on screen.
  const monthReq = useRef(0);
  const dayReq = useRef(0);
  const loadMonth = useCallback(() => {
    const n = ++monthReq.current;
    api.journalMonth(monthKey)
      .then((m) => { if (n === monthReq.current) setCounts(m.days); })
      .catch((e) => { if (n === monthReq.current) setError(e.message); });
  }, [monthKey]);
  const loadDay = useCallback(() => {
    const n = ++dayReq.current;
    api.journalDay(selected)
      .then((d) => { if (n === dayReq.current) setEntries(d.entries); })
      .catch((e) => { if (n === dayReq.current) setError(e.message); });
  }, [selected]);

  // after a change, reload what is on screen now (the user may have moved to another day meanwhile)
  const latest = useRef({ loadDay, loadMonth });
  latest.current = { loadDay, loadMonth };

  useEffect(() => { loadMonth(); }, [loadMonth]);
  useEffect(() => { setEntries(null); setEditing(null); setError(""); loadDay(); }, [loadDay]);
  // following a link to another month's day shows that month
  useEffect(() => {
    const d = parseKey(selected);
    if (d.getFullYear() !== month.getFullYear() || d.getMonth() !== month.getMonth()) {
      setMonth(new Date(d.getFullYear(), d.getMonth(), 1));
    }
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  const weeks = useMemo(() => weeksOf(month), [month]);
  const pick = (key: string) => navigate(key === todayKey ? "/journal" : `/journal/${key}`);
  const shiftMonth = (delta: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + delta, 1));

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      latest.current.loadDay();
      latest.current.loadMonth();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    run(async () => {
      await api.createNote({ content: text, space: "perso", category: "journal", entry_date: selected });
      setDraft("");
    });
  };

  const save = () => {
    if (!editing || !editing.text.trim() || busy) return;
    const { id, text } = editing;
    run(async () => { await api.patch(id, { content: text.trim() }); setEditing(null); });
  };

  const remove = (entry: JournalEntry) => {
    if (busy || !window.confirm(t("Supprimer cette note du journal ?"))) return;
    run(() => api.remove(entry.id));
  };

  const day = parseKey(selected);
  const isFuture = selected > todayKey;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);

  return (
    <div className="page wide journal">
      <header className="space-head">
        <div>
          <h1 className="title">{t("Journal")}</h1>
          <p className="muted">{t("Une page par jour : ce qui s'est passé, ce que tu as ressenti, ce que tu retiens. Tes notes restent dans ton espace Perso.")}</p>
        </div>
      </header>

      <div className="journal-layout">
        <section className="calendar" aria-label={t("Calendrier")}>
          <div className="cal-head">
            <button type="button" className="icon-btn" onClick={() => shiftMonth(-1)} aria-label={t("Mois précédent")}><ChevronLeft size={17} /></button>
            <h2 aria-live="polite">{monthLabel(month)}</h2>
            <button type="button" className="icon-btn" onClick={() => shiftMonth(1)} aria-label={t("Mois suivant")}><ChevronRight size={17} /></button>
          </div>
          <div className="cal-grid">
            <div className="cal-row" aria-hidden="true">
              {WEEKDAYS.map((w) => <span key={w} className="cal-wd">{w}</span>)}
            </div>
            {weeks.map((week, i) => (
              <div className="cal-row" key={i}>
                {week.map((d, j) => {
                  if (!d) return <span key={j} className="cal-day empty" />;
                  const key = keyOf(d);
                  const n = counts[key] ?? 0;
                  return (
                    <button key={j} type="button" className="cal-day" data-day={key}
                            aria-pressed={key === selected} aria-current={key === todayKey ? "date" : undefined}
                            aria-label={`${dayLabel(d)}${n ? ` · ${n > 1 ? t("{n} notes", { n }) : t("1 note")}` : ""}`}
                            onClick={() => pick(key)}>
                      <span className="num">{d.getDate()}</span>
                      {n > 0 && <span className="mark" aria-hidden="true">{n > 1 ? n : ""}</span>}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="cal-foot">
            {selected !== todayKey || keyOf(month).slice(0, 7) !== todayKey.slice(0, 7) ? (
              <button type="button" className="btn small" onClick={() => { setMonth(new Date(new Date().getFullYear(), new Date().getMonth(), 1)); pick(todayKey); }}>
                <CalendarDays size={15} /> {t("Aujourd'hui")}
              </button>
            ) : <span />}
            <span className="mono-label">{total === 0 ? t("Aucune note ce mois-ci") : total > 1 ? t("{n} notes ce mois-ci", { n: total }) : t("{n} note ce mois-ci", { n: total })}</span>
          </div>
        </section>

        <section className="day" aria-labelledby="day-title">
          <h2 id="day-title">{dayLabel(day)}</h2>
          {selected === todayKey && <p className="mono-label">{t("Aujourd'hui")}</p>}

          {entries === null ? (
            <div className="status-line"><Loader2 size={16} className="spin" /> {t("Chargement…")}</div>
          ) : (
            <ol className="entries">
              {entries.map((e) => (
                <li key={e.id} className="entry">
                  <div className="entry-head">
                    <span className="mono-label">{timeLabel(e.created_at)}</span>
                    {e.kind !== "note" && <Link to={`/item/${e.id}`}>{t("Voir la fiche")}</Link>}
                    <span className="entry-actions">
                      {e.kind === "note" && editing?.id !== e.id && (
                        <button type="button" className="icon-btn" aria-label={t("Modifier")} onClick={() => setEditing({ id: e.id, text: e.text ?? "" })}><Pencil size={15} /></button>
                      )}
                      <button type="button" className="icon-btn" aria-label={t("Supprimer")} disabled={busy} onClick={() => remove(e)}><Trash2 size={15} /></button>
                    </span>
                  </div>
                  {editing?.id === e.id ? (
                    <div className="entry-edit">
                      <label className="sr-only" htmlFor={`edit-${e.id}`}>{t("Modifier la note")}</label>
                      <textarea id={`edit-${e.id}`} className="field" value={editing.text} autoFocus
                                onChange={(ev) => setEditing({ id: e.id, text: ev.target.value })} />
                      <div className="editor-actions">
                        <button type="button" className="btn small primary" onClick={save} disabled={busy || !editing.text.trim()}>{t("Enregistrer")}</button>
                        <button type="button" className="btn small ghost" onClick={() => setEditing(null)}>{t("Annuler")}</button>
                      </div>
                    </div>
                  ) : (
                    <p className="entry-text">{e.kind === "note" ? e.text : e.title}</p>
                  )}
                  {e.kind === "note" && (
                    <Link className="entry-link" to={`/item/${e.id}`}>{t("Résumé, tags et liens")}</Link>
                  )}
                </li>
              ))}
            </ol>
          )}
          {entries !== null && entries.length === 0 && (
            <p className="muted entries-empty">{isFuture ? t("Rien pour ce jour. Tu peux déjà y noter ce que tu prévois.") : t("Rien d'écrit ce jour-là.")}</p>
          )}

          <form className="composer-journal" onSubmit={add}>
            <label className="lbl" htmlFor="journal-new">{entries?.length ? t("Ajouter une note à ce jour") : t("Écrire pour ce jour")}</label>
            <textarea id="journal-new" className="field" value={draft} onChange={(e) => setDraft(e.target.value)}
                      placeholder={t("Ce qui s'est passé, ce que tu as ressenti, ce que tu retiens…")}
                      onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) add(e); }} />
            <div className="editor-actions">
              <button className="btn primary" disabled={busy || !draft.trim()}>
                {busy && <Loader2 size={15} className="spin" />} {t("Ajouter au journal")}
              </button>
              <span className="hint kbd-hint">{t("⌘ + Entrée pour enregistrer")}</span>
            </div>
          </form>
          {error && <div className="error-box">{error}</div>}
        </section>
      </div>
    </div>
  );
}
