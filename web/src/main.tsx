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
import { noteNavigation, skipNextAnimation } from "./motion";
import "@fontsource-variable/newsreader/opsz.css";
import "@fontsource-variable/newsreader/opsz-italic.css";
import "@fontsource-variable/instrument-sans";
import "@fontsource-variable/geist-mono";
import "./styles.css";

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
}
