"use client";

// Shared hook for config pages: load a section on mount, hold a local draft,
// and persist via PUT with success/error toasts. Keeps every page DRY while
// routing all I/O through lib/config.ts -> lib/api.ts.

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  getSection,
  putSection,
  type SectionName,
  type SectionTypeMap,
} from "@/lib/config";

export interface UseConfigSection<K extends SectionName> {
  data: SectionTypeMap[K] | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  set: <F extends keyof SectionTypeMap[K]>(
    field: F,
    value: SectionTypeMap[K][F]
  ) => void;
  patch: (partial: Partial<SectionTypeMap[K]>) => void;
  save: () => Promise<void>;
  reload: () => Promise<void>;
}

export function useConfigSection<K extends SectionName>(
  section: K,
  label: string
): UseConfigSection<K> {
  const [data, setData] = useState<SectionTypeMap[K] | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getSection(section);
      setData(result);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail || err.message
          : "Failed to load configuration";
      setError(message);
      toast.error(`Could not load ${label}`, { description: message });
    } finally {
      setLoading(false);
    }
  }, [section, label]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const set = useCallback(
    <F extends keyof SectionTypeMap[K]>(
      field: F,
      value: SectionTypeMap[K][F]
    ) => {
      setData((prev) => (prev ? { ...prev, [field]: value } : prev));
    },
    []
  );

  const patch = useCallback((partial: Partial<SectionTypeMap[K]>) => {
    setData((prev) => (prev ? { ...prev, ...partial } : prev));
  }, []);

  const save = useCallback(async () => {
    if (!data) return;
    setSaving(true);
    try {
      const updated = await putSection(section, data);
      setData(updated);
      toast.success(`${label} saved`);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail || err.message
          : "Failed to save configuration";
      toast.error(`Could not save ${label}`, { description: message });
    } finally {
      setSaving(false);
    }
  }, [data, section, label]);

  return { data, loading, saving, error, set, patch, save, reload };
}
