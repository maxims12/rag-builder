"use client";

import { useState } from "react";
import { RefreshCw, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import { ingestSources } from "@/lib/config";
import { useConfigSection } from "@/lib/use-config-section";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  PageHeader,
  Field,
  ToggleField,
  SaveBar,
  ConfigSkeleton,
  ConfigError,
  StringListEditor,
} from "@/components/config-form";

const COMMON_FILE_TYPES = [
  ".pdf",
  ".docx",
  ".md",
  ".txt",
  ".html",
  ".csv",
  ".json",
  ".rst",
];

export default function SourcesPage() {
  const cfg = useConfigSection("sources", "Sources config");
  const [reindexing, setReindexing] = useState(false);

  const reindex = async () => {
    setReindexing(true);
    try {
      const ack = await ingestSources();
      toast.success("Re-index started", {
        description: `Job #${ack.job_id} (${ack.status})`,
      });
    } catch (err) {
      const message =
        err instanceof ApiError ? err.detail || err.message : "Request failed";
      toast.error("Could not start re-index", { description: message });
    } finally {
      setReindexing(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <PageHeader
        title="Sources"
        description="Scan a local folder for documents to ingest into the vector store."
      />

      <Card>
        <CardHeader>
          <CardTitle>Local folder</CardTitle>
          <CardDescription>
            Files in this folder are parsed, chunked, embedded, and indexed.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {cfg.loading && !cfg.data ? (
            <ConfigSkeleton rows={5} />
          ) : cfg.error && !cfg.data ? (
            <ConfigError message={cfg.error} onRetry={cfg.reload} />
          ) : !cfg.data ? (
            <ConfigSkeleton rows={5} />
          ) : (
            <>
              <Field
                label="Docs folder"
                htmlFor="docs_folder"
                hint="Absolute or relative path"
              >
                <Input
                  id="docs_folder"
                  value={cfg.data.docs_folder}
                  onChange={(e) => cfg.set("docs_folder", e.target.value)}
                />
              </Field>

              <Field
                label="Max file size (MB)"
                htmlFor="max_file_size_mb"
                hint="Larger files are skipped"
              >
                <Input
                  id="max_file_size_mb"
                  type="number"
                  min={1}
                  value={cfg.data.max_file_size_mb}
                  onChange={(e) =>
                    cfg.set("max_file_size_mb", Number(e.target.value))
                  }
                />
              </Field>

              <Field
                label="Polling interval (s)"
                htmlFor="polling_interval"
                hint="Watchdog tick when watch mode is on"
              >
                <Input
                  id="polling_interval"
                  type="number"
                  min={1}
                  value={cfg.data.polling_interval}
                  onChange={(e) =>
                    cfg.set("polling_interval", Number(e.target.value))
                  }
                />
              </Field>

              <ToggleField
                label="Recursive scan"
                hint="Include subfolders"
              >
                <Switch
                  checked={cfg.data.recursive}
                  onCheckedChange={(v) => cfg.set("recursive", v)}
                />
              </ToggleField>

              <ToggleField
                label="Watch mode"
                hint="Auto re-index on file changes (watchdog)"
              >
                <Switch
                  checked={cfg.data.watch_mode}
                  onCheckedChange={(v) => cfg.set("watch_mode", v)}
                />
              </ToggleField>

              <div className="space-y-2">
                <p className="text-sm font-medium">File types</p>
                <div className="flex flex-wrap gap-2">
                  {Array.from(
                    new Set([...COMMON_FILE_TYPES, ...cfg.data.file_types])
                  ).map((ext) => {
                    const active = cfg.data!.file_types.includes(ext);
                    return (
                      <button
                        key={ext}
                        type="button"
                        onClick={() =>
                          cfg.set(
                            "file_types",
                            active
                              ? cfg.data!.file_types.filter((t) => t !== ext)
                              : [...cfg.data!.file_types, ext]
                          )
                        }
                        className={
                          "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors " +
                          (active
                            ? "border-primary bg-primary text-primary-foreground"
                            : "bg-background text-muted-foreground hover:bg-accent")
                        }
                      >
                        {ext}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2">
                <p className="text-sm font-medium">Exclude patterns</p>
                <StringListEditor
                  values={cfg.data.exclude_patterns}
                  onChange={(next) => cfg.set("exclude_patterns", next)}
                  placeholder="e.g. node_modules/**"
                />
              </div>

              <SaveBar saving={cfg.saving} onSave={cfg.save} />
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Re-index</CardTitle>
          <CardDescription>
            Trigger a fresh ingestion job for the configured folder.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="outline"
            onClick={reindex}
            disabled={reindexing}
          >
            {reindexing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            Re-index now
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
