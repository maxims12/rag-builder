"use client";

import * as React from "react";
import { Loader2, Save, AlertCircle, RefreshCw } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

// ---------------------------------------------------------------------------
// Page header
// ---------------------------------------------------------------------------

export function PageHeader({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="space-y-1">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {description ? (
        <p className="text-sm text-muted-foreground">{description}</p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// A labelled field row. Mobile-first: label stacks above the control; on wider
// screens the control sits to the right with the label as a fixed-width column.
// ---------------------------------------------------------------------------

export function Field({
  label,
  htmlFor,
  hint,
  children,
  className,
}: {
  label: string;
  htmlFor?: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1.5 sm:grid sm:grid-cols-[180px_1fr] sm:items-center sm:gap-4",
        className
      )}
    >
      <div className="flex flex-col gap-0.5">
        <Label htmlFor={htmlFor}>{label}</Label>
        {hint ? (
          <span className="text-xs text-muted-foreground">{hint}</span>
        ) : null}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

// A row that pairs a label/hint with a trailing control (used for switches).
export function ToggleField({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="flex flex-col gap-0.5">
        <Label>{label}</Label>
        {hint ? (
          <span className="text-xs text-muted-foreground">{hint}</span>
        ) : null}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Save bar (sticky-ish footer button)
// ---------------------------------------------------------------------------

export function SaveBar({
  saving,
  onSave,
  dirty = true,
  label = "Save changes",
}: {
  saving: boolean;
  onSave: () => void;
  dirty?: boolean;
  label?: string;
}) {
  return (
    <div className="flex justify-end">
      <Button onClick={onSave} disabled={saving || !dirty}>
        {saving ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Save className="h-4 w-4" />
        )}
        {label}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton for config pages
// ---------------------------------------------------------------------------

export function ConfigSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-4">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex flex-col gap-2">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-9 w-full" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error state for a config section: a clear message plus a retry button.
// Rendered when a section fails to load (so pages don't hang on a skeleton).
// ---------------------------------------------------------------------------

export function ConfigError({
  message,
  onRetry,
  retrying = false,
}: {
  message?: string | null;
  onRetry: () => void;
  retrying?: boolean;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed p-6 text-center">
      <AlertCircle className="h-6 w-6 text-destructive" />
      <div className="space-y-1">
        <p className="text-sm font-medium">Couldn&apos;t load this section</p>
        <p className="break-words text-xs text-muted-foreground">
          {message || "An unexpected error occurred."}
        </p>
      </div>
      <Button variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
        {retrying ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <RefreshCw className="h-4 w-4" />
        )}
        Retry
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// A simple string-list editor (comma/newline-free chip list with add/remove).
// Used for file_types, exclude_patterns, web_urls, strip_selectors.
// ---------------------------------------------------------------------------

import { Input } from "@/components/ui/input";
import { Plus, X } from "lucide-react";

export function StringListEditor({
  values,
  onChange,
  placeholder,
  inputType = "text",
}: {
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  inputType?: string;
}) {
  const [draft, setDraft] = React.useState("");

  const add = () => {
    const trimmed = draft.trim();
    if (!trimmed || values.includes(trimmed)) {
      setDraft("");
      return;
    }
    onChange([...values, trimmed]);
    setDraft("");
  };

  const remove = (idx: number) => {
    onChange(values.filter((_, i) => i !== idx));
  };

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Input
          type={inputType}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <Button type="button" variant="outline" size="icon" onClick={add}>
          <Plus className="h-4 w-4" />
        </Button>
      </div>
      {values.length > 0 ? (
        <ul className="flex flex-wrap gap-2">
          {values.map((v, idx) => (
            <li
              key={`${v}-${idx}`}
              className="inline-flex max-w-full items-center gap-1.5 rounded-md border bg-muted/50 py-1 pl-2.5 pr-1 text-xs"
            >
              <span className="truncate">{v}</span>
              <button
                type="button"
                onClick={() => remove(idx)}
                className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label={`Remove ${v}`}
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">None added.</p>
      )}
    </div>
  );
}
