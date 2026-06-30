import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Alpaca Paper Dashboard",
  description: "Read-only view of the Alpaca paper trading account.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-ink-950 text-gray-200 antialiased">
        {children}
      </body>
    </html>
  );
}
