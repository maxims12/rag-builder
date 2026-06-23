"use client";

import { useConfigSection } from "@/lib/use-config-section";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
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

export default function RetrievalPage() {
  const cfg = useConfigSection("retrieval", "Retrieval config");

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <PageHeader
        title="Retrieval"
        description="How many chunks to fetch and how to rank them."
      />

      <Card>
        <CardHeader>
          <CardTitle>Search</CardTitle>
          <CardDescription>Similarity, diversity, and reranking.</CardDescription>
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
              <Field label="Top-k" hint={`${cfg.data.top_k} chunks`}>
                <Slider
                  min={1}
                  max={50}
                  step={1}
                  value={[cfg.data.top_k]}
                  onValueChange={([v]) => cfg.set("top_k", v)}
                />
              </Field>

              <Field
                label="Score threshold"
                hint={cfg.data.score_threshold.toFixed(2)}
              >
                <Slider
                  min={0}
                  max={1}
                  step={0.01}
                  value={[cfg.data.score_threshold]}
                  onValueChange={([v]) => cfg.set("score_threshold", v)}
                />
              </Field>

              <Field label="Search type" htmlFor="search_type">
                <Select
                  value={cfg.data.search_type}
                  onValueChange={(v) =>
                    cfg.set("search_type", v as typeof cfg.data.search_type)
                  }
                >
                  <SelectTrigger id="search_type">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="similarity">Similarity</SelectItem>
                    <SelectItem value="mmr">MMR</SelectItem>
                    <SelectItem value="hybrid">Hybrid</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              {cfg.data.search_type === "mmr" ? (
                <Field
                  label="MMR diversity"
                  hint={cfg.data.mmr_diversity.toFixed(2)}
                >
                  <Slider
                    min={0}
                    max={1}
                    step={0.01}
                    value={[cfg.data.mmr_diversity]}
                    onValueChange={([v]) => cfg.set("mmr_diversity", v)}
                  />
                </Field>
              ) : null}

              {cfg.data.search_type === "hybrid" ? (
                <>
                  <Field
                    label="Hybrid method"
                    htmlFor="hybrid_method"
                    hint="How the lexical side is scored"
                  >
                    <Select
                      value={cfg.data.hybrid_method}
                      onValueChange={(v) =>
                        cfg.set(
                          "hybrid_method",
                          v as typeof cfg.data.hybrid_method
                        )
                      }
                    >
                      <SelectTrigger id="hybrid_method">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="token_overlap">
                          Token overlap (candidates only, fast)
                        </SelectItem>
                        <SelectItem value="bm25">
                          BM25 (whole collection)
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>

                  <Field
                    label="Hybrid alpha"
                    hint={`${cfg.data.hybrid_alpha.toFixed(2)} (semantic vs lexical)`}
                  >
                    <Slider
                      min={0}
                      max={1}
                      step={0.01}
                      value={[cfg.data.hybrid_alpha]}
                      onValueChange={([v]) => cfg.set("hybrid_alpha", v)}
                    />
                  </Field>
                </>
              ) : null}

              <ToggleField
                label="Reranking"
                hint="Second-stage neural reranker"
              >
                <Switch
                  checked={cfg.data.reranking}
                  onCheckedChange={(v) => cfg.set("reranking", v)}
                />
              </ToggleField>

              {cfg.data.reranking ? (
                <Field label="Reranker model" htmlFor="reranker_model">
                  <Input
                    id="reranker_model"
                    value={cfg.data.reranker_model}
                    onChange={(e) =>
                      cfg.set("reranker_model", e.target.value)
                    }
                  />
                </Field>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Advanced (LLM-assisted)</CardTitle>
          <CardDescription>
            Query expansion and compression run through your configured LLM —
            better recall and tighter context, at extra latency and cost per
            query.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {!cfg.data ? (
            <ConfigSkeleton rows={3} />
          ) : (
            <>
              <ToggleField
                label="Multi-query"
                hint="Rewrite the question into variants and merge the results"
              >
                <Switch
                  checked={cfg.data.multi_query}
                  onCheckedChange={(v) => cfg.set("multi_query", v)}
                />
              </ToggleField>

              {cfg.data.multi_query ? (
                <Field
                  label="Query variants"
                  hint={`${cfg.data.multi_query_count} extra ${
                    cfg.data.multi_query_count === 1 ? "query" : "queries"
                  }`}
                >
                  <Slider
                    min={1}
                    max={8}
                    step={1}
                    value={[cfg.data.multi_query_count]}
                    onValueChange={([v]) => cfg.set("multi_query_count", v)}
                  />
                </Field>
              ) : null}

              <ToggleField
                label="Contextual compression"
                hint="Keep only the query-relevant parts of each retrieved chunk"
              >
                <Switch
                  checked={cfg.data.contextual_compression}
                  onCheckedChange={(v) =>
                    cfg.set("contextual_compression", v)
                  }
                />
              </ToggleField>

              <SaveBar saving={cfg.saving} onSave={cfg.save} />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
