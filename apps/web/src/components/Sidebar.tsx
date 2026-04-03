"use client";
/**
 * Collapsible sidebar navigation.
 */
import { useState, useEffect } from "react";
import { usePathname } from "next/navigation";

interface NavItem {
  href: string;
  label: string;
  icon: string;
  soon?: boolean;
}

const NAV_SECTIONS: { label: string; items: NavItem[] }[] = [
  {
    label: "Markets",
    items: [
      { href: "/overview", label: "Overview", icon: "\u25A3" },
      { href: "/opportunities", label: "Opportunities", icon: "\u25CE" },
      { href: "/funding", label: "Funding Rates", icon: "\u25B6" },
      { href: "/basis", label: "Basis Trades", icon: "\u25C7" },
    ],
  },
  {
    label: "Assets",
    items: [
      { href: "/assets", label: "Assets", icon: "\u25A1" },
      { href: "/tokens", label: "Token Universe", icon: "\u25CB" },
      { href: "/assets/BTC", label: "BTC", icon: "\u20BF" },
      { href: "/assets/ETH", label: "ETH", icon: "\u039E" },
      { href: "/assets/SOL", label: "SOL", icon: "\u25C6" },
      { href: "/assets/USDC", label: "USDC", icon: "$" },
    ],
  },
  {
    label: "Tools",
    items: [
      { href: "/optimizer", label: "Optimizer", icon: "\u2192" },
      { href: "/book", label: "Book", icon: "\u25A0" },
    ],
  },
];

export default function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  // Persist collapsed state in localStorage
  useEffect(() => {
    const saved = localStorage.getItem("yc-sidebar-collapsed");
    if (saved === "true") setCollapsed(true);
  }, []);

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem("yc-sidebar-collapsed", String(next));
  };

  return (
    <aside className={`sb-root ${collapsed ? "sb-collapsed" : ""}`}>
      {/* Collapse toggle */}
      <button className="sb-toggle" onClick={toggle} aria-label="Toggle sidebar">
        {collapsed ? "\u276F" : "\u276E"}
      </button>

      <nav className="sb-nav">
        {NAV_SECTIONS.map((section) => (
          <div key={section.label} className="sb-section">
            {!collapsed && <div className="sb-section-label">{section.label}</div>}
            {section.items.map((item) => {
              const active =
                pathname === item.href ||
                (item.href !== "/" && pathname.startsWith(item.href));
              return (
                <a
                  key={item.href}
                  href={item.href}
                  className={`sb-link ${active ? "sb-active" : ""} ${"soon" in item && item.soon ? "sb-soon" : ""}`}
                  title={collapsed ? item.label : undefined}
                >
                  <span className="sb-icon">{item.icon}</span>
                  {!collapsed && (
                    <span className="sb-label">
                      {item.label}
                      {"soon" in item && item.soon && (
                        <span className="sb-soon-tag">soon</span>
                      )}
                    </span>
                  )}
                </a>
              );
            })}
          </div>
        ))}
      </nav>
    </aside>
  );
}
