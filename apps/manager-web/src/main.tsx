import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import App from "./App";
import "./index.css";
import { initPreferences } from "./lib/preferences";

initPreferences(); // áp giao diện sáng/tối và cỡ chữ đã chọn TRƯỚC khi render, tránh nháy

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename="/admin">
      <App />
      <Toaster position="top-center" richColors closeButton />
    </BrowserRouter>
  </StrictMode>,
);
