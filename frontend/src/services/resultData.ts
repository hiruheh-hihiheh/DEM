// Loads a processed result package (manifest + binary frames) into typed
// arrays the viewer can render.
//
// frames.bin layout (see manifest.layout): per frame, sequential particles of
// float32 xyz (3 values) followed by float32 speed (1 value) — 16 bytes each.

import { fetchResultBinary, fetchResultManifest } from './api'
import type { ResultManifest } from '../types/simulation'

export interface LoadedResult {
  manifest: ResultManifest
  frames: Float32Array
  terrain: Float32Array | null
}

export const loadResult = async (
  jobId: string,
): Promise<LoadedResult> => {
  const manifest = await fetchResultManifest(jobId)
  const framesBuffer = await fetchResultBinary(jobId, 'frames.bin')
  let terrain: Float32Array | null = null
  if (manifest.files.terrain) {
    try {
      const buffer = await fetchResultBinary(jobId, 'terrain.bin')
      terrain = new Float32Array(buffer)
    } catch {
      terrain = null
    }
  }
  return {
    manifest,
    frames: new Float32Array(framesBuffer),
    terrain,
  }
}

/** Byte offset of a frame inside frames.bin (cumulative particle counts). */
export const frameOffset = (
  manifest: ResultManifest,
  frame: number,
): number => {
  let particles = 0
  for (let i = 0; i < frame; i += 1) {
    particles += manifest.frames.counts[i]
  }
  return particles * manifest.layout.frame_bytes_per_particle
}

/** Slice one frame into positions (np*3) and speeds (np). */
export const sliceFrame = (
  manifest: ResultManifest,
  frames: Float32Array,
  frame: number,
): { pos: Float32Array; speed: Float32Array } => {
  const count = manifest.frames.counts[frame]
  const start = frameOffset(manifest, frame)
  const xyzStart = start / 4
  const speedStart = xyzStart + count * 3
  return {
    pos: frames.subarray(xyzStart, xyzStart + count * 3),
    speed: frames.subarray(speedStart, speedStart + count),
  }
}

/** Time of a frame in seconds (falls back to frame index when unknown). */
export const frameTime = (
  manifest: ResultManifest,
  frame: number,
): number => manifest.frames.times[frame] ?? frame

/** Speed colour ramp (0..1) -> [r,g,b], blue -> cyan -> yellow -> red. */
export const speedColor = (t: number): [number, number, number] => {
  const x = Math.max(0, Math.min(1, t))
  if (x < 0.33) {
    const u = x / 0.33
    return [40 + 60 * u, 90 + 150 * u, 230 - 40 * u]
  }
  if (x < 0.66) {
    const u = (x - 0.33) / 0.33
    return [100 + 155 * u, 240 - 20 * u, 190 - 160 * u]
  }
  const u = (x - 0.66) / 0.34
  return [255, 220 - 150 * u, 30 - 20 * u]
}
