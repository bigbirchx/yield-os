import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Yield Cockpit",
  description: "Institutional crypto yield monitoring",
};

const NAV_LINKS = [
  { href: "/overview", label: "Overview" },
  { href: "/assets/BTC", label: "BTC" },
  { href: "/assets/ETH", label: "ETH" },
  { href: "/assets/SOL", label: "SOL" },
  { href: "/assets/USDC", label: "USDC" },
];

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <header className="app-header">
          <a href="/" className="app-logo">
            YIELD COCKPIT
          </a>
          <nav className="app-nav">
            {NAV_LINKS.map((link) => (
              <a key={link.href} href={link.href} className="app-nav-link">
                {link.label}
              </a>
            ))}
          </nav>
          <span className="app-env-tag">INTERNAL · MVP</span>
        </header>
        <main className="app-main">{children}</main>
      </body>
    </html>
  );
}
