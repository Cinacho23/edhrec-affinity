import { Link } from "react-router";

/*
  Simple 404 page for unmatched routes.
*/

export default function NotFoundPage() {
  return (
    <div className="page page--narrow">
      <section className="status-box">
        <h1>Page not found</h1>
        <p>The page you requested does not exist in this prototype.</p>
        <Link className="button" to="/">
          Return home
        </Link>
      </section>
    </div>
  );
}