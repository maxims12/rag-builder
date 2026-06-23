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

export default function VectorStorePage() {
  const cfg = useConfigSection("vectorstore", "Vector store config");

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <PageHeader
        title="Vector Store"
        description="Where embeddings are stored and how nearest neighbours are computed."
      />

      <Card>
        <CardHeader>
          <CardTitle>Store</CardTitle>
          <CardDescription>Backend, collection, and index params.</CardDescription>
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
              <Field label="Backend" htmlFor="vs_backend">
                <Select
                  value={cfg.data.vs_backend}
                  onValueChange={(v) =>
                    cfg.set("vs_backend", v as typeof cfg.data.vs_backend)
                  }
                >
                  <SelectTrigger id="vs_backend">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="chroma">Chroma</SelectItem>
                    <SelectItem value="qdrant">Qdrant</SelectItem>
                    <SelectItem value="pgvector">pgvector</SelectItem>
                    <SelectItem value="milvus">Milvus</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <Field label="Collection name" htmlFor="vs_collection">
                <Input
                  id="vs_collection"
                  value={cfg.data.vs_collection}
                  onChange={(e) => cfg.set("vs_collection", e.target.value)}
                />
              </Field>

              <Field label="Distance metric" htmlFor="vs_distance">
                <Select
                  value={cfg.data.vs_distance}
                  onValueChange={(v) =>
                    cfg.set("vs_distance", v as typeof cfg.data.vs_distance)
                  }
                >
                  <SelectTrigger id="vs_distance">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cosine">Cosine</SelectItem>
                    <SelectItem value="euclidean">Euclidean</SelectItem>
                    <SelectItem value="dot">Dot product</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <Field
                label="HNSW M"
                htmlFor="vs_hnsw_m"
                hint="Link count per node"
              >
                <Input
                  id="vs_hnsw_m"
                  type="number"
                  min={2}
                  value={cfg.data.vs_hnsw_m}
                  onChange={(e) =>
                    cfg.set("vs_hnsw_m", Number(e.target.value))
                  }
                />
              </Field>

              <Field
                label="HNSW ef_construct"
                htmlFor="vs_hnsw_ef_construct"
                hint="Build accuracy/speed trade-off"
              >
                <Input
                  id="vs_hnsw_ef_construct"
                  type="number"
                  min={1}
                  value={cfg.data.vs_hnsw_ef_construct}
                  onChange={(e) =>
                    cfg.set("vs_hnsw_ef_construct", Number(e.target.value))
                  }
                />
              </Field>

              <ToggleField
                label="Persist on disk"
                hint="Off = in-memory only"
              >
                <Switch
                  checked={cfg.data.vs_on_disk}
                  onCheckedChange={(v) => cfg.set("vs_on_disk", v)}
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
