"use client";

import { useState } from "react";
import { Loader2, FlaskConical, Download } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  ingestWebSources,
  testWebExtraction,
  type WebTestResponse,
} from "@/lib/config";
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
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  PageHeader,
  Field,
  ToggleField,
  SaveBar,
  ConfigSkeleton,
  ConfigError,
  StringListEditor,
} from "@/components/config-form";

export default function WebSourcesPage() {
  const cfg = useConfigSection("web_sources", "Web sources config");

  const [testUrl, setTestUrl] = useState("");
  const [testing, setTesting] = useState(false);
  const [preview, setPreview] = useState<WebTestResponse | null>(null);
  const [ingesting, setIngesting] = useState(false);

  const runTest = async () => {
    const url = testUrl.trim();
    if (!url) {
      toast.error("Enter a URL to test");
      return;
    }
    setTesting(true);
    setPreview(null);
    try {
      const result = await testWebExtraction({
        url,
        render_js: cfg.data?.render_js ?? false,
        strip_selectors: cfg.data?.strip_selectors ?? [],
      });
      setPreview(result);
      toast.success("Extraction succeeded");
    } catch (err) {
      const message =
        err instanceof ApiError ? err.detail || err.message : "Request failed";
      toast.error("Extraction failed", { description: message });
    } finally {
      setTesting(false);
    }
  };

  const runIngest = async () => {
    setIngesting(true);
    try {
      const ack = await ingestWebSources();
      toast.success("Web ingestion started", {
        description: `Job #${ack.job_id} (${ack.status})`,
      });
    } catch (err) {
      const message =
        err instanceof ApiError ? err.detail || err.message : "Request failed";
      toast.error("Could not start ingestion", { description: message });
    } finally {
      setIngesting(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <PageHeader
        title="Web Sources"
        description="Fetch, crawl, or sitemap-scan web pages into the same collection."
      />

      <Card>
        <CardHeader>
          <CardTitle>Targets</CardTitle>
          <CardDescription>
            Choose a mode and configure the crawl behaviour.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {cfg.loading && !cfg.data ? (
            <ConfigSkeleton rows={6} />
          ) : cfg.error && !cfg.data ? (
            <ConfigError message={cfg.error} onRetry={cfg.reload} />
          ) : !cfg.data ? (
            <ConfigSkeleton rows={6} />
          ) : (
            <>
              <Field label="Mode" htmlFor="web_mode" hint="Scan strategy">
                <Select
                  value={cfg.data.web_mode}
                  onValueChange={(v) =>
                    cfg.set("web_mode", v as typeof cfg.data.web_mode)
                  }
                >
                  <SelectTrigger id="web_mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="single">Single / multi URL</SelectItem>
                    <SelectItem value="crawl">Recursive crawl</SelectItem>
                    <SelectItem value="sitemap">Sitemap</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <div className="space-y-2">
                <p className="text-sm font-medium">URLs</p>
                <StringListEditor
                  values={cfg.data.web_urls}
                  onChange={(next) => cfg.set("web_urls", next)}
                  placeholder="https://example.com/docs"
                  inputType="url"
                />
              </div>

              {cfg.data.web_mode === "sitemap" ? (
                <Field
                  label="Sitemap URL"
                  htmlFor="sitemap_url"
                  hint="Used in sitemap mode"
                >
                  <Input
                    id="sitemap_url"
                    type="url"
                    value={cfg.data.sitemap_url ?? ""}
                    placeholder="https://example.com/sitemap.xml"
                    onChange={(e) =>
                      cfg.set("sitemap_url", e.target.value || null)
                    }
                  />
                </Field>
              ) : null}

              {cfg.data.web_mode === "crawl" ? (
                <>
                  <Field
                    label="Crawl depth"
                    htmlFor="crawl_depth"
                    hint="Link hops to follow"
                  >
                    <Input
                      id="crawl_depth"
                      type="number"
                      min={0}
                      value={cfg.data.crawl_depth}
                      onChange={(e) =>
                        cfg.set("crawl_depth", Number(e.target.value))
                      }
                    />
                  </Field>
                  <Field
                    label="Max pages"
                    htmlFor="max_pages"
                    hint="Hard cap"
                  >
                    <Input
                      id="max_pages"
                      type="number"
                      min={1}
                      value={cfg.data.max_pages}
                      onChange={(e) =>
                        cfg.set("max_pages", Number(e.target.value))
                      }
                    />
                  </Field>
                  <ToggleField
                    label="Same domain only"
                    hint="Don't wander off the start domain"
                  >
                    <Switch
                      checked={cfg.data.same_domain_only}
                      onCheckedChange={(v) => cfg.set("same_domain_only", v)}
                    />
                  </ToggleField>
                </>
              ) : null}

              <Field
                label="Request timeout (s)"
                htmlFor="request_timeout_s"
              >
                <Input
                  id="request_timeout_s"
                  type="number"
                  min={1}
                  value={cfg.data.request_timeout_s}
                  onChange={(e) =>
                    cfg.set("request_timeout_s", Number(e.target.value))
                  }
                />
              </Field>

              <Field
                label="Crawl concurrency"
                htmlFor="crawl_concurrency"
                hint="Parallel fetches"
              >
                <Input
                  id="crawl_concurrency"
                  type="number"
                  min={1}
                  value={cfg.data.crawl_concurrency}
                  onChange={(e) =>
                    cfg.set("crawl_concurrency", Number(e.target.value))
                  }
                />
              </Field>

              <ToggleField
                label="Render JS"
                hint="Use a headless browser for JS-heavy pages"
              >
                <Switch
                  checked={cfg.data.render_js}
                  onCheckedChange={(v) => cfg.set("render_js", v)}
                />
              </ToggleField>

              <ToggleField
                label="Respect robots.txt"
                hint="Enforce crawl policy"
              >
                <Switch
                  checked={cfg.data.respect_robots_txt}
                  onCheckedChange={(v) => cfg.set("respect_robots_txt", v)}
                />
              </ToggleField>

              <div className="space-y-2">
                <p className="text-sm font-medium">Strip selectors</p>
                <p className="text-xs text-muted-foreground">
                  Extra CSS selectors to drop (nav, footer, ads).
                </p>
                <StringListEditor
                  values={cfg.data.strip_selectors}
                  onChange={(next) => cfg.set("strip_selectors", next)}
                  placeholder="e.g. .navigation, footer"
                />
              </div>

              <div className="space-y-4 rounded-lg border p-4">
                <ToggleField
                  label="Auto-refresh"
                  hint="Periodically re-crawl via scheduler"
                >
                  <Switch
                    checked={cfg.data.auto_refresh}
                    onCheckedChange={(v) => cfg.set("auto_refresh", v)}
                  />
                </ToggleField>
                {cfg.data.auto_refresh ? (
                  <Field
                    label="Interval (hours)"
                    htmlFor="refresh_interval_hours"
                  >
                    <Input
                      id="refresh_interval_hours"
                      type="number"
                      min={1}
                      value={cfg.data.refresh_interval_hours}
                      onChange={(e) =>
                        cfg.set(
                          "refresh_interval_hours",
                          Number(e.target.value)
                        )
                      }
                    />
                  </Field>
                ) : null}
              </div>

              <div className="flex flex-col gap-2 sm:flex-row sm:justify-between">
                <Button
                  variant="outline"
                  onClick={runIngest}
                  disabled={ingesting}
                >
                  {ingesting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Download className="h-4 w-4" />
                  )}
                  Ingest web sources
                </Button>
                <SaveBar saving={cfg.saving} onSave={cfg.save} />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Test extraction</CardTitle>
          <CardDescription>
            Preview the cleaned text for a single URL without saving it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              type="url"
              value={testUrl}
              placeholder="https://example.com/docs/intro"
              onChange={(e) => setTestUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") runTest();
              }}
            />
            <Button onClick={runTest} disabled={testing}>
              {testing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <FlaskConical className="h-4 w-4" />
              )}
              Test
            </Button>
          </div>

          {preview ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">
                  {preview.title || "(untitled)"}
                </span>
                <Badge variant="secondary">
                  {preview.extracted_text_length.toLocaleString()} chars
                </Badge>
                <Badge variant="outline">
                  raw {preview.raw_html_length.toLocaleString()}
                </Badge>
              </div>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 text-xs">
                {preview.clean_text}
              </pre>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
