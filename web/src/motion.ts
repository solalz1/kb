// How the app moves: which way a page slides in (view transitions, styles.css), the gestures of an installed app
// (components/Gestures.tsx), and cards that glide to their new place when a list changes.
import { useLayoutEffect, useRef, type RefObject } from "react";

/** Opened from the home screen (iPhone) or as an installed app: no browser bar, so no reload button or back gesture. */
export const standalone = () =>
  (navigator as Navigator & { standalone?: boolean }).standalone === true || window.matchMedia("(display-mode: standalone)").matches;
export const touchScreen = () => window.matchMedia("(pointer: coarse)").matches;
export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** A copy of the app to try a new version on (kb-dev.example.com, dev.kb.example.com): it says so, so that it is never
 *  mistaken for the real one. */
export const devCopy = () => /^[^.]+-dev\./.test(window.location.hostname) || /^dev\./.test(window.location.hostname);

let tick: HTMLLabelElement | null = null;
/** A light tick under the finger when a gesture is about to do something: on an iPhone (iOS 18+) through the system
 *  switch's haptic, elsewhere a short vibration where there is one. Nothing on a computer. */
export function haptic() {
  if (!touchScreen()) return;
  try {
    if (typeof navigator.vibrate === "function") { navigator.vibrate(8); return; }
    if (!tick) {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.setAttribute("switch", "");
      input.id = "haptic-tick";
      input.tabIndex = -1;
      tick = document.createElement("label");
      tick.htmlFor = input.id;
      tick.className = "haptic";
      tick.setAttribute("aria-hidden", "true");
      tick.append(input);
      document.body.append(tick);
    }
    tick.click();
  } catch { /* no haptics here */ }
}

/** What a finger presses lights up or sinks a little (CSS: .is-pressed), but only once the touch is clearly not the
 *  start of a scroll, as on an iPhone; a quick tap still shows it. A mouse uses :active (styles.css). */
const PRESSABLE = ".btn, .chip, .pill, .icon-link, .icon-btn, .seg button, .drop, .suggestions button, .cal-day, .composer .send, " +
  ".menu button, .item-tools button, .settings-nav a, .fiche, .folder-card, .resurface-card, .resurface-note, .related, .source, " +
  ".check-card, a.group-row, label.group-row, .back, .linkish, .tabbar a, .wiki-card, .compact-title, .compact-back";
export function pressFeedback() {
  let el: Element | null = null;
  let timer = 0;
  let x = 0;
  let y = 0;
  const clear = (delay = 0) => {
    window.clearTimeout(timer);
    const was = el;
    el = null;
    if (!was) return;
    if (delay) window.setTimeout(() => was.classList.remove("is-pressed"), delay);
    else was.classList.remove("is-pressed");
  };
  const down = (e: PointerEvent) => {
    if (e.pointerType !== "touch" && e.pointerType !== "pen") return;
    clear();
    const target = e.target instanceof Element ? e.target.closest(PRESSABLE) : null;
    if (!target || (target as HTMLButtonElement).disabled) return;
    el = target;
    x = e.clientX;
    y = e.clientY;
    timer = window.setTimeout(() => el?.classList.add("is-pressed"), 70);
  };
  const move = (e: PointerEvent) => { if (el && Math.hypot(e.clientX - x, e.clientY - y) > 8) clear(); };
  const up = () => { if (!el) return; el.classList.add("is-pressed"); clear(140); };
  const cancel = () => clear();
  document.addEventListener("pointerdown", down, { passive: true });
  document.addEventListener("pointermove", move, { passive: true });
  document.addEventListener("pointerup", up, { passive: true });
  document.addEventListener("pointercancel", cancel, { passive: true });
  window.addEventListener("scroll", cancel, { passive: true, capture: true });
  return () => {
    document.removeEventListener("pointerdown", down);
    document.removeEventListener("pointermove", move);
    document.removeEventListener("pointerup", up);
    document.removeEventListener("pointercancel", cancel);
    window.removeEventListener("scroll", cancel, { capture: true });
  };
}

/** There is a page of the app to go back to (not a page from before it, in the same browser tab). */
export const canGoBack = () => ((window.history.state as { idx?: number } | null)?.idx ?? 0) > 0;

/** A new version of the app was deployed since this page opened (sw.js tells): a pull to refresh reloads it. */
export const update = { ready: false };

/** The width of the left edge where a finger takes the page back (standalone only; swipeable cards leave it alone). */
export const EDGE = 22;
export const edgeSwipe = () => standalone() && touchScreen();

/** The tab a page belongs to. An item page or a note belongs to the tab it was opened from; Réglages to none. */
export function tabOf(path: string, previous: string): string {
  if (path === "/" || path.startsWith("/todo") || path.startsWith("/folders") || path.startsWith("/wiki")) return "/";
  if (path.startsWith("/perso") || path.startsWith("/journal")) return "/perso";
  if (path.startsWith("/digest")) return "/digest";
  if (path.startsWith("/ask")) return "/ask";
  if (path.startsWith("/add")) return "/add";
  if (path.startsWith("/item/") || path.startsWith("/note/")) return previous;
  return "";
}

