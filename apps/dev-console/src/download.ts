/** Explicit user action only. Never persist credentials or payloads in browser storage. */
export function downloadText(content: string, filename: string, type = 'text/plain;charset=utf-8'): void {
  const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 120) || 'allira-export.txt';
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url; link.download = safeName; link.rel = 'noopener noreferrer';
  try { document.body.appendChild(link); link.click(); }
  finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1_000); }
}
