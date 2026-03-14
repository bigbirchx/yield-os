export default function Home() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "1rem",
      }}
    >
      <h1
        style={{
          fontSize: "1.5rem",
          fontWeight: 600,
          color: "var(--text-primary)",
          letterSpacing: "-0.02em",
        }}
      >
        Yield Cockpit
      </h1>
      <p style={{ color: "var(--text-secondary)" }}>
        Institutional crypto yield monitoring
      </p>
      <nav
        style={{
          display: "flex",
          gap: "1.5rem",
          marginTop: "0.5rem",
          fontSize: "0.875rem",
        }}
      >
        <a href="/overview">Overview</a>
        <a href="/assets/BTC">BTC</a>
        <a href="/assets/ETH">ETH</a>
        <a href="/assets/SOL">SOL</a>
      </nav>
    </main>
  );
}
