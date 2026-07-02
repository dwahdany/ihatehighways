/** Full-screen onboarding shown when VITE_GOOGLE_MAPS_API_KEY is missing. */
export default function SetupCard() {
  return (
    <div className="setup-screen">
      <main className="setup-card">
        <span className="eyebrow">Setup</span>
        <h1>Almost there</h1>
        <p>ihatehighways needs two Google Maps API keys before it can plan a ride.</p>
        <ol className="setup-steps">
          <li>
            Create a <strong>browser key</strong> in the Google Cloud console and enable{' '}
            <strong>Maps JavaScript API</strong> and <strong>Places API (New)</strong>.
          </li>
          <li>
            Create a <strong>backend key</strong> with <strong>Routes API</strong> enabled.
          </li>
          <li>
            Put them in place: the browser key goes in{' '}
            <code className="kv">frontend/.env.local</code> as{' '}
            <code className="kv">VITE_GOOGLE_MAPS_API_KEY=…</code>, the backend key in{' '}
            <code className="kv">backend/.env</code> as{' '}
            <code className="kv">GOOGLE_MAPS_API_KEY=…</code>.
          </li>
        </ol>
        <p className="setup-muted">Restart the dev server after saving and reload this page.</p>
      </main>
    </div>
  )
}
