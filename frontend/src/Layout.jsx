// Page shell: header (name, tagline, who's signed in), content, footer.
// Photo credits (PhotoCredit.jsx) and the disclosure line belong in `footer`.
export default function Layout({ title, tagline, user, actions, footer, children }) {
  return (
    <div className="container">
      <header className="app-header">
        <div>
          <h1>{title}</h1>
          {tagline && <p className="muted" style={{ margin: 0 }}>{tagline}</p>}
        </div>
        <div className="row">
          {user && <span className="muted">Signed in as {user.email}</span>}
          {actions}
        </div>
      </header>
      <main className="stack">{children}</main>
      {footer && <footer className="app-footer">{footer}</footer>}
    </div>
  )
}
