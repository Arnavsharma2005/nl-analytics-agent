import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import Curator from "./components/Curator";
import EvalReport from "./components/EvalReport";
import Keys from "./components/Keys";
import { ReportList, ReportView } from "./components/Reports";
import SharedAnswer from "./components/SharedAnswer";
import "./index.css";

// A shared answer lives at /s/{id}, the Curator at /curator, the eval results at /eval and Deep Analysis at /reports
// (a report at /reports/{id}), API keys at /keys; every other path is the app.
const shared = /^\/s\/([A-Za-z0-9_-]{16})\/?$/.exec(location.pathname)?.[1];
const curator = /^\/curator\/?$/.test(location.pathname);
const evalReport = /^\/eval\/?$/.test(location.pathname);
const reports = /^\/reports\/?$/.test(location.pathname);
const keys = /^\/keys\/?$/.test(location.pathname);
const report = /^\/reports\/([0-9a-f-]{36})\/?$/.exec(location.pathname)?.[1];

const queries = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queries}>
      {shared ? <SharedAnswer id={shared} /> : curator ? <Curator /> : evalReport ? <EvalReport /> : reports ? <ReportList /> : report ? <ReportView id={report} /> : keys ? <Keys /> : <App />}
    </QueryClientProvider>
  </StrictMode>,
);
