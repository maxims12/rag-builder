"use client";

import { useEffect, useRef, useState } from "react";
import {
  FileText,
  Boxes,
  Clock,
  Activity,
  Globe,
  CheckCircle2,
  XCircle,
  Loader2,
} from "lucide-react";

import { streamSSE, type SSEMessage } from "@/lib/sse";
import {
  listJobs,
  type HealthStats,
  type IndexJob,
} from "@/lib/config";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function statusVariant(
  status: string
): "default" | "secondary" | "destructive" | "success" | "warning" {
  switch (status) {
    case "done":
      return "success";
    case "running":
    case "pending":
      return "warning";
    case "error":
      return "destructive";
    default:
      return "secondary";
  }
}

export default function OverviewPage() {
  const [health, setHealth] = useState<HealthStats | null>(null);
  const [jobs, setJobs] = useState<IndexJob[]>([]);
  const [loaded, setLoaded] = useState(false);

  // Merge a single job_progress event into the recent-jobs list.
  const upsertJob = (job: IndexJob) => {
    setJobs((prev) => {
      const idx = prev.findIndex((j) => j.id === job.id);
      if (idx === -1) return [job, ...prev].slice(0, 10);
      const next = [...prev];
      next[idx] = job;
      return next;
    });
  };

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    let pollTimer: ReturnType<typeof setInterval> | null = null;

    // Seed the jobs list immediately via the REST endpoint.
    const seed = async () => {
      try {
        const res = await listJobs(10, 0);
        if (!cancelled) {
          setJobs(res.jobs);
          setLoaded(true);
        }
      } catch {
        if (!cancelled) setLoaded(true);
      }
    };

    // Fallback: poll the jobs endpoint when SSE is unavailable.
    const startPolling = () => {
      if (pollTimer) return;
      pollTimer = setInterval(async () => {
        try {
          const res = await listJobs(10, 0);
          if (!cancelled) setJobs(res.jobs);
        } catch {
          /* ignore transient errors */
        }
      }, 5000);
    };

    const handleMessage = (msg: SSEMessage) => {
      let payload: unknown;
      try {
        payload = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (msg.event === "health") {
        setHealth(payload as HealthStats);
      } else if (msg.event === "job_progress") {
        upsertJob(payload as IndexJob);
      }
    };

    void seed();

    streamSSE("/pipeline/status", {
      method: "GET",
      signal: controller.signal,
      onMessage: handleMessage,
    }).catch(() => {
      // SSE failed/closed — fall back to polling for job stats.
      if (!cancelled) startPolling();
    });

    return () => {
      cancelled = true;
      controller.abort();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, []);

  const lastSync =
    jobs.find((j) => j.status === "done" && j.finished_at)?.finished_at ?? null;

  const stats: {
    label: string;
    value: string | number;
    icon: typeof FileText;
  }[] = [
    {
      label: "Documents",
      value: health
        ? health.local_docs_count + health.web_pages_count
        : "—",
      icon: FileText,
    },
    {
      label: "Chunks",
      value: health ? health.total_chunks_count.toLocaleString() : "—",
      icon: Boxes,
    },
    { label: "Last sync", value: formatTime(lastSync), icon: Clock },
    {
      label: "Store health",
      value: health ? (health.vector_store_healthy ? "Healthy" : "Down") : "—",
      icon: Activity,
    },
  ];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">
          Live ingestion stats and recent jobs.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {stats.map((s) => {
          const Icon = s.icon;
          return (
            <Card key={s.label}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 p-4 pb-1">
                <CardDescription>{s.label}</CardDescription>
                <Icon className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent className="p-4 pt-0">
                <p className="truncate text-xl font-semibold">{s.value}</p>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {health ? (
        <div className="flex flex-wrap gap-3">
          <HealthPill label="Vector store" ok={health.vector_store_healthy} />
          <HealthPill label="Database" ok={health.db_healthy} />
          <Badge variant="outline" className="gap-1.5">
            <FileText className="h-3.5 w-3.5" />
            {health.local_docs_count} local
          </Badge>
          <Badge variant="outline" className="gap-1.5">
            <Globe className="h-3.5 w-3.5" />
            {health.web_pages_count} web
          </Badge>
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Recent jobs</CardTitle>
          <CardDescription>Latest ingestion runs (files + pages).</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!loaded ? (
            <>
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </>
          ) : jobs.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No ingestion jobs yet. Run a re-index from Sources or ingest from
              Web Sources.
            </p>
          ) : (
            jobs.map((job) => (
              <div
                key={job.id}
                className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex items-center gap-2">
                  {job.status === "running" || job.status === "pending" ? (
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                  ) : job.source_type === "web" ? (
                    <Globe className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <FileText className="h-4 w-4 text-muted-foreground" />
                  )}
                  <span className="text-sm font-medium">
                    Job #{job.id}
                  </span>
                  <Badge variant={statusVariant(job.status)}>
                    {job.status}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {job.source_type}
                  </span>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span>{job.files_processed} files</span>
                  <span>{job.pages_fetched} pages</span>
                  <span>{job.chunks_created} chunks</span>
                  <span>{formatTime(job.started_at)}</span>
                </div>
                {job.error_message ? (
                  <p className="break-words text-xs text-destructive">
                    {job.error_message}
                  </p>
                ) : null}
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function HealthPill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <Badge variant={ok ? "success" : "destructive"} className="gap-1.5">
      {ok ? (
        <CheckCircle2 className="h-3.5 w-3.5" />
      ) : (
        <XCircle className="h-3.5 w-3.5" />
      )}
      {label}
    </Badge>
  );
}
