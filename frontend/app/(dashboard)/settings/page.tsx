"use client";

import { useState } from "react";
import { Loader2, Save } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  putSection,
  type CredentialsConfig,
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
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
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
} from "@/components/config-form";

const MASKED = "******";

const CREDENTIAL_FIELDS: { key: keyof CredentialsConfig; label: string }[] = [
  { key: "anthropic_api_key", label: "Anthropic API key" },
  { key: "openai_api_key", label: "OpenAI API key" },
  { key: "cohere_api_key", label: "Cohere API key" },
  { key: "groq_api_key", label: "Groq API key" },
  { key: "voyage_api_key", label: "Voyage API key" },
];

export default function SettingsPage() {
  const llm = useConfigSection("llm", "LLM config");
  const sys = useConfigSection("system", "System config");
  const creds = useConfigSection("credentials", "API credentials");

  // Write-only credential inputs. Empty = leave the stored key untouched.
  const [credInputs, setCredInputs] = useState<Record<string, string>>({});
  const [savingCreds, setSavingCreds] = useState(false);

  const saveCredentials = async () => {
    // Only send non-empty values; masked/empty fields are omitted so the
    // backend preserves the stored key (CONTRACT.md §2 credentials rule).
    const payload: Partial<CredentialsConfig> = {};
    for (const { key } of CREDENTIAL_FIELDS) {
      const value = (credInputs[key] ?? "").trim();
      if (value && value !== MASKED) {
        payload[key] = value;
      }
    }
    if (Object.keys(payload).length === 0) {
      toast.info("No new keys to save");
      return;
    }
    setSavingCreds(true);
    try {
      await putSection("credentials", payload as CredentialsConfig);
      setCredInputs({});
      await creds.reload();
      toast.success("API credentials saved");
    } catch (err) {
      const message =
        err instanceof ApiError ? err.detail || err.message : "Request failed";
      toast.error("Could not save credentials", { description: message });
    } finally {
      setSavingCreds(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <PageHeader
        title="Settings"
        description="LLM behaviour, API credentials, and system-level controls."
      />

      <Card>
        <CardHeader>
          <CardTitle>LLM</CardTitle>
          <CardDescription>Answer generation provider and model.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {llm.loading && !llm.data ? (
            <ConfigSkeleton rows={5} />
          ) : llm.error && !llm.data ? (
            <ConfigError message={llm.error} onRetry={llm.reload} />
          ) : !llm.data ? (
            <ConfigSkeleton rows={5} />
          ) : (
            <>
              <Field label="Provider" htmlFor="llm_provider">
                <Select
                  value={llm.data.llm_provider}
                  onValueChange={(v) =>
                    llm.set("llm_provider", v as typeof llm.data.llm_provider)
                  }
                >
                  <SelectTrigger id="llm_provider">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="anthropic">Anthropic</SelectItem>
                    <SelectItem value="openai">OpenAI</SelectItem>
                    <SelectItem value="ollama">Ollama</SelectItem>
                    <SelectItem value="groq">Groq</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <Field label="Model" htmlFor="llm_model">
                <Input
                  id="llm_model"
                  value={llm.data.llm_model}
                  onChange={(e) => llm.set("llm_model", e.target.value)}
                />
              </Field>

              <Field
                label="Temperature"
                hint={llm.data.temperature.toFixed(2)}
              >
                <Slider
                  min={0}
                  max={2}
                  step={0.05}
                  value={[llm.data.temperature]}
                  onValueChange={([v]) => llm.set("temperature", v)}
                />
              </Field>

              <Field label="Max tokens" htmlFor="max_tokens">
                <Input
                  id="max_tokens"
                  type="number"
                  min={1}
                  value={llm.data.max_tokens}
                  onChange={(e) =>
                    llm.set("max_tokens", Number(e.target.value))
                  }
                />
              </Field>

              <Field label="System prompt" htmlFor="system_prompt">
                <Textarea
                  id="system_prompt"
                  rows={3}
                  value={llm.data.system_prompt}
                  onChange={(e) => llm.set("system_prompt", e.target.value)}
                />
              </Field>

              <ToggleField
                label="Streaming"
                hint="Stream tokens in the playground"
              >
                <Switch
                  checked={llm.data.streaming}
                  onCheckedChange={(v) => llm.set("streaming", v)}
                />
              </ToggleField>

              <SaveBar saving={llm.saving} onSave={llm.save} />
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>API credentials</CardTitle>
          <CardDescription>
            Keys are stored securely and never shown again. Leave a field blank to
            keep the existing key.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {creds.loading && !creds.data ? (
            <ConfigSkeleton rows={5} />
          ) : creds.error && !creds.data ? (
            <ConfigError message={creds.error} onRetry={creds.reload} />
          ) : !creds.data ? (
            <ConfigSkeleton rows={5} />
          ) : (
            <>
              {CREDENTIAL_FIELDS.map(({ key, label }) => {
                const stored = creds.data![key] === MASKED;
                return (
                  <Field
                    key={key}
                    label={label}
                    htmlFor={key}
                    hint={stored ? "A key is stored" : "Not set"}
                  >
                    <div className="flex items-center gap-2">
                      <Input
                        id={key}
                        type="password"
                        autoComplete="new-password"
                        placeholder={stored ? "•••••• (stored)" : "Enter key"}
                        value={credInputs[key] ?? ""}
                        onChange={(e) =>
                          setCredInputs((prev) => ({
                            ...prev,
                            [key]: e.target.value,
                          }))
                        }
                      />
                      {stored ? (
                        <Badge variant="success" className="shrink-0">
                          set
                        </Badge>
                      ) : null}
                    </div>
                  </Field>
                );
              })}

              <div className="flex justify-end">
                <Button onClick={saveCredentials} disabled={savingCreds}>
                  {savingCreds ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Save className="h-4 w-4" />
                  )}
                  Save credentials
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>System</CardTitle>
          <CardDescription>Concurrency, caching, and limits.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {sys.loading && !sys.data ? (
            <ConfigSkeleton rows={4} />
          ) : sys.error && !sys.data ? (
            <ConfigError message={sys.error} onRetry={sys.reload} />
          ) : !sys.data ? (
            <ConfigSkeleton rows={4} />
          ) : (
            <>
              <Field label="Parallel workers" htmlFor="parallel_workers">
                <Input
                  id="parallel_workers"
                  type="number"
                  min={1}
                  value={sys.data.parallel_workers}
                  onChange={(e) =>
                    sys.set("parallel_workers", Number(e.target.value))
                  }
                />
              </Field>

              <Field
                label="Rate limit (rpm)"
                htmlFor="rate_limit_rpm"
              >
                <Input
                  id="rate_limit_rpm"
                  type="number"
                  min={1}
                  value={sys.data.rate_limit_rpm}
                  onChange={(e) =>
                    sys.set("rate_limit_rpm", Number(e.target.value))
                  }
                />
              </Field>

              <Field label="Log level" htmlFor="log_level">
                <Select
                  value={sys.data.log_level}
                  onValueChange={(v) => sys.set("log_level", v)}
                >
                  <SelectTrigger id="log_level">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="DEBUG">DEBUG</SelectItem>
                    <SelectItem value="INFO">INFO</SelectItem>
                    <SelectItem value="WARNING">WARNING</SelectItem>
                    <SelectItem value="ERROR">ERROR</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <ToggleField
                label="Cache embeddings"
                hint="Reuse embeddings to save cost"
              >
                <Switch
                  checked={sys.data.cache_embeddings}
                  onCheckedChange={(v) => sys.set("cache_embeddings", v)}
                />
              </ToggleField>

              <SaveBar saving={sys.saving} onSave={sys.save} />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
