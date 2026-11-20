import { deflateSync } from 'node:zlib';

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const value of bytes) {
    crc ^= value;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const kind = Buffer.from(type, 'ascii');
  const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4); checksum.writeUInt32BE(crc32(Buffer.concat([kind, data])));
  return Buffer.concat([length, kind, data, checksum]);
}
/** Deterministic opaque public app icon. Native PNG encoding; no dependency or runtime data URI. */
export function appIcon(size) {
  if (![192, 512].includes(size)) throw new Error('Unsupported public icon size');
  const rows = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const nx = x / size; const ny = y / size;
      const left = Math.abs(nx - (0.5 - (ny - 0.22) * 0.35)) < 0.045;
      const right = Math.abs(nx - (0.5 + (ny - 0.22) * 0.35)) < 0.045;
      const bar = ny > 0.59 && ny < 0.67 && nx > 0.38 && nx < 0.62;
      const foreground = ny >= 0.22 && ny <= 0.78 && (left || right || bar);
      const index = y * (size * 4 + 1) + 1 + x * 4;
      rows[index] = foreground ? 255 : 52; rows[index + 1] = foreground ? 255 : 58; rows[index + 2] = foreground ? 255 : 64; rows[index + 3] = 255;
    }
  }
  const header = Buffer.alloc(13); header.writeUInt32BE(size, 0); header.writeUInt32BE(size, 4); header[8] = 8; header[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
}
