"use client";

/**
 * The orb, plus the inputs that move it.
 *
 * The level comes from `getOutputByteFrequencyData()` — the WebRTC analyser in
 * this tab — read once per frame. That is the whole reason the voice moved into
 * the browser: the old console polled a Python meter once a second, and an orb
 * that updates at 1fps does not breathe with anybody.
 */

import { useEffect, useRef, useState } from "react";

import { GESTURES_ENABLED, startHandTracking, type HandTracker } from "@/lib/handTracker";
import { createOrbScene, type OrbHandle, type OrbMode } from "@/lib/orbScene";

type Props = {
  mode: OrbMode;
  /** Returns the current output spectrum, or null when no session is live. */
  getFrequencies: () => Uint8Array | null;
};

/** Mean of the low half of the spectrum: where a human voice actually lives. */
function levelFrom(bytes: Uint8Array | null): number {
  if (!bytes || bytes.length === 0) return 0;
  const half = Math.max(1, Math.floor(bytes.length / 2));
  let total = 0;
  for (let i = 0; i < half; i++) total += bytes[i];
  return total / half / 255;
}

export default function JobvisOrb({ mode, getFrequencies }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const orbRef = useRef<OrbHandle | null>(null);
  const [gesturesOn, setGesturesOn] = useState(false);
  const [gestureStarting, setGestureStarting] = useState(false);
  const [gestureError, setGestureError] = useState("");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const orb = createOrbScene(canvas);
    orbRef.current = orb;

    let raf = 0;
    const pump = () => {
      orb.setLevel(levelFrom(getFrequencies()));
      raf = requestAnimationFrame(pump);
    };
    raf = requestAnimationFrame(pump);

    return () => {
      cancelAnimationFrame(raf);
      orb.dispose();
      orbRef.current = null;
    };
  }, [getFrequencies]);

  useEffect(() => {
    orbRef.current?.setMode(mode);
  }, [mode]);

  // Mouse drag, wheel, and the keyboard shortcuts.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;

    const down = (event: PointerEvent) => {
      dragging = true;
      lastX = event.clientX;
      lastY = event.clientY;
      canvas.setPointerCapture(event.pointerId);
    };
    const move = (event: PointerEvent) => {
      if (!dragging) return;
      orbRef.current?.spinBy((event.clientX - lastX) * 0.0018, (event.clientY - lastY) * 0.0018);
      lastX = event.clientX;
      lastY = event.clientY;
    };
    const up = () => {
      dragging = false;
    };
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      orbRef.current?.zoomBy(event.deltaY > 0 ? 1.06 : 0.94);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "r" || event.key === "R") orbRef.current?.reset();
      if (event.key === "+" || event.key === "=") orbRef.current?.zoomBy(0.9);
      if (event.key === "-" || event.key === "_") orbRef.current?.zoomBy(1.1);
      if ((event.key === "g" || event.key === "G") && GESTURES_ENABLED) setGesturesOn((on) => !on);
    };

    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("wheel", wheel, { passive: false });
    window.addEventListener("keydown", key);
    return () => {
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("wheel", wheel);
      window.removeEventListener("keydown", key);
    };
  }, []);

  // Gestures: the webcam only opens after this flips on, and closes when it flips off.
  useEffect(() => {
    if (!gesturesOn || !videoRef.current) return;
    let tracker: HandTracker | null = null;
    let cancelled = false;

    // Nothing is live until getUserMedia resolves, and it sits pending for as
    // long as Chrome's permission bubble is open. Say "starting" until then —
    // claiming the camera is on while the browser is still asking is a lie.
    setGestureStarting(true);
    startHandTracking(videoRef.current, {
      spinBy: (dx, dy) => orbRef.current?.spinBy(dx, dy),
      zoomBy: (factor) => orbRef.current?.zoomBy(factor),
    })
      .then((started) => {
        if (cancelled) started.stop();
        else tracker = started;
      })
      .catch((error: Error) => {
        setGestureError(error.message || "hand tracking unavailable");
        setGesturesOn(false);
      })
      .finally(() => setGestureStarting(false));

    return () => {
      cancelled = true;
      setGestureStarting(false);
      tracker?.stop();
    };
  }, [gesturesOn]);

  const gestureLabel = gestureStarting
    ? "starting camera…"
    : gesturesOn
      ? "camera on — pinch to spin (G)"
      : "enable hand control (G)";

  return (
    <div className="orb-wrap">
      <canvas ref={canvasRef} className="orb-canvas" aria-label="Jobvis" />
      {GESTURES_ENABLED && (
        <div className="orb-controls">
          <button type="button" className="ghost" onClick={() => setGesturesOn((on) => !on)}>
            {gestureLabel}
          </button>
          {gestureError && <span className="orb-error">{gestureError}</span>}
        </div>
      )}
      <video ref={videoRef} className="orb-video" playsInline muted />
    </div>
  );
}
