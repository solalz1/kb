import { Check, ExternalLink, Loader2, Pencil } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type CostService, type Costs as CostsT } from "../api";
import { lang, locale, t } from "../i18n";
import { ago, fullDate } from "../kinds";

/** US dollars, as the services bill them ("$US" in French, like the Claude Console); tiny amounts don't read as zero. */
export const dollars = (n: number) =>
  n > 0 && n < 0.01 ? (lang === "fr" ? "< 0,01 $US" : "< $0.01")
    : n.toLocaleString(locale, { style: "currency", currency: "USD" });

const compact = (n: number) => n.toLocaleString(locale, { notation: "compact", maximumFractionDigits: 1 });

/** Settings → Costs: what the KB spent this month and in all, service by service, and what is left. */
export function Costs() {
  const [data, setData] = useState<CostsT | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState("");

  useEffect(() => { api.costs().then(setData).catch((e) => setError(e.message)); }, []);

  if (error) return <div className="error-box">{error}</div>;
  if (!data) return <div className="status-line"><Loader2 size={16} className="spin" /> {t("Chargement…")}</div>;

  return (
    <>
      <div className="cost-totals">
        <div><span className="mono-label">{t("Ce mois-ci")}</span><b>{dollars(data.month)}</b></div>
        <div><span className="mono-label">{t("Depuis le début")}</span><b>{dollars(data.total)}</b></div>
      </div>
      <ul className="cost-list">
        {data.services.map((s) => (
          <CostRow key={s.id} s={s} open={open === s.id} onToggle={() => setOpen(open === s.id ? "" : s.id)}
                   onSaved={(next) => { setData(next); setOpen(""); }} />
        ))}
      </ul>
      <p className="hint">
        {t("Synchronisé : le chiffre vient du service lui-même. ")}
        {data.measured_since
          ? t("Estimé : la KB compte chacun de ses appels payants au prix public, depuis le {date}. ",
              { date: fullDate(data.measured_since.slice(0, 10)) })
          : t("Estimé : la KB comptera chacun de ses appels payants au prix public, dès le premier. ")}
        {t("Ce que ces comptes dépensent hors de la KB n'y est pas, et Voyage, Groq et Railway n'ont pas d'API de facturation. ")}
        {!data.anthropic_admin && t("Pour le chiffre exact de la Console Claude, ajoute une clé Admin (ANTHROPIC_ADMIN_KEY, voir SETUP.md). ")}
        {t("Montants en dollars US, comme les services les facturent.")}
      </p>
    </>
  );
}

/** One line under the name: why this figure is what it is. */
function note(s: CostService): string {
  if (s.id === "anthropic") {
    return s.synced
      ? t("Chiffre de la Console : toute ton organisation. Dont la KB : {amount} ce mois-ci.", { amount: dollars(s.kb_month) })
      : t("Seulement ce que la KB consomme. La Console compte aussi tes autres usages de l'API.");
  }
  if (s.id === "railway") return t("Le minimum de l'offre Hobby, usage compris. Un dépassement n'est pas compté ici.");
  return "";
}

