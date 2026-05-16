/*
  ErrorState displays a readable error message if JSON loading fails.
*/

export default function ErrorState({ title = "Something went wrong", error }) {
  return (
    <div className="status-box status-box--error" role="alert">
      <h2>{title}</h2>

      <p>
        Check that the required JSON files exist in{" "}
        <code>frontend/public/data/latest/</code>.
      </p>

      {error ? <pre>{error.message}</pre> : null}
    </div>
  );
}