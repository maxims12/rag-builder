import type { Metadata, Viewport } from "next";
import "./globals.css";

import { AuthProvider } from "@/components/auth-provider";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: "RAG System Builder",
  description:
    "Scan, ingest, and query your documents with a configurable RAG pipeline.",
};

// Mobile-first: lock the viewport so pages design at 375px first.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
        <Toaster richColors position="top-center" />
      </body>
    </html>
  );
}
