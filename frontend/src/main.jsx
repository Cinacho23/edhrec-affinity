import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";

import App from "./App.jsx";
import "./index.css";

/**
 * HashRouter is the simplest GitHub Pages-safe router.
 *
 * Instead of:
 *   /commanders/jasmine-boreal-of-the-seven
 *
 * deployed pages look like:
 *   /#/commanders/jasmine-boreal-of-the-seven
 *
 * This avoids static-host refresh problems on GitHub Pages.
 */
ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </React.StrictMode>
);