import { Routes, Route } from "react-router-dom";

import Layout from "./components/Layout";
import HomePage from "./pages/HomePage";
import CommanderSearchPage from "./pages/CommanderSearchPage";
import CommanderDetailPage from "./pages/CommanderDetailPage";
import GlobalLeaderboardPage from "./pages/GlobalLeaderboardPage";
import TagExplorerPage from "./pages/TagExplorerPage";
import SetExplorerPage from "./pages/SetExplorerPage";
import MethodologyPage from "./pages/MethodologyPage";
import NotFoundPage from "./pages/NotFoundPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/commanders" element={<CommanderSearchPage />} />
        <Route path="/commanders/:commanderSlug" element={<CommanderDetailPage />} />
        <Route path="/leaderboard" element={<GlobalLeaderboardPage />} />
        <Route path="/tags" element={<TagExplorerPage />} />
        <Route path="/sets" element={<SetExplorerPage />} />
        <Route path="/methodology" element={<MethodologyPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