function CostRow({ s, open, onToggle, onSaved }: {
  s: CostService; open: boolean; onToggle: () => void; onSaved: (c: CostsT) => void;
}) {
  const [balance, setBalance] = useState("");
  const [before, setBefore] = useState(s.before != null ? String(s.before) : "");
  const [monthly, setMonthly] = useState(s.monthly != null ? String(s.monthly) : "");
  const [limit, setLimit] = useState(s.limit != null ? String(s.limit) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const num = (v: string) => (v.trim() === "" ? undefined : Number(v.replace(",", ".")));
  const metered = s.kind === "metered";
  const why = note(s);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    // an emptied field clears what was set
    const body = {
      balance: num(balance),
      before: s.synced ? undefined : num(before) ?? (s.before != null ? 0 : undefined),
      monthly: s.kind === "plan" ? num(monthly) : undefined,
      limit: metered ? num(limit) ?? (s.limit != null ? 0 : undefined) : undefined,
    };
    if (Object.values(body).some((v) => v !== undefined && (isNaN(v) || v < 0))) { setError(t("Un montant positif, en dollars.")); return; }
    setBusy(true);
    setError("");
    try { onSaved(await api.setCosts(s.id, body)); setBalance(""); } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };

  return (
    <li className="cost-row" data-service={s.id}>
      <div className="cost-line">
        <div className="cost-main">
          <span className="cost-name">
            {s.name}
            {metered && <span className={`cost-tag${s.synced ? " ok" : ""}`}>{s.synced ? t("synchronisé") : t("estimé")}</span>}
          </span>
          <span className="cost-sub">
            {s.kind === "plan"
              ? (s.monthly ? t("forfait {amount} / mois", { amount: dollars(s.monthly) }) : t("gratuit"))
              : s.free_tokens
                ? t("{used} tokens sur {free} offerts", { used: compact(s.tokens ?? 0), free: compact(s.free_tokens) })
                : t("à l'usage")}
          </span>
          {why && <span className="cost-note">{why}</span>}
          {s.sync_error && <span className="cost-note warn">{t("Synchronisation impossible ({error}).", { error: s.sync_error })}</span>}
          {s.limit != null && s.left_this_month != null && (
            <span className={`cost-left${s.left_this_month < s.limit * 0.1 ? " low" : ""}`}>
              {s.synced
                ? t("Reste ce mois {amount} sur {limit} de limite", { amount: dollars(Math.max(0, s.left_this_month)), limit: dollars(s.limit) })
                : t("Reste ce mois ≈ {amount} sur {limit} de limite", { amount: dollars(Math.max(0, s.left_this_month)), limit: dollars(s.limit) })}
            </span>
          )}
          {s.remaining != null && (
            <span className={`cost-left${s.remaining < 2 ? " low" : ""}`}>
              {s.remaining_synced
                ? <>{t("Reste {amount}", { amount: dollars(s.remaining) })}<span className="muted"> · {t("solde du compte, synchronisé")}</span></>
                : <>{t("Reste ≈ {amount}", { amount: dollars(s.remaining) })}
                    <span className="muted"> · {t("sur {balance} notés {when}", { balance: dollars(s.balance ?? 0), when: ago(s.balance_at ?? "") })}</span></>}
            </span>
          )}
        </div>
        <div className="cost-nums">
          <b>{dollars(s.month)}</b>
          <span className="muted">{t("{amount} en tout", { amount: dollars(s.total) })}</span>
        </div>
        <button type="button" className="icon-btn" aria-expanded={open} aria-label={t("Modifier {name}", { name: s.name })}
                onClick={onToggle}><Pencil size={15} /></button>
      </div>
      {open && (
        <form className="cost-edit" onSubmit={save}>
          {s.prepaid && !s.remaining_synced && (
            <label>{t("Solde affiché sur ton compte")}
              <input className="field" inputMode="decimal" placeholder={s.balance != null ? String(s.balance) : t("ex. 18,40")}
                     value={balance} onChange={(e) => setBalance(e.target.value)} />
            </label>
          )}
          {metered && (
            <label>{t("Limite de dépenses mensuelle")}
              <input className="field" inputMode="decimal" placeholder={t("aucune")} value={limit} onChange={(e) => setLimit(e.target.value)} />
            </label>
          )}
          {s.kind === "plan" && (
            <label>{t("Forfait mensuel")}
              <input className="field" inputMode="decimal" value={monthly} onChange={(e) => setMonthly(e.target.value)} />
            </label>
          )}
          {!s.synced && (
            <label>{t("Déjà dépensé avant le suivi")}
              <input className="field" inputMode="decimal" placeholder="0" value={before} onChange={(e) => setBefore(e.target.value)} />
            </label>
          )}
          {error && <div className="error-box">{error}</div>}
          <div className="cost-edit-actions">
            <button className="btn small primary" disabled={busy}>{busy ? <Loader2 size={14} className="spin" /> : <Check size={14} />} {t("Enregistrer")}</button>
            <a className="btn small ghost" href={s.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> {t("Voir le compte")}</a>
          </div>
        </form>
      )}
    </li>
  );
}
