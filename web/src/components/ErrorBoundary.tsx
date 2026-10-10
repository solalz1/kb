import { Component, type ErrorInfo, type ReactNode } from "react";
import { cache } from "../cache";
import { t } from "../i18n";

/** A page that fails to render shows what happened and a way out, instead of leaving the whole app blank.
 *  `resetKey` (the URL) clears the error when the user goes somewhere else. What the app had cached is forgotten:
 *  the page may have crashed on it, and the next try must load it afresh. */
export class ErrorBoundary extends Component<{ resetKey?: string; children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Page crash:", error, info.componentStack);
    cache.clear();
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="page crash" role="alert">
        <h1 className="title">{t("Cette page n'a pas pu s'afficher")}</h1>
        <p>{t("Le reste de l'app marche toujours. Reviens en arrière, ou recharge la page.")}</p>
        <p className="code">{error.message || String(error)}</p>
        <div className="btn-row">
          <button type="button" className="btn primary" onClick={() => window.history.length > 1 ? window.history.back() : window.location.assign("/")}>
            {t("Revenir en arrière")}</button>
          <button type="button" className="btn ghost" onClick={() => window.location.reload()}>{t("Recharger")}</button>
        </div>
      </div>
    );
  }
}
