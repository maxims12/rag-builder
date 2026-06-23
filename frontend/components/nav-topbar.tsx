"use client";

import { usePathname } from "next/navigation";
import { LogOut, Boxes } from "lucide-react";

import { NAV_ITEMS } from "@/components/nav-items";
import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";

// Mobile-only top bar: brand + current page title + logout (the bottom nav has
// no room for a logout control).
export function NavTopbar() {
  const pathname = usePathname();
  const { logout } = useAuth();

  const current =
    NAV_ITEMS.find((item) =>
      item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)
    ) ?? NAV_ITEMS[0];

  return (
    <header className="flex h-14 items-center justify-between border-b bg-card px-4 md:hidden">
      <div className="flex min-w-0 items-center gap-2">
        <Boxes className="h-5 w-5 shrink-0" />
        <span className="truncate text-sm font-semibold">{current.label}</span>
      </div>
      <Button
        variant="ghost"
        size="icon"
        aria-label="Log out"
        onClick={() => logout()}
      >
        <LogOut className="h-4 w-4" />
      </Button>
    </header>
  );
}
