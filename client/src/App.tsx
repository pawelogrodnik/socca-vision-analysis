export default function App() {
  const isAdmin = window.location.pathname.startsWith('/admin-panel');
  return (
    <main className="app">
      <section className="hero">
        <p className="eyebrow">{isAdmin ? 'Local admin panel' : 'Public viewer'}</p>
        <h1>Socca Vision Analysis</h1>
        <p>{isAdmin ? 'Identity candidate resolver jest poniżej.' : 'Publiczny widok opublikowanych meczów.'}</p>
        <a href={isAdmin ? '/' : '/admin-panel'}>{isAdmin ? 'Public viewer' : 'Admin panel'}</a>
      </section>
    </main>
  );
}
