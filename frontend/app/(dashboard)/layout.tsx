"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

import { useAuth } from "@/components/auth-provider";
import { NavSidebar } from "@/components/nav-sidebar";
import { NavMobile } from "@/components/nav-mobile";
import { NavTopbar } from "@/components/nav-topbar";

// Responsive shell + client-side route guard for all dashboard pages.
// - Desktop (md+): persistent sidebar on the left.
// - Mobile (<md): top bar + fixed bottom nav, content padded to avoid overlap.
// Unauthenticated users are redirected to /login once bootstrap completes.
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen w-full">
      <NavSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <NavTopbar />
        <main className="flex-1 overflow-x-hidden p-4 pb-24 md:p-6 md:pb-6">
          {children}
        </main>
      </div>
      <NavMobile />
    </div>
  );
}
