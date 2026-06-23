"use client";

import { useRef, useState } from "react";
import { Send, Square, Globe, FileText, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { streamSSE } from "@/lib/sse";
import { type PlaygroundSource } from "@/lib/config";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/config-form";

export default function PlaygroundPage() {
  const [query, setQuery] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<PlaygroundSource[]>([]);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  };

  const ask = async () => {
    const q = query.trim();
    if (!q || streaming) return;

    setAnswer("");
    setSources([]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamSSE("/playground/query", {
        method: "POST",
        body: { query: q, stream: true },
        signal: controller.signal,
        onMessage: (msg) => {
          let payload: unknown;
          try {
            payload = JSON.parse(msg.data);
          } catch {
            return;
          }
          switch (msg.event) {
            case "source": {
              const p = payload as { sources?: PlaygroundSource[] };
              if (p.sources) setSources(p.sources);
              break;
            }
            case "token": {
              const p = payload as { token?: string };
              if (typeof p.token === "string") {
                setAnswer((prev) => prev + p.token);
              }
              break;
            }
            case "done": {
              const p = payload as {
                answer?: string;
                sources?: PlaygroundSource[];
              };
              if (typeof p.answer === "string") setAnswer(p.answer);
              if (p.sources) setSources(p.sources);
              break;
            }
            case "error": {
              const p = payload as { message?: string };
              toast.error("Generation failed", {
                description: p.message || "Unknown error",
              });
              break;
            }
          }
        },
      });
    } catch (err) {
      if (!controller.signal.aborted) {
        const message =
          err instanceof Error ? err.message : "Streaming request failed";
        toast.error("Query failed", { description: message });
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setStreaming(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <PageHeader
        title="Playground"
        description="Ask a question grounded in your ingested content."
      />

      <Card>
        <CardContent className="space-y-3 pt-6">
          <Textarea
            rows={3}
            placeholder="How do I configure the server timeout?"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                ask();
              }
            }}
          />
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">
              Cmd/Ctrl + Enter to send
            </span>
            {streaming ? (
              <Button variant="outline" onClick={stop}>
                <Square className="h-4 w-4" />
                Stop
              </Button>
            ) : (
              <Button onClick={ask} disabled={!query.trim()}>
                <Send className="h-4 w-4" />
                Ask
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {(answer || streaming) && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Answer
              {streaming ? (
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              ) : null}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">
              {answer || (
                <span className="text-muted-foreground">Thinking…</span>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {sources.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-medium text-muted-foreground">
            Sources ({sources.length})
          </h2>
          <div className="space-y-3">
            {sources.map((s, idx) => {
              const isWeb = s.source_type === "web";
              return (
                <Card key={`${s.source_path_or_url}-${idx}`}>
                  <CardHeader className="p-4 pb-2">
                    <div className="flex items-start justify-between gap-2">
                      <CardTitle className="flex min-w-0 items-center gap-2 text-sm">
                        {isWeb ? (
                          <Globe className="h-4 w-4 shrink-0" />
                        ) : (
                          <FileText className="h-4 w-4 shrink-0" />
                        )}
                        <span className="truncate">
                          {s.title || s.source_path_or_url}
                        </span>
                      </CardTitle>
                      <Badge variant="secondary" className="shrink-0">
                        {s.score.toFixed(3)}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-2 p-4 pt-0">
                    {isWeb ? (
                      <a
                        href={s.source_path_or_url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="block truncate text-xs text-primary underline-offset-2 hover:underline"
                      >
                        {s.source_path_or_url}
                      </a>
                    ) : (
                      <CardDescription className="truncate font-mono text-xs">
                        {s.source_path_or_url}
                      </CardDescription>
                    )}
                    <p className="break-words text-xs text-muted-foreground">
                      {s.snippet}
                    </p>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
