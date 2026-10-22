import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, useLocation } from "react-router-dom";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import Add from "./pages/Add";
import Ask from "./pages/Ask";
import Digest from "./pages/Digest";
import Feed from "./pages/Feed";
import Folders from "./pages/Folders";
import Interests from "./pages/Interests";
import ItemPage from "./pages/ItemPage";
import Journal from "./pages/Journal";
import NoteEditor from "./pages/NoteEditor";
import Settings from "./pages/Settings";
import Todo from "./pages/Todo";
import Wiki from "./pages/Wiki";
import WikiPage from "./pages/WikiPage";
import { devCopy, noteNavigation, skipNextAnimation, update } from "./motion";
import "@fontsource-variable/newsreader/opsz.css";
import "@fontsource-variable/newsreader/opsz-italic.css";
import "@fontsource-variable/instrument-sans";
import "@fontsource-variable/geist-mono";
import "./styles.css";

// A page change that interrupts another (back while a page is still sliding in) skips the first one's animation:
// expected, not an error. Its promises reject with an AbortError that nobody else catches.
let sliding: ViewTransition | null = null;
if (typeof document.startViewTransition === "function") {
  const start = document.startViewTransition.bind(document);
  document.startViewTransition = ((update?: Parameters<typeof start>[0]) => {
    const transition = start(update);
    for (const p of [transition.ready, transition.updateCallbackDone, transition.finished]) p.catch(() => {});
    sliding = transition;
    transition.finished.finally(() => { if (sliding === transition) sliding = null; }).catch(() => {});
    return transition;
  }) as typeof document.startViewTransition;
}
// While a page slides, the browser shows pictures of the pages and every tap lands on <html>, lost. As in an iPhone
// app, a finger that comes down ends the slide at once, and its tap reaches what is under it.
document.addEventListener("pointerdown", () => { sliding?.skipTransition(); }, { capture: true });
document.addEventListener("click", (e) => {
  if (!e.isTrusted || e.target !== document.documentElement) return;
  const target = document.elementFromPoint(e.clientX, e.clientY)
    ?.closest<HTMLElement>("a[href], button, [role='button'], [role='menuitem'], [role='tab'], label, summary");
  if (!target) return;
  e.preventDefault();
  e.stopPropagation();
  target.click();
}, { capture: true });
window.addEventListener("unhandledrejection", (e) => {
  const reason = e.reason as { name?: string; message?: string } | null;
  if (reason?.name === "AbortError" && /transition/i.test(reason.message ?? "")) e.preventDefault();
});

// Safari's and Chrome's own back gestures already animate the page: no second animation on top of theirs.
window.addEventListener("popstate", (e) => {
  if ((e as PopStateEvent & { hasUAVisualTransition?: boolean }).hasUAVisualTransition) skipNextAnimation();
});

/** Whatever breaks, going to another page tries again. */
function Root() {
  const location = useLocation();
  return <ErrorBoundary resetKey={location.key}><App /></ErrorBoundary>;
}

// A data router, every page a route of it: links and navigate() run view transitions, and <ScrollRestoration> (in
// App) puts each page back where it was.
const router = createBrowserRouter([{
  element: <Root />,
  children: [
    { path: "/", element: <Feed key="main" space="main" /> },
    { path: "/perso", element: <Feed key="perso" space="perso" /> },
    { path: "/folders", element: <Folders /> },
    { path: "/folders/:folderId", element: <Feed key="folder" /> },
    { path: "/journal", element: <Journal /> },
    { path: "/journal/:day", element: <Journal /> },
    { path: "/note/new", element: <NoteEditor key="new" /> },
    { path: "/note/:id/edit", element: <NoteEditor key="edit" /> },
    { path: "/item/:id", element: <ItemPage /> },
    { path: "/wiki", element: <Wiki /> },
    { path: "/wiki/:slug", element: <WikiPage /> },
    { path: "/ask", element: <Ask /> },
    { path: "/digest", element: <Digest /> },
    { path: "/digest/interets", element: <Interests /> },
    { path: "/digest/:id", element: <Digest /> },
    { path: "/add", element: <Add /> },
    { path: "/todo", element: <Todo /> },
    { path: "/settings", element: <Settings /> },
    { path: "/settings/:section", element: <Settings /> },
    { path: "*", element: <Feed key="main" space="main" /> },
  ],
}]);
noteNavigation(router.state.location.pathname, "PUSH");
// a copy of the app to try a new version on: its tab and its home-screen icon say so
if (devCopy()) {
  document.title = "KB dev";
  document.querySelector('meta[name="apple-mobile-web-app-title"]')?.setAttribute("content", "KB dev");
}
router.subscribe((state) => noteNavigation(state.location.pathname, state.historyAction));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <RouterProvider router={router} />
    </ErrorBoundary>
  </StrictMode>,
);

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
  navigator.serviceWorker.addEventListener("message", (e) => { if (e.data?.type === "kb:update") update.ready = true; });
}
