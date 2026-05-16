/*
  LoadingState gives the user clear feedback while a JSON file is loading.
*/

export default function LoadingState({ message = "Loading data..." }) {
  return (
    <div className="status-box" role="status">
      <div className="spinner" aria-hidden="true" />
      <p>{message}</p>
    </div>
  );
}