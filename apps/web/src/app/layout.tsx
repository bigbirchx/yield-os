import type { Metadata } from "next";
import "./globals.css";

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
      <body>{children}</body>
    </html>
  );
}
