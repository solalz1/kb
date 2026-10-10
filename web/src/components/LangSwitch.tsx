import { lang, setLang, t } from "../i18n";

/** Français / English, as a small segmented control ("FR / EN" or the full names). */
export function LangSwitch({ size = "xs" }: { size?: "xs" | "sm" }) {
  const names = size === "xs" ? { fr: "FR", en: "EN" } : { fr: "Français", en: "English" };
  return (
    <div className={`seg ${size}`} role="group" aria-label={t("Langue")}>
      {(["fr", "en"] as const).map((l) => (
        <button key={l} type="button" lang={l} aria-pressed={lang === l} onClick={() => lang !== l && setLang(l)}>{names[l]}</button>
      ))}
    </div>
  );
}
