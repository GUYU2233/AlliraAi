# @allira/client

A lean, framework-neutral TypeScript SDK for Allira's `/api/v1` HTTP and SSE API. No UI, runtime dependencies, EventSource, automatic retries, or automatic reconnection. Requires Fetch, ReadableStream, TextDecoder and AbortController (modern browsers or Node.js 20+). Bundlers can consume the ESM output and generated declarations.

## Build and test

Run these commands inside `packages/client` on the development server:

```sh
npm ci
npm test
# Build only:
npm run build
```

Tests use Node's built-in test runner and mocked Fetch responses, never a real model provider. `npm test` first compiles the TypeScript source. The committed npm lock pins the compiler; there are no runtime dependencies.

## Session and conversation

After building, the example below can run from this package directory as an ESM script. Consumers of the installed package import from `@allira/client` instead of `./dist/index.js`.

```js
import { AlliraClient } from './dist/index.js';

const baseUrl = 'http://localhost:8000'; // SDK appends /api/v1
const bootstrap = new AlliraClient({ baseUrl });
// Development only: the server must explicitly enable its dev session endpoint.
const session = await bootstrap.createDevSession({ userId: 'local-user' });
const client = new AlliraClient({
  baseUrl,
  accessToken: session.accessToken,
});
const conversation = await client.createConversation({
  title: 'First chat',
  instructions: null,
});
const firstPage = await client.listConversations({ limit: 20 });
if (firstPage.nextCursor !== null) {
  const nextPage = await client.listConversations({
    limit: 20,
    cursor: firstPage.nextCursor,
  });
  console.log(nextPage.items);
}
console.log(await client.getConversation(conversation.id));
console.log(await client.listMessages(conversation.id, { limit: 20 }));
```

`accessToken` accepts a string or a synchronous/asynchronous callback returning a token (or undefined). The callback is evaluated separately for each protected request, including SSE, so applications can supply a fresh token. Dev session creation does not attach Bearer credentials and does not change the client's token. Credentials and refresh policies remain application-owned. An existing base URL ending in `/api/v1` is also accepted without doubling the suffix. An optional `fetch` implementation can be injected for tests or runtime integration.

## Run streaming, cancellation and explicit resume

```js
import { StreamInterruptedError } from './dist/index.js';

// Generate once per logical send. Reuse this exact UUID for any explicit retry.
const clientMessageId = crypto.randomUUID();
const run = await client.createRun(conversation.id, {
  clientMessageId,
  content: 'Hello!',
}); // The server returns 202 with a Run; idempotent by clientMessageId.
const controller = new AbortController();
let lastProcessedId = 0;

async function consume(after) {
  for await (const event of client.streamRunEvents(run.id, {
    after,
    signal: controller.signal,
  })) {
    if (event.type === 'message.delta') {
      console.log(event.data.delta);
    } else if (event.type === 'run.started') {
      console.log(event.data.run.status);
    } else {
      // Terminal data is canonical: replace locally accumulated content with it.
      console.log(event.data.run, event.data.message);
    }
    // Advance only AFTER application processing succeeds.
    lastProcessedId = event.id;
  }
}

try {
  await consume(lastProcessedId);
} catch (error) {
  if (error instanceof StreamInterruptedError) {
    console.warn('Unconfirmed EOF; choose whether/when to resume', error.lastEventId);
    // Explicit application choice, not an SDK retry loop:
    await consume(lastProcessedId);
  } else {
    throw error; // Abort and transport errors retain their original identity.
  }
}
console.log(await client.getRun(run.id));
// Abort only stops this HTTP stream; it does NOT cancel server-side generation:
// controller.abort();
// To cancel generation explicitly:
// await client.cancelRun(run.id);
```

SSE uses Fetch with `Authorization: Bearer …`, not EventSource. Events expose `{ id: number, type, data }` with the wire names `run.started`, `message.delta`, `run.completed`, `run.failed`, and `run.cancelled`. IDs are decimal, safe positive integers increasing within a run. `after` must be a nonnegative safe integer and is sent as a query parameter (default `0`). Heartbeat comments are ignored. UTF-8 and line endings can span arbitrary network chunks. Multiline `data:` and LF/CRLF/CR framing are supported. A terminal event is yielded before the iterator completes and closes the connection; failure/cancellation are terminal events, not transport exceptions. Breaking out of iteration also releases the connection.

A clean EOF without a complete terminal event throws `StreamInterruptedError`; its `lastEventId` identifies the last parsed event, not necessarily the last event your application processed. Never infer completion from EOF or a partial final frame. Network failures propagate unchanged and may also warrant an explicit resume. Abort reasons propagate unchanged; no POST, token refresh, or reconnect is retried automatically. A malformed event name, ID, sequence, or JSON raises `StreamProtocolError`. Event JSON payloads and successful HTTP response bodies are typed contracts, not runtime schema validation.

## HTTP failures and request options

```js
import { AlliraApiError } from './dist/index.js';

try {
  await client.getRun('missing-run', { signal: new AbortController().signal });
} catch (error) {
  if (error instanceof AlliraApiError) {
    console.error(error.status, error.code, error.message);
  } else {
    throw error;
  }
}
```

All methods accept a final options object with `signal`. List methods additionally accept `limit` and `cursor`; returned pages contain `items` and nullable `nextCursor`. `streamRunEvents` takes `{ after?, signal? }`. HTTP error envelopes `{ error: { code, message } }` become `AlliraApiError` with the HTTP status; non-JSON error bodies use `http_error` and the status text. No SDK operation implicitly creates a dev session or sends a request to a model provider.
