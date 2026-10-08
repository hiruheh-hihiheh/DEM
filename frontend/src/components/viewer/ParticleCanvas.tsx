import React, { useEffect, useMemo, useRef } from 'react'
import { sliceFrame, speedColor } from '../../services/resultData'
import type { ResultManifest } from '../../types/simulation'

/**
 * Lightweight ParaView-style point-cloud viewer rendered with canvas 2D.
 *
 * Camera model: orbit around the domain centre —
 *   yaw   (rad)  horizontal orbit
 *   pitch (rad)  elevation of the camera (0 = horizon, π/2 = top-down)
 *   zoom         scale multiplier around the fitted view
 *   panX/panY    screen-space translation (device px)
 *
 * Terrain is cached to an offscreen canvas (it never changes over time);
 * water particles are re-sorted back-to-front each frame for correct
 * occlusion.
 */

interface Camera {
  yaw: number
  pitch: number
  zoom: number
  panX: number
  panY: number
}

interface Props {
  manifest: ResultManifest
  frames: Float32Array
  terrain: Float32Array | null
  frame: number
  layers: { terrain: boolean; water: boolean; dam: boolean }
  /** Increment to return the camera to its default pose. */
  resetKey: number
}

const FOCAL_RATIO = 0.85 // fraction of the short viewport side the domain spans
const CAM_DIST_RATIO = 1.7 // camera distance as multiple of the domain size

const defaultCamera = (): Camera => ({
  yaw: -0.65,
  pitch: 0.55,
  zoom: 1,
  panX: 0,
  panY: 0,
})

