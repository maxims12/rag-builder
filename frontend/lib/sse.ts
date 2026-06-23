// Fetch-based SSE reader.
//
// The backend SSE endpoints are authenticated (Bearer header) and one of them
// (/playground/query) is a POST with a JSON body. Native EventSource supports
// neither custom headers nor POST, so we stream the response body via fetch and
// parse the `event:`/`data:` frames ourselves. Requests go through customFetch
// so the in-memory access token is attached and a 401 triggers refresh+retry.

import { apiUrl, customFetch } from "@/lib/api";

export interface SSEMessage {
  event: string;
  data: string;
}

export interface StreamSSEOptions {
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
  onMessage: (msg: SSEMessage) => void;
}

// Streams an authenticated SSE endpoint and invokes onMessage per event frame.
// Resolves when the stream closes; throws on non-2xx or network error (callers
// should catch and surface a toast).
export async function streamSSE(
  path: string,
  { method = "GET", body, signal, onMessage }: StreamSSEOptions
): Promise<void> {
  const res = await customFetch(apiUrl(path), {
    method,
    headers: { Accept: "text/event-stream" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`SSE request failed with status ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line.
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const rawFrame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const msg = parseFrame(rawFrame);
        if (msg) onMessage(msg);
      }
    }
    // Flush any trailing frame without a terminating blank line.
    const tail = buffer.trim();
    if (tail) {
      const msg = parseFrame(tail);
      if (msg) onMessage(msg);
    }
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(frame: string): SSEMessage | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue; // comment / keep-alive
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).replace(/^ /, ""));
    }
  }
  if (dataLines.length === 0 && event === "message") return null;
  return { event, data: dataLines.join("\n") };
}
