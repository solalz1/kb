// Which look the app uses. "classic" keeps the original interface (styles.classic.css and the original shell);
// anything else is the redesigned, phone-first interface. Stored on the device; changing it reloads the app.

const UI_KEY = "kb_ui";

export type UiVersion = "v2" | "classic";

export const ui = {
  get version(): UiVersion {
    try { return localStorage.getItem(UI_KEY) === "classic" ? "classic" : "v2"; } catch { return "v2"; }
  },
  set version(v: UiVersion) {
    try { v === "classic" ? localStorage.setItem(UI_KEY, "classic") : localStorage.removeItem(UI_KEY); } catch { /* storage unavailable */ }
  },
};

/** Fired when the user taps the tab of the page they are already on: scroll to the top and focus the search. */
export const HOME_EVENT = "kb:home";
