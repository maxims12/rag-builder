import {
  LayoutDashboard,
  FolderOpen,
  Globe,
  Workflow,
  Database,
  Search,
  MessageSquare,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  // Short label for the cramped mobile bottom nav.
  shortLabel: string;
  icon: LucideIcon;
}

// Routes from the PAGES table in CLAUDE.md.
export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Overview", shortLabel: "Home", icon: LayoutDashboard },
  { href: "/sources", label: "Sources", shortLabel: "Files", icon: FolderOpen },
  { href: "/web-sources", label: "Web Sources", shortLabel: "Web", icon: Globe },
  { href: "/pipeline", label: "Pipeline", shortLabel: "Pipe", icon: Workflow },
  {
    href: "/vectorstore",
    label: "Vector Store",
    shortLabel: "Store",
    icon: Database,
  },
  { href: "/retrieval", label: "Retrieval", shortLabel: "Search", icon: Search },
  {
    href: "/playground",
    label: "Playground",
    shortLabel: "Ask",
    icon: MessageSquare,
  },
  { href: "/settings", label: "Settings", shortLabel: "Config", icon: Settings },
];

// The mobile bottom nav can only fit a handful of items comfortably at 375px.
export const MOBILE_NAV_ITEMS: NavItem[] = NAV_ITEMS.filter((item) =>
  ["/", "/sources", "/web-sources", "/playground", "/settings"].includes(
    item.href
  )
);
