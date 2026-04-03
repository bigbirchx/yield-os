import type { Metadata } from "next";
import "./globals.css";
import "./dashboard.css";
import DataRefresher from "@/components/DataRefresher";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";

export const metadata: Metadata = {
  title: "Yield Cockpit",
  description: "Institutional crypto yield monitoring",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="app-shell">
          <Sidebar />
          <div className="app-body">
            <TopBar />
            <main className="app-main">{children}</main>
          </div>
        </div>
        <DataRefresher />
      </body>
    </html>
  );
}
