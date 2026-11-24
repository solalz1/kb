import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type ItemPatch, type JournalEntry } from "../api";
import { useQuery } from "../cache";
import { Lines } from "../components/Skeleton";
import { agoLabel, capitalize, dayLabel, isDayKey, journalHref, keyOf, parseKey } from "../days";
import { locale, t } from "../i18n";
import { IconBack, IconEdit, IconNext, IconPrev, IconSpinner } from "../icons";
import { useDesktop } from "../layout";
import { reducedMotion } from "../motion";
import { Link, useNavigate } from "../nav";
import { forgetItem, keys } from "../queries";

const monthLabel = (d: Date) => capitalize(d.toLocaleDateString(locale, { month: "long", year: "numeric" }));
const timeLabel = (iso: string) => new Date(iso).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });

/** Monday-first weekday initials in the interface language. */
const WEEKDAYS = Array.from({ length: 7 }, (_, i) => new Date(2026, 0, 5 + i).toLocaleDateString(locale, { weekday: "narrow" }));

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
  const desktop = useDesktop();
  // computed once: a tab left open past midnight keeps showing the day it was on
  const [todayKey] = useState(() => keyOf(new Date()));
  const selected = isDayKey(params.day) ? params.day : todayKey;
  const [month, setMonth] = useState(() => { const d = parseKey(selected); return new Date(d.getFullYear(), d.getMonth(), 1); });
  const [draft, setDraft] = useState("");
  // the entry open for changes: its text (a note's only) and its day, which moves it to another page of the journal
  const [editing, setEditing] = useState<{ id: string; text: string; day: string } | null>(null);
  // an entry just moved here from another day: it is shown, and lit up a moment
  const [landed, setLanded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [wholeMonth, setWholeMonth] = useState(false);
  const monthKey = keyOf(month).slice(0, 7);

  // Each day and month is cached on its own: the page shows the one on screen, whatever answers arrive late; after a
  // change, what is on screen reloads by itself.
  const monthQuery = useQuery(keys.journalMonth(monthKey), () => api.journalMonth(monthKey), { keep: true });
  const counts = monthQuery.data?.days ?? {};
  const dayQuery = useQuery(keys.journalDay(selected), () => api.journalDay(selected));
  const entries = dayQuery.data?.entries ?? null;
  const echoes = dayQuery.data?.echoes ?? [];
  useEffect(() => { setEditing(null); setError(""); }, [selected]);
  // following a link to another month's day shows that month
  useEffect(() => {
    const d = parseKey(selected);
    if (d.getFullYear() !== month.getFullYear() || d.getMonth() !== month.getMonth()) {
      setMonth(new Date(d.getFullYear(), d.getMonth(), 1));
    }
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  const weeks = useMemo(() => weeksOf(month), [month]);
  // on a phone, two weeks around the chosen day unless the whole month is asked for
  const shownWeeks = useMemo(() => {
    if (desktop || wholeMonth) return weeks;
    const at = weeks.findIndex((w) => w.some((d) => d && keyOf(d) === selected));
    if (at < 0) return weeks;
    return weeks.slice(Math.max(0, at - 1), Math.max(0, at - 1) + 2);
  }, [weeks, desktop, wholeMonth, selected]);
  // another day is the same page, not a step deeper: it replaces this one in the history, so that going back (the
  // swipe from the edge, the back button) leaves the journal instead of walking back through the days looked at
  const pick = (key: string) => navigate(journalHref(key, todayKey), { viewTransition: false, replace: true });
  const shiftMonth = (delta: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + delta, 1));
  const goToday = () => { setMonth(new Date(new Date().getFullYear(), new Date().getMonth(), 1)); pick(todayKey); };

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      await Promise.all([dayQuery.refresh(), monthQuery.refresh()]);
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

  const open = (entry: JournalEntry) => { setError(""); setEditing({ id: entry.id, text: entry.text ?? "", day: selected }); };

  const save = (e?: React.FormEvent) => {
    e?.preventDefault();
    const entry = entries?.find((x) => x.id === editing?.id);
    if (!editing || !entry || busy) return;
    const note = entry.kind === "note";
    const text = editing.text.trim();
    if (note && !text) return;
    const body: Partial<ItemPatch> = {};
    if (note && text !== (entry.text ?? "").trim()) body.content = text;
    const moved = editing.day !== selected;
    if (moved) body.entry_date = editing.day;
    if (!Object.keys(body).length) { setEditing(null); return; }
    run(async () => {
      await api.patch(entry.id, body);
      setEditing(null);
      // the note goes with its new day: that page opens, the note lit up among the others
      if (moved) { setLanded(entry.id); pick(editing.day); }
    });
  };

  const remove = (entry: JournalEntry) => {
    if (busy || !window.confirm(t("Supprimer cette note du journal ?"))) return;
    run(() => api.remove(entry.id).then(() => { forgetItem(entry.id); setEditing(null); }));
  };

  // a note moved here: brought into view once its day has loaded
  useEffect(() => {
    if (!landed || !entries?.some((x) => x.id === landed)) return;
    document.querySelector(`[data-entry="${landed}"]`)?.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
  }, [landed, entries]);

  const day = parseKey(selected);
  const isFuture = selected > todayKey;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const elsewhere = selected !== todayKey || monthKey !== todayKey.slice(0, 7);

  return (
    <div className="page tight">
      <Link className="back phone-only" to="/perso"><IconBack size={20} /> {t("Perso")}</Link>
      <div className="title-block">
        <h1 className="title">{t("Journal")}</h1>
        <p className="lede desk-only">{t("Une page par jour : ce qui s'est passé, ce que tu as ressenti, ce que tu retiens. Tes notes restent dans ton espace Perso.")}</p>
      </div>

      <div className="journal-layout">
        <section className="calendar" aria-label={t("Calendrier")}>
          <div className="cal-head">
            <button type="button" className="icon-btn" onClick={() => shiftMonth(-1)} aria-label={t("Mois précédent")}><IconPrev size={18} /></button>
            <h2 aria-live="polite">{monthLabel(month)}</h2>
            <button type="button" className="icon-btn" onClick={() => shiftMonth(1)} aria-label={t("Mois suivant")}><IconNext size={18} /></button>
          </div>
          <div className="cal-grid">
            <div className="cal-row" aria-hidden="true">
              {WEEKDAYS.map((w, i) => <span key={i} className="cal-wd">{w}</span>)}
            </div>
            {shownWeeks.map((week, i) => (
              <div className="cal-row" key={i}>
                {week.map((d, j) => {
                  if (!d) return <span key={j} className="cal-day empty" />;
                  const key = keyOf(d);
                  const n = counts[key] ?? 0;
                  return (
                    <button key={j} type="button" className={`cal-day${key > todayKey ? " later" : ""}`} data-day={key}
                            aria-pressed={key === selected} aria-current={key === todayKey ? "date" : undefined}
                            aria-label={`${dayLabel(d)}${n ? ` · ${n > 1 ? t("{n} notes", { n }) : t("1 note")}` : ""}`}
                            onClick={() => pick(key)}>
                      {d.getDate()}
                      {n > 0 && <span className="mark" aria-hidden="true" />}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          <p className="cal-foot">
            <span>{total === 0 ? t("Aucune note ce mois-ci") : total > 1 ? t("{n} notes ce mois-ci", { n: total }) : t("{n} note ce mois-ci", { n: total })}</span>
            {!desktop && shownWeeks.length < weeks.length && <>· <button type="button" className="linkish" onClick={() => setWholeMonth(true)}>{t("voir tout le mois")}</button></>}
            {elsewhere && <>· <button type="button" className="linkish" onClick={goToday}>{t("Revenir à aujourd'hui")}</button></>}
          </p>
        </section>

        <section className="day" aria-labelledby="day-title">
          <div className="day-head">
            <h2 id="day-title">{dayLabel(day, desktop)}</h2>
            {selected === todayKey && <span className="today">{t("Aujourd'hui")}</span>}
          </div>

          {entries === null ? (
            dayQuery.error ? null : <Lines n={2} widths={["90%", "60%"]} />
          ) : entries.length > 0 && (
            <ol className="entries">
              {entries.map((e) => (
                <li key={e.id} data-entry={e.id} className={`entry${landed === e.id ? " landed" : ""}${editing?.id === e.id ? " editing" : ""}`}
                    onAnimationEnd={() => setLanded((was) => (was === e.id ? null : was))}>
                  {editing?.id === e.id ? (
                    <form className="entry-edit" onSubmit={save} onKeyDown={(ev) => { if (ev.key === "Escape") setEditing(null); }}>
                      <span className="entry-head"><span className="time">{timeLabel(e.created_at)}</span></span>
                      {e.kind === "note" ? <>
                        <label className="sr-only" htmlFor={`edit-${e.id}`}>{t("Modifier la note")}</label>
                        <textarea id={`edit-${e.id}`} className="field serif" rows={4} value={editing.text} autoFocus
                                  onChange={(ev) => setEditing({ ...editing, text: ev.target.value })}
                                  onKeyDown={(ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) save(ev); }} />
                      </> : <p className="entry-text">{e.title}</p>}
                      <div className="journal-day">
                        <label className="label" htmlFor={`day-${e.id}`}>{t("Jour")}</label>
                        <input id={`day-${e.id}`} className="field" type="date" value={editing.day} required
                               onChange={(ev) => { if (isDayKey(ev.target.value)) setEditing({ ...editing, day: ev.target.value }); }} />
                        <span className="hint">{editing.day === todayKey
                          ? t("Aujourd'hui · {day}", { day: dayLabel(parseKey(editing.day), false) }) : dayLabel(parseKey(editing.day))}</span>
                      </div>
                      <div className="btn-row">
                        <button type="submit" className="btn small primary" disabled={busy || (e.kind === "note" && !editing.text.trim())}>
                          {busy && <IconSpinner size={15} />} {t("Enregistrer")}
                        </button>
                        <button type="button" className="btn small ghost" onClick={() => setEditing(null)}>{t("Annuler")}</button>
                        <button type="button" className="linkish entry-delete" disabled={busy} onClick={() => remove(e)}>{t("Supprimer")}</button>
                      </div>
                    </form>
                  ) : <>
                    {/* the whole note is what you touch to change it: its words, its day, or to delete it */}
                    <button type="button" className="entry-open" onClick={() => open(e)} disabled={busy}>
                      <span className="entry-head">
                        <span className="time">{timeLabel(e.created_at)}</span>
                        <span className="sr-only">{t("Modifier")}</span>
                        <IconEdit size={15} className="entry-pen" />
                      </span>
                      <span className="entry-text">{e.kind === "note" ? e.text : e.title}</span>
                    </button>
                    {e.kind !== "note"
                      ? <Link className="entry-link" to={`/item/${e.id}`}>{t("Voir la fiche")}</Link>
                      : <Link className="entry-link desk-only" to={`/item/${e.id}`}>{t("Résumé, tags et liens")}</Link>}
                  </>}
                </li>
              ))}
            </ol>
          )}
          {entries !== null && entries.length === 0 && (
            <p className="hint">{isFuture ? t("Rien pour ce jour. Tu peux déjà y noter ce que tu prévois.") : t("Rien d'écrit ce jour-là.")}</p>
          )}

          <form className="composer-journal" onSubmit={add}>
            <label className="label sr-phone" htmlFor="journal-new">{entries?.length ? t("Ajouter une note à ce jour") : t("Écrire pour ce jour")}</label>
            <textarea id="journal-new" className="field serif" rows={desktop ? 4 : 2} value={draft} onChange={(e) => setDraft(e.target.value)}
                      style={{ fontSize: desktop ? 18 : 17, borderRadius: desktop ? 14 : 12 }}
                      placeholder={t("Ce qui s'est passé, ce que tu as ressenti, ce que tu retiens…")}
                      onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) add(e); }} />
            <div className="actions">
              <button className="btn primary" disabled={busy}>{busy && <IconSpinner size={15} />} {t("Ajouter au journal")}</button>
              <span className="hint kbd-hint desk-only">{t("⌘ + Entrée pour enregistrer")}</span>
            </div>
          </form>
          {(error || dayQuery.error || monthQuery.error) && <div className="error-box">{error || dayQuery.error || monthQuery.error}</div>}

          {echoes.length > 0 && (
            <section className="echoes" aria-labelledby="echoes-title">
              <h3 id="echoes-title">{t("Ce jour-là")}</h3>
              {echoes.map((echo) => (
                <Link key={echo.date} className="echo" to={journalHref(echo.date, todayKey)} viewTransition={false} replace>
                  <span className="meta"><b>{agoLabel(echo.months)}</b> · {dayLabel(parseKey(echo.date))}</span>
                  <span className="t">{echo.entries[0].kind === "note" ? echo.entries[0].text : echo.entries[0].title}</span>
                  {echo.entries.length > 1 && <span className="more">{echo.entries.length > 2
                    ? t("et {n} autres notes", { n: echo.entries.length - 1 }) : t("et 1 autre note")}</span>}
                </Link>
              ))}
            </section>
          )}
        </section>
      </div>
    </div>
  );
}
