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

export default function PipelinePage() {
  const chunk = useConfigSection("chunking", "Chunking config");
  const emb = useConfigSection("embedding", "Embedding config");

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <PageHeader
        title="Pipeline"
        description="How documents are split into chunks and turned into embeddings."
      />

      <Card>
        <CardHeader>
          <CardTitle>Chunking</CardTitle>
          <CardDescription>Text splitting strategy and sizing.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {chunk.loading && !chunk.data ? (
            <ConfigSkeleton rows={4} />
          ) : chunk.error && !chunk.data ? (
            <ConfigError message={chunk.error} onRetry={chunk.reload} />
          ) : !chunk.data ? (
            <ConfigSkeleton rows={4} />
          ) : (
            <>
              <Field label="Strategy" htmlFor="chunk_strategy">
                <Select
                  value={chunk.data.chunk_strategy}
                  onValueChange={(v) =>
                    chunk.set(
                      "chunk_strategy",
                      v as typeof chunk.data.chunk_strategy
                    )
                  }
                >
                  <SelectTrigger id="chunk_strategy">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="recursive">Recursive</SelectItem>
                    <SelectItem value="semantic">Semantic</SelectItem>
                    <SelectItem value="fixed">Fixed</SelectItem>
                    <SelectItem value="markdown">Markdown</SelectItem>
                    <SelectItem value="token">Token</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <Field
                label="Chunk size"
                hint={`${chunk.data.chunk_size} chars`}
              >
                <Slider
                  min={100}
                  max={4000}
                  step={50}
                  value={[chunk.data.chunk_size]}
                  onValueChange={([v]) => chunk.set("chunk_size", v)}
                />
              </Field>

              <Field
                label="Chunk overlap"
                hint={`${chunk.data.chunk_overlap} chars`}
              >
                <Slider
                  min={0}
                  max={1000}
                  step={10}
                  value={[chunk.data.chunk_overlap]}
                  onValueChange={([v]) => chunk.set("chunk_overlap", v)}
                />
              </Field>

              <Field
                label="Min chunk size"
                htmlFor="min_chunk_size"
                hint="Drop chunks smaller than this"
              >
                <Input
                  id="min_chunk_size"
                  type="number"
                  min={0}
                  value={chunk.data.min_chunk_size}
                  onChange={(e) =>
                    chunk.set("min_chunk_size", Number(e.target.value))
                  }
                />
              </Field>

              <ToggleField
                label="Respect sentence boundary"
                hint="Force split points at punctuation"
              >
                <Switch
                  checked={chunk.data.respect_sentence_boundary}
                  onCheckedChange={(v) =>
                    chunk.set("respect_sentence_boundary", v)
                  }
                />
              </ToggleField>

              <SaveBar saving={chunk.saving} onSave={chunk.save} />
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Embedding</CardTitle>
          <CardDescription>
            Provider and model used to vectorise chunks.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {emb.loading && !emb.data ? (
            <ConfigSkeleton rows={4} />
          ) : emb.error && !emb.data ? (
            <ConfigError message={emb.error} onRetry={emb.reload} />
          ) : !emb.data ? (
            <ConfigSkeleton rows={4} />
          ) : (
            <>
              <Field label="Provider" htmlFor="emb_provider">
                <Select
                  value={emb.data.emb_provider}
                  onValueChange={(v) =>
                    emb.set("emb_provider", v as typeof emb.data.emb_provider)
                  }
                >
                  <SelectTrigger id="emb_provider">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="huggingface">HuggingFace</SelectItem>
                    <SelectItem value="openai">OpenAI</SelectItem>
                    <SelectItem value="cohere">Cohere</SelectItem>
                    <SelectItem value="ollama">Ollama</SelectItem>
                    <SelectItem value="voyage">Voyage</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <Field label="Model" htmlFor="emb_model">
                <Input
                  id="emb_model"
                  value={emb.data.emb_model}
                  onChange={(e) => emb.set("emb_model", e.target.value)}
                />
              </Field>

              <Field
                label="Dimensions"
                htmlFor="emb_dimensions"
                hint="Optional — leave blank for model default"
              >
                <Input
                  id="emb_dimensions"
                  type="number"
                  min={1}
                  value={emb.data.emb_dimensions ?? ""}
                  onChange={(e) =>
                    emb.set(
                      "emb_dimensions",
                      e.target.value === "" ? null : Number(e.target.value)
                    )
                  }
                />
              </Field>

              <Field
                label="Batch size"
                htmlFor="emb_batch_size"
              >
                <Input
                  id="emb_batch_size"
                  type="number"
                  min={1}
                  value={emb.data.emb_batch_size}
                  onChange={(e) =>
                    emb.set("emb_batch_size", Number(e.target.value))
                  }
                />
              </Field>

              <Field label="Device" htmlFor="emb_device">
                <Select
                  value={emb.data.emb_device}
                  onValueChange={(v) =>
                    emb.set("emb_device", v as typeof emb.data.emb_device)
                  }
                >
                  <SelectTrigger id="emb_device">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cpu">CPU</SelectItem>
                    <SelectItem value="cuda">CUDA</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <ToggleField
                label="Normalize embeddings"
                hint="Scale vectors to unit length"
              >
                <Switch
                  checked={emb.data.emb_normalize}
                  onCheckedChange={(v) => emb.set("emb_normalize", v)}
                />
              </ToggleField>

              <div className="flex items-center justify-between">
                <Badge variant="secondary">{emb.data.emb_provider}</Badge>
                <SaveBar saving={emb.saving} onSave={emb.save} />
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
