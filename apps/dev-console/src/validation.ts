/** Mirror server bounds without logging or returning user inputs in error strings. */
export const utf8Bytes = (value: string): number => new TextEncoder().encode(value).byteLength;
export const characters = (value: string): number => Array.from(value).length;
function unicode(value: string): boolean {
  for (const character of value) { const code = character.codePointAt(0)!; if (code >= 0xd800 && code <= 0xdfff) return false; }
  return true;
}
export const validUsername = (value: string): boolean => /^[A-Za-z0-9_]{3,32}$/.test(value);
export const validPassword = (value: string): boolean => unicode(value) && characters(value) >= 10 && characters(value) <= 128 && utf8Bytes(value) <= 256;
export const validDisplayName = (value: string): boolean => unicode(value) && !!value.trim() && characters(value) <= 80 && utf8Bytes(value.trim()) <= 240 && !/\p{C}/u.test(value);
export const validImport = (value: string): boolean => unicode(value) && !!value.trim() && utf8Bytes(value) <= 1024 * 1024;
/** Stored memory may contain literal controls; only the server decides what may enter an actual prompt. */
export const validMemoryText = (value: string, maximum: number, required = true): boolean => unicode(value) && characters(value) <= maximum && (!required || !!value.trim());
export const validCdkIssue = (energy: number, count: number, days: number): boolean => Number.isInteger(energy) && energy >= 1 && energy <= 1000000 && Number.isInteger(count) && count >= 1 && count <= 50 && Number.isInteger(days) && days >= 1 && days <= 365;
