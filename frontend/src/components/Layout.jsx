import { NavLink, Outlet } from "react-router";

/*
  Layout wraps every page.

  Outlet is where the active page component appears.
  NavLink is like a normal link, but it knows when it is active.
*/

export default function Layout() {
  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="site-header__inner">
          <NavLink to="/" className="brand">
            <span className="brand__mark">EA</span>
            <span>
              <strong>EDHREC Affinity</strong>
              <small>Commander Tag Analysis</small>
            </span>
          </NavLink>

          <nav className="site-nav" aria-label="Main navigation">
            <NavLink to="/" end>
              Home
            </NavLink>
            <NavLink to="/commanders">Commanders</NavLink>
            <NavLink to="/leaderboard">Leaderboard</NavLink>
            <NavLink to="/tags">Tag Explorer</NavLink>
            <NavLink to="/methodology">Methodology</NavLink>
          </nav>
        </div>
      </header>

      <main className="site-main">
        <Outlet />
      </main>

      <footer className="site-footer">
        <p>
         Built as a static analysis project. Data is processed before being
         displayed by the frontend.
        </p>
      </footer>
    </div>
  );
}