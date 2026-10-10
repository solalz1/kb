// How the app moves: which way a page slides in (view transitions, styles.css), the gestures of an installed app
// (components/Gestures.tsx), and cards that glide to their new place when a list changes.
import { useLayoutEffect, useRef, type RefObject } from "react";

/** Opened from the home screen (iPhone) or as an installed app: no browser bar, so no reload button or back gesture. */
export const standalone = () =>
  (navigator as Navigator & { standalone?: boolean }).standalone === true || window.matchMedia("(display-mode: standalone)").matches;
export const touchScreen = () => window.matchMedia("(pointer: coarse)").matches;
export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The width of the left edge where a finger takes the page back (standalone only; swipeable cards leave it alone). */
export const EDGE = 22;
export const edgeSwipe = () => standalone() && touchScreen();

/** The tab a page belongs to. An item page or a note belongs to the tab it was opened from; Réglages to none. */
export function tabOf(path: string, previous: string): string {
  if (path === "/" || path.startsWith("/todo") || path.startsWith("/folders")) return "/";
  if (path.startsWith("/perso") || path.startsWith("/journal")) return "/perso";
  if (path.startsWith("/digest")) return "/digest";
  if (path.startsWith("/ask")) return "/ask";
  if (path.startsWith("/add")) return "/add";
  if (path.startsWith("/item/") || path.startsWith("/note/")) return previous;
  return "";
}

/** How deep a page sits: a tab's first screen, a page opened from it, a page opened from that. */
function depth(path: string): number {
  if (["/", "/perso", "/digest", "/ask", "/add"].includes(path) || /^\/digest\/\d+$/.test(path)) return 0;
  if (/^\/(folders|todo|journal|settings)$/.test(path) || path.startsWith("/journal/") || path === "/digest/interets") return 1;
  return 2;
}

export type Nav = "push" | "back" | "tab" | "fade" | "none";

let last = { path: "", tab: "/", idx: 0 };
let skip = false;

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
  let nav: Nav;
  if (skip || !last.path) nav = "none";
  else if (action === "POP") nav = idx < last.idx ? "back" : "push";
  else if (tab !== last.tab && depth(path) === 0) nav = "tab";
  else if (depth(path) > depth(last.path)) nav = "push";
  else if (depth(path) < depth(last.path)) nav = "back";
  else nav = depth(path) === 2 ? "push" : "fade";
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
