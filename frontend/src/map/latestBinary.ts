import type { LatestPosition } from '../api/types'

// Decoder for /api/positions/latest.bin ("DSB1") - mirrors
// backend/app/api/positions.py:_build_latest_bin exactly:
//   "DSB1" | u32 count
//   u32 mmsi[] u32 unix_ts[] i32 lat*1e5[] i32 lon*1e5[]
//   u16 sog*10[] u16 cog*10[] u16 heading*10[]  (0xFFFF = null)
//   u8 flags[] (bit0: source == "region")
//   u32 tail_len | tail JSON {"names": {mmsi: str}, "watch": {mmsi: {...}}}
export function decodeLatestBin(buf: ArrayBuffer): LatestPosition[] {
  const magic = new Uint8Array(buf, 0, 4)
  if (String.fromCharCode(...magic) !== 'DSB1') throw new Error('bad snapshot magic')
  const n = new DataView(buf).getUint32(4, true)
  let o = 8
  const mmsi = new Uint32Array(buf, o, n); o += 4 * n
  const ts = new Uint32Array(buf, o, n); o += 4 * n
  const lat = new Int32Array(buf, o, n); o += 4 * n
  const lon = new Int32Array(buf, o, n); o += 4 * n
  const sog = new Uint16Array(buf, o, n); o += 2 * n
  const cog = new Uint16Array(buf, o, n); o += 2 * n
  const heading = new Uint16Array(buf, o, n); o += 2 * n
  const flags = new Uint8Array(buf, o, n); o += n
  const tailLen = new DataView(buf, o, 4).getUint32(0, true); o += 4
  const tail = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, o, tailLen))) as {
    names: Record<string, string>
    watch: Record<string, Partial<LatestPosition>>
  }

  const out: LatestPosition[] = new Array(n)
  for (let i = 0; i < n; i++) {
    const m = mmsi[i]
    const w = tail.watch[m]
    out[i] = {
      mmsi: m,
      ts: new Date(ts[i] * 1000).toISOString(),
      lat: lat[i] / 1e5,
      lon: lon[i] / 1e5,
      sog: sog[i] === 0xffff ? null : sog[i] / 10,
      cog: cog[i] === 0xffff ? null : cog[i] / 10,
      heading: heading[i] === 0xffff ? null : heading[i] / 10,
      source: flags[i] & 1 ? 'region' : 'world',
      ship_name: tail.names[m] ?? null,
      ...w,
    } as LatestPosition
  }
  return out
}
