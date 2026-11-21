import { useState } from "react";
import { api } from "../api";
import { useQuery } from "../cache";
import { agoLabel, dayLabel, journalHref, keyOf } from "../days";
import { t } from "../i18n";
import { IconCalendar, IconSpinner } from "../icons";
import { Link } from "../nav";
import { keys } from "../queries";

/** The top of Perso: today's page of the journal, how much is written on it, and a box to write in it at once. */
export function JournalToday() {
  // computed once: a tab left open past midnight keeps showing the day it was on
  const [today] = useState(() => keyOf(new Date()));
  const day = useQuery(keys.journalDay(today), () => api.journalDay(today));
  const entries = day.data?.entries ?? null;
  const [writing, setWriting] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const n = entries?.length ?? 0;
  const last = entries?.length ? entries[entries.length - 1] : null;
  // the furthest « Ce jour-là »: a year ago reads better than a month ago
  const echo = day.data?.echoes?.length ? day.data.echoes[day.data.echoes.length - 1] : null;

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.createNote({ content: text, space: "perso", category: "journal", entry_date: today });
      await day.refresh();
      setDraft("");
      setWriting(false);
      setSaved(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="journal-today" aria-labelledby="journal-today-title">
      <div className="jt-head">
        <h2 id="journal-today-title">{t("Journal")}</h2>
        <span className="jt-day">{dayLabel(new Date(), false)}</span>
        <Link className="jt-cal" to="/journal"><IconCalendar size={16} /> {t("Voir le calendrier")}</Link>
      </div>
      <p className="jt-count" aria-live="polite">
        {entries === null ? " "
          : n === 0 ? t("Rien d'écrit aujourd'hui.")
          : saved ? (n > 1 ? t("Ajouté. {n} notes aujourd'hui.", { n }) : t("Ajouté. 1 note aujourd'hui."))
          : n > 1 ? t("{n} notes aujourd'hui.", { n }) : t("1 note aujourd'hui.")}
      </p>
      {last && !writing && <p className="jt-last">{last.kind === "note" ? last.text : last.title}</p>}
      {writing ? (
        <form className="composer-journal" onSubmit={add}>
          <label className="sr-only" htmlFor="journal-today-new">{t("Écrire dans le journal d'aujourd'hui")}</label>
          <textarea id="journal-today-new" className="field serif" rows={4} value={draft} autoFocus
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder={t("Ce qui s'est passé, ce que tu as ressenti, ce que tu retiens…")}
                    onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) add(e); }} />
          <div className="actions">
            <button className="btn primary small" disabled={busy || !draft.trim()}>{busy && <IconSpinner size={15} />} {t("Ajouter au journal")}</button>
            <button type="button" className="btn ghost small" onClick={() => { setWriting(false); setError(""); }}>{t("Annuler")}</button>
          </div>
        </form>
      ) : (
        <button type="button" className="btn small primary jt-write" onClick={() => { setWriting(true); setSaved(false); }}>{t("Écrire")}</button>
      )}
      {error && <div className="error-box">{error}</div>}
      {echo && (
        <Link className="jt-echo" to={journalHref(echo.date, today)}>
          <span className="meta"><b>{t("Ce jour-là")}</b> · {agoLabel(echo.months).toLowerCase()}</span>
          <span className="t">{echo.entries[0].kind === "note" ? echo.entries[0].text : echo.entries[0].title}</span>
        </Link>
      )}
    </section>
  );
}