export const ParticleCanvas: React.FC<Props> = ({
  manifest,
  frames,
  terrain,
  frame,
  layers,
  resetKey,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const cameraRef = useRef<Camera>(defaultCamera())

  // Latest props for the long-lived render/listener closure.
  const propsRef = useRef({ manifest, frames, terrain, frame, layers })
  propsRef.current = { manifest, frames, terrain, frame, layers }

  const renderReqRef = useRef(0)
  const requestRenderRef = useRef<() => void>(() => {})

  // ---- scene geometry (fixed per result) ---------------------------------
  const scene = useMemo(() => {
    const fmin = manifest.bounds.fluid_min
    const fmax = manifest.bounds.fluid_max
    let min = [...fmin] as [number, number, number]
    let max = [...fmax] as [number, number, number]

    if (manifest.bounds.terrain) {
      min = [
        Math.min(min[0], manifest.bounds.terrain.min[0]),
        Math.min(min[1], manifest.bounds.terrain.min[1]),
        Math.min(min[2], manifest.bounds.terrain.min[2]),
      ]
      max = [
        Math.max(max[0], manifest.bounds.terrain.max[0]),
        Math.max(max[1], manifest.bounds.terrain.max[1]),
        Math.max(max[2], manifest.bounds.terrain.max[2]),
      ]
    }

    const center: [number, number, number] = [
      (min[0] + max[0]) / 2,
      (min[1] + max[1]) / 2,
      (min[2] + max[2]) / 2,
    ]
    const dx = max[0] - min[0]
    const dy = max[1] - min[1]
    const dz = max[2] - min[2]
    const size = Math.max(Math.hypot(dx, dy), dz, 1e-6)
    const dist = size * CAM_DIST_RATIO
    return { min, max, center, size, dist }
  }, [manifest])

  // ---- terrain elevation colours ------------------------------------------
  const terrainColors = useMemo(() => {
    if (!terrain) return null
    const n = terrain.length / 3
    const colors = new Uint8Array(n * 3)
    let zLo = Infinity
    let zHi = -Infinity
    for (let i = 0; i < terrain.length; i += 3) {
      const z = terrain[i + 2]
      if (z < zLo) zLo = z
      if (z > zHi) zHi = z
    }
    const span = zHi - zLo || 1
    for (let i = 0; i < n; i += 1) {
      const t = (terrain[i * 3 + 2] - zLo) / span
      colors[i * 3] = Math.round(58 + t * 150)
      colors[i * 3 + 1] = Math.round(50 + t * 135)
      colors[i * 3 + 2] = Math.round(40 + t * 115)
    }
    return colors
  }, [terrain])

  // ---- offscreen terrain cache --------------------------------------------
  const terrainCacheRef = useRef<{
    key: string
    canvas: HTMLCanvasElement
  } | null>(null)

  const buildTerrainCache = (
    w: number,
    h: number,
    cam: Camera,
    data: Float32Array,
    colors: Uint8Array,
  ): HTMLCanvasElement => {
    const key = [
      w,
      h,
      cam.yaw.toFixed(3),
      cam.pitch.toFixed(3),
      cam.zoom.toFixed(3),
      cam.panX.toFixed(1),
      cam.panY.toFixed(1),
    ].join('|')

    const cached = terrainCacheRef.current
    if (cached && cached.key === key) return cached.canvas

    const off = document.createElement('canvas')
    off.width = w
    off.height = h
    const octx = off.getContext('2d')
    if (!octx) return off

    const img = octx.createImageData(w, h)
    const buf = img.data

    const { center, dist } = scene
    const focal = (FOCAL_RATIO * Math.min(w, h) * dist) / scene.size
    const F = focal * cam.zoom
    const cy = Math.cos(cam.yaw)
    const sy = Math.sin(cam.yaw)
    const cp = Math.cos(cam.pitch)
    const sp = Math.sin(cam.pitch)
    const ox = w / 2 + cam.panX
    const oy = h / 2 + cam.panY

    const n = data.length / 3
    const depths = new Float32Array(n)
    const order = new Int32Array(n)

    for (let i = 0; i < n; i += 1) {
      const dx = data[i * 3] - center[0]
      const dy = data[i * 3 + 1] - center[1]
      const dz = data[i * 3 + 2] - center[2]
      const y1 = dx * sy + dy * cy
      depths[i] = dist + y1 * cp - dz * sp
      order[i] = i
    }

    // Painter's algorithm — far points first.
    const sorted = Array.from(order).sort((a, b) => depths[b] - depths[a])

    for (const i of sorted) {
      const depth = depths[i]
      if (depth < 1e-3) continue
      const dx = data[i * 3] - center[0]
      const dy = data[i * 3 + 1] - center[1]
      const dz = data[i * 3 + 2] - center[2]
      const x1 = dx * cy - dy * sy
      const y1 = dx * sy + dy * cy
      const y2 = y1 * cp - dz * sp
      const sx = (ox + (x1 * F) / depth) | 0
      const sy2 = (oy - (y2 * F) / depth) | 0
      if (sx < 0 || sy2 < 0 || sx >= w - 1 || sy2 >= h - 1) continue
      const r = colors[i * 3]
      const g = colors[i * 3 + 1]
      const b = colors[i * 3 + 2]
      // 2×2 block keeps the surface gap-free when zoomed out.
      let p = (sy2 * w + sx) * 4
      buf[p] = r
      buf[p + 1] = g
      buf[p + 2] = b
      buf[p + 3] = 255
      p += 4
      buf[p] = r
      buf[p + 1] = g
      buf[p + 2] = b
      buf[p + 3] = 255
      p = ((sy2 + 1) * w + sx) * 4
      buf[p] = r
      buf[p + 1] = g
      buf[p + 2] = b
      buf[p + 3] = 255
      p += 4
      buf[p] = r
      buf[p + 1] = g
      buf[p + 2] = b
      buf[p + 3] = 255
    }

    octx.putImageData(img, 0, 0)
    terrainCacheRef.current = { key, canvas: off }
    return off
  }

  // ---- main render --------------------------------------------------------
  const render = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const { manifest: man, frames: fr, terrain: ter, frame: fi, layers: ly } =
      propsRef.current

    const dpr = window.devicePixelRatio || 1
    const cssW = canvas.clientWidth
    const cssH = canvas.clientHeight
    if (cssW === 0 || cssH === 0) return
    const w = Math.round(cssW * dpr)
    const h = Math.round(cssH * dpr)
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w
      canvas.height = h
      terrainCacheRef.current = null
    }

    const cam = cameraRef.current
    const { center, dist } = scene
    const focal = (FOCAL_RATIO * Math.min(w, h) * dist) / scene.size
    const F = focal * cam.zoom
    const ox = w / 2 + cam.panX
    const oy = h / 2 + cam.panY
    const cy = Math.cos(cam.yaw)
    const sy = Math.sin(cam.yaw)
    const cp = Math.cos(cam.pitch)
    const sp = Math.sin(cam.pitch)

    ctx.clearRect(0, 0, w, h)

    const project = (x: number, y: number, z: number) => {
      const dx = x - center[0]
      const dy = y - center[1]
      const dz = z - center[2]
      const x1 = dx * cy - dy * sy
      const y1 = dx * sy + dy * cy
      const y2 = y1 * cp - dz * sp
      const depth = dist + y1 * cp - dz * sp
      return {
        x: ox + (x1 * F) / depth,
        y: oy - (y2 * F) / depth,
        depth,
        scale: F / depth,
      }
    }

    // 1. terrain (cached offscreen)
    if (ly.terrain && ter && terrainColors) {
      const cached =
        terrainCacheRef.current !== null
          ? terrainCacheRef.current
          : { canvas: buildTerrainCache(w, h, cam, ter, terrainColors) }
      ctx.drawImage(cached.canvas, 0, 0)
    }

    // 2. dam centreline
    if (ly.dam && man.dam?.centerline_endpoints?.length === 2) {
      const [a, b] = man.dam.centerline_endpoints
      const z = man.dam.center_z ?? man.dam.crest_z ?? center[2]
      const pa = project(a[0], a[1], z)
      const pb = project(b[0], b[1], z)
      ctx.save()
      ctx.strokeStyle = '#f59e0b'
      ctx.lineWidth = Math.max(2, 0.35 * (pa.scale + pb.scale) * 0.06)
      ctx.shadowColor = 'rgba(245, 158, 11, 0.8)'
      ctx.shadowBlur = 8
      ctx.beginPath()
      ctx.moveTo(pa.x, pa.y)
      ctx.lineTo(pb.x, pb.y)
      ctx.stroke()
      ctx.restore()
    }

    // 3. water particles, sorted far → near
    if (ly.water && fi < man.frames.count) {
      const { pos, speed } = sliceFrame(man, fr, fi)
      const count = speed.length
      const globalMax = man.speed.global_max || 1

      const px = new Float64Array(count)
      const py = new Float64Array(count)
      const pr = new Float64Array(count)
      const depth = new Float64Array(count)
      const order: number[] = new Array(count)

      const dp = man.metrics.volume_estimate?.dp ?? 0.1
      for (let i = 0; i < count; i += 1) {
        const p = project(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2])
        px[i] = p.x
        py[i] = p.y
        depth[i] = p.depth
        pr[i] = Math.max(1, dp * p.scale * 0.62)
        order[i] = i
      }
      order.sort((a, b) => depth[b] - depth[a])

      ctx.globalAlpha = 0.94
      for (const i of order) {
        const s = speed[i] / globalMax
        const [r, g, b] = speedColor(s)
        ctx.fillStyle = `rgb(${r | 0},${g | 0},${b | 0})`
        const rad = pr[i]
        ctx.fillRect(px[i] - rad, py[i] - rad, rad * 2, rad * 2)
      }
      ctx.globalAlpha = 1
    }
  }

  // ---- wiring -------------------------------------------------------------
  useEffect(() => {
    terrainCacheRef.current = null
    requestRenderRef.current()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifest, terrain])

  useEffect(() => {
    requestRenderRef.current = () => {
      if (renderReqRef.current) return
      renderReqRef.current = requestAnimationFrame(() => {
        renderReqRef.current = 0
        render()
      })
    }
    requestRenderRef.current()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifest, frames, terrain, frame, layers])

  useEffect(() => {
    cameraRef.current = defaultCamera()
    terrainCacheRef.current = null
    requestRenderRef.current()
  }, [resetKey])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    let dragging: 'orbit' | 'pan' | null = null
    let lastX = 0
    let lastY = 0

    const onPointerDown = (e: PointerEvent) => {
      dragging = e.button === 2 || e.shiftKey ? 'pan' : 'orbit'
      lastX = e.clientX
      lastY = e.clientY
      canvas.setPointerCapture(e.pointerId)
    }

    const onPointerMove = (e: PointerEvent) => {
      if (!dragging) return
      const cam = cameraRef.current
      const dx = e.clientX - lastX
      const dy = e.clientY - lastY
      lastX = e.clientX
      lastY = e.clientY
      if (dragging === 'orbit') {
        cam.yaw += dx * 0.008
        cam.pitch = Math.min(1.5, Math.max(0.08, cam.pitch + dy * 0.008))
      } else {
        cam.panX += dx * (window.devicePixelRatio || 1)
        cam.panY += dy * (window.devicePixelRatio || 1)
      }
      requestRenderRef.current()
    }

    const onPointerUp = (e: PointerEvent) => {
      dragging = null
      try {
        canvas.releasePointerCapture(e.pointerId)
      } catch {
        /* pointer already released */
      }
    }

    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const cam = cameraRef.current
      cam.zoom = Math.min(30, Math.max(0.15, cam.zoom * Math.exp(-e.deltaY * 0.0012)))
      requestRenderRef.current()
    }

    const onContext = (e: Event) => e.preventDefault()

    canvas.addEventListener('pointerdown', onPointerDown)
    canvas.addEventListener('pointermove', onPointerMove)
    canvas.addEventListener('pointerup', onPointerUp)
    canvas.addEventListener('wheel', onWheel, { passive: false })
    canvas.addEventListener('contextmenu', onContext)

    const observer = new ResizeObserver(() => requestRenderRef.current())
    observer.observe(canvas)

    requestRenderRef.current()

    return () => {
      canvas.removeEventListener('pointerdown', onPointerDown)
      canvas.removeEventListener('pointermove', onPointerMove)
      canvas.removeEventListener('pointerup', onPointerUp)
      canvas.removeEventListener('wheel', onWheel)
      canvas.removeEventListener('contextmenu', onContext)
      observer.disconnect()
      if (renderReqRef.current) cancelAnimationFrame(renderReqRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <canvas ref={canvasRef} className="viewer-3d__canvas" />
}
