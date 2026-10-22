import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import Curator from "./components/Curator";
import EvalReport from "./components/EvalReport";
import SharedAnswer from "./components/SharedAnswer";
import "./index.css";

// A shared answer lives at /s/{id}, the Curator at /curator and the eval results at /eval; every other path is the app.
const shared = /^\/s\/([A-Za-z0-9_-]{16})\/?$/.exec(location.pathname)?.[1];
const curator = /^\/curator\/?$/.test(location.pathname);
const evalReport = /^\/eval\/?$/.test(location.pathname);

const queries = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queries}>
      {shared ? <SharedAnswer id={shared} /> : curator ? <Curator /> : evalReport ? <EvalReport /> : <App />}
    </QueryClientProvider>
  </StrictMode>,
);
