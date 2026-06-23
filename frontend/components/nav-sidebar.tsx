"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Boxes } from "lucide-react";

import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "@/components/nav-items";
import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";

// Desktop sidebar. Hidden below the md breakpoint (mobile uses the bottom nav).
export function NavSidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  return (
    <aside className="hidden md:flex md:w-60 md:flex-col md:border-r md:bg-card">
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <Boxes className="h-5 w-5" />
        <span className="text-sm font-semibold">RAG Builder</span>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto p-3">
        {NAV_ITEMS.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t p-3">
        {user ? (
          <p className="mb-2 truncate px-1 text-xs text-muted-foreground">
            {user.email}
          </p>
        ) : null}
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start gap-3 text-muted-foreground"
          onClick={() => logout()}
        >
          <LogOut className="h-4 w-4" />
          Log out
        </Button>
      </div>
    </aside>
  );
}