/** How deep a page sits: a tab's first screen, a page opened from it, a page opened from that. */
export function depth(path: string): number {
  if (["/", "/perso", "/digest", "/ask", "/add"].includes(path) || /^\/digest\/\d+$/.test(path)) return 0;
  if (/^\/(folders|todo|journal|settings|wiki)$/.test(path) || path.startsWith("/journal/") || path === "/digest/interets") return 1;
  return 2;
}

export type Nav = "push" | "back" | "tab" | "fade" | "none";

let last = { path: "", tab: "/", idx: 0 };
let skip = false;
// where each tab's first screen sits in the history, so that tapping the tab from deeper in it goes back there
const roots = new Map<string, { idx: number; path: string }>();

/** How many steps back the first screen of `tab` is from here (0: not in reach, go there instead). */
export function stepsToRoot(tab: string): number {
  const root = roots.get(tab);
  const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0;
  return root && root.path === tab && root.idx < idx ? idx - root.idx : 0;
}

// The page left behind when another is pushed over it, as a still copy: the edge swipe (Gestures.tsx) lays it under
// the page a finger drags away, as the page below shows through on an iPhone. A few levels, by history index.
const below = new Map<number, { node: HTMLElement; scrollY: number }>();

function keepBelow(idx: number) {
  const main = document.querySelector<HTMLElement>(".main");
  if (!main) return;
  const node = main.cloneNode(true) as HTMLElement;
  node.className = "main-copy";      // never taken for the page itself (".main" is looked up by the gestures)
  node.removeAttribute("style");
  node.querySelectorAll("[id]").forEach((n) => n.removeAttribute("id"));
  // players stay still: a copy must never load or play anything
  node.querySelectorAll("iframe, video, audio").forEach((n) => {
    n.removeAttribute("src");
    n.removeAttribute("srcdoc");
    n.querySelectorAll("source").forEach((s) => s.remove());
  });
  below.set(idx, { node, scrollY: window.scrollY });
  for (const k of [...below.keys()]) if (k > idx || k < idx - 3) below.delete(k);
}

/** The copy of the page at history index `idx`, if it was left by a push in this visit. */
export const pageBelow = (idx: number) => below.get(idx);

/** The next page change happens without animation (a swipe back already moved the page). */
export function skipNextAnimation() { skip = true; }

/** Called on every page change, before the view transition starts: `html[data-nav]` picks its animation. */
export function noteNavigation(path: string, action: "POP" | "PUSH" | "REPLACE") {
  if (path === last.path) {             // the same page with other filters: nothing slides
    if (skip) document.documentElement.dataset.nav = "none";
    skip = false;
    return;
  }
  const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0;
  const tab = tabOf(path, last.tab);
  for (const [key, root] of roots) {
    if ((root.idx === idx && root.path !== path) || (action === "PUSH" && root.idx > idx)) roots.delete(key);
  }
  if (depth(path) === 0) roots.set(tab, { idx, path });
  let nav: Nav;
  if (skip || !last.path) nav = "none";
  else if (action === "POP") nav = idx < last.idx ? "back" : "push";
  else if (tab !== last.tab && depth(path) === 0) nav = "tab";
  else if (depth(path) > depth(last.path)) nav = "push";
  else if (depth(path) < depth(last.path)) nav = "back";
  else nav = depth(path) === 2 ? "push" : "fade";
  if (nav === "push" && action === "PUSH") keepBelow(last.idx);   // the old page is still on screen here
  skip = false;
  last = { path, tab, idx };
  document.documentElement.dataset.nav = nav;
}

/** The scroll position a page comes back to: each tab's first screen keeps its own (switching tabs and coming back
 *  finds it where it was), the journal keeps one while days are picked, any other page one per visit. */
export function scrollKey(location: { pathname: string; key: string }): string {
  const p = location.pathname;
  if (["/", "/perso", "/digest", "/ask"].includes(p)) return p;
  if (p.startsWith("/journal")) return "/journal";
  return location.key;
}

/**
 * Cards glide to their new place when a list changes (one pinned moves up, one archived leaves a gap that closes)
 * and new ones fade in, instead of everything jumping. Children carry `data-flip` with a stable id.
 */
export function useFlip(container: RefObject<HTMLElement | null>, deps: unknown) {
  const places = useRef(new Map<string, number>());
  useLayoutEffect(() => {
    const box = container.current;
    if (!box) return;
    const top = box.getBoundingClientRect().top;
    const next = new Map<string, number>();
    const first = places.current.size === 0;
    for (const el of box.querySelectorAll<HTMLElement>(":scope > [data-flip]")) {
      const id = el.dataset.flip!;
      const y = el.getBoundingClientRect().top - top;
      next.set(id, y);
      if (first || reducedMotion() || typeof el.animate !== "function") continue;
      const before = places.current.get(id);
      if (before === undefined) {
        el.animate([{ opacity: 0, transform: "scale(0.98)" }, { opacity: 1, transform: "none" }],
                   { duration: 260, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" });
      } else if (Math.abs(before - y) > 1 && Math.abs(before - y) < 2000) {
        el.animate([{ transform: `translateY(${before - y}px)` }, { transform: "none" }],
                   { duration: 380, easing: "cubic-bezier(0.32, 0.72, 0, 1)" });
      }
    }
    places.current = next;
  }, [deps]); // eslint-disable-line react-hooks/exhaustive-deps
}
