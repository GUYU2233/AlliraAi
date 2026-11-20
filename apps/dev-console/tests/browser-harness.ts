// Test-only bundle: never included in the served dist or service worker allowlist.
import { renderMarkdown } from '../src/markdown.ts';
import { MessageList } from '../src/messages.ts';
import { beginReplay, applyRunEvent } from '../src/state.ts';
Object.assign(window, { consoleHarness: { renderMarkdown, MessageList, beginReplay, applyRunEvent } });
