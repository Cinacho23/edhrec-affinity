/*
  App.jsx

  This file defines the frontend route table.

  Chat 9 routes:
  - Home
  - Commander Search
  - Methodology
  - Not Found

  Chat 10 routes:
  - Global Leaderboard
  - Tag Explorer

  New route added now:
  - Commander Detail:
      /commanders/:commanderSlug
*/

import { Routes, Route } from "react-router";

import Layout from "./components/Layout.jsx";

import HomePage from "./pages/HomePage.jsx";
import CommanderSearchPage from "./pages/CommanderSearchPage.jsx";
import CommanderDetailPage from "./pages/CommanderDetailPage.jsx";
import GlobalLeaderboardPage from "./pages/GlobalLeaderboardPage.jsx";
import TagExplorerPage from "./pages/TagExplorerPage.jsx";
import MethodologyPage from "./pages/MethodologyPage.jsx";
import NotFoundPage from "./pages/NotFoundPage.jsx";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />

        <Route path="/commanders" element={<CommanderSearchPage />} />
        <Route
          path="/commanders/:commanderSlug"
          element={<CommanderDetailPage />}
        />

        <Route path="/leaderboard" element={<GlobalLeaderboardPage />} />
        <Route path="/tags" element={<TagExplorerPage />} />

        <Route path="/methodology" element={<MethodologyPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}