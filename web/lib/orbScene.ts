/**
 * The Jobvis orb: a holographic core in layered shells, drawn with Three.js.
 *
 * Five pieces, each doing one job:
 *   core    a small icosahedron that brightens and swells with the voice
 *   spiral  a helix threaded through the core, always turning
 *   shells  three counter-rotating wireframes, the "hologram" read
 *   debris  points orbiting on a wide shell, so the space has depth
 *   rings   thin tori that sweep, so the thing looks like it is scanning
 *
 * Then bloom and a whisper of chromatic aberration on top, which is what makes
 * it read as light rather than as geometry.
 *
 * The scene owns no state about the conversation. It takes a level (0-1) and a
 * mode, and the caller feeds those from the WebRTC audio analyser at whatever
 * rate it likes — here, once per frame.
 */

import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

export type OrbMode = "idle" | "connecting" | "listening" | "speaking" | "error";

/** Ice azure. The wizard stays green; blue means you are talking to Jobvis. */
export const PALETTE = {
  core: 0xbfe6ff,
  shells: 0x3aa9ff,
  rings: 0x1b7fd4,
  falloff: 0x072a52,
  backdrop: 0x060b12,
  error: 0xb45309,
};

/** A cheap chromatic aberration: split the channels radially, strongest at the edge. */
const ChromaticAberrationShader = {
  uniforms: {
    tDiffuse: { value: null as THREE.Texture | null },
    amount: { value: 0.0016 },
  },
  vertexShader: `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    uniform sampler2D tDiffuse;
    uniform float amount;
    varying vec2 vUv;
    void main() {
      vec2 toCenter = vUv - vec2(0.5);
      float falloff = dot(toCenter, toCenter) * 2.0;
      vec2 offset = toCenter * amount * falloff;
      float r = texture2D(tDiffuse, vUv + offset).r;
      vec4 g = texture2D(tDiffuse, vUv);
      float b = texture2D(tDiffuse, vUv - offset).b;
      gl_FragColor = vec4(r, g.g, b, g.a);
    }
  `,
};

export type OrbHandle = {
  setLevel: (level: number) => void;
  setMode: (mode: OrbMode) => void;
  /** Nudge the spin, in radians. Mouse drag and pinch gestures both land here. */
  spinBy: (dx: number, dy: number) => void;
  /** Multiply the camera distance. Scroll and two-hand pinch both land here. */
  zoomBy: (factor: number) => void;
  reset: () => void;
  dispose: () => void;
};

const MIN_DISTANCE = 3.0;
const MAX_DISTANCE = 9.0;

export function createOrbScene(canvas: HTMLCanvasElement): OrbHandle {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(PALETTE.backdrop, 0.085);

  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  let distance = 5.2;
  camera.position.set(0, 0, distance);

  const group = new THREE.Group();
  scene.add(group);

  // --- core -----------------------------------------------------------------
  const coreMaterial = new THREE.MeshBasicMaterial({
    color: PALETTE.core,
    transparent: true,
    opacity: 0.85,
    blending: THREE.AdditiveBlending,
  });
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(0.42, 3), coreMaterial);
  group.add(core);

  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(0.72, 32, 32),
    new THREE.MeshBasicMaterial({
      color: PALETTE.shells,
      transparent: true,
      opacity: 0.12,
      blending: THREE.AdditiveBlending,
      side: THREE.BackSide,
    }),
  );
  group.add(halo);

  // --- spiral ---------------------------------------------------------------
  const spiralPoints: THREE.Vector3[] = [];
  for (let i = 0; i <= 260; i++) {
    const t = i / 260;
    const angle = t * Math.PI * 12;
    const radius = 0.16 + t * 0.5;
    spiralPoints.push(new THREE.Vector3(Math.cos(angle) * radius, (t - 0.5) * 1.25, Math.sin(angle) * radius));
  }
  const spiral = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(spiralPoints),
    new THREE.LineBasicMaterial({ color: PALETTE.core, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending }),
  );
  group.add(spiral);

  // --- shells ---------------------------------------------------------------
  const shellSpecs = [
    { radius: 1.15, detail: 1, opacity: 0.5, speed: 0.16 },
    { radius: 1.5, detail: 2, opacity: 0.26, speed: -0.1 },
    { radius: 1.95, detail: 1, opacity: 0.14, speed: 0.06 },
  ];
  const shells = shellSpecs.map((spec) => {
    const mesh = new THREE.Mesh(
      new THREE.IcosahedronGeometry(spec.radius, spec.detail),
      new THREE.MeshBasicMaterial({
        color: PALETTE.shells,
        wireframe: true,
        transparent: true,
        opacity: spec.opacity,
        blending: THREE.AdditiveBlending,
      }),
    );
    group.add(mesh);
    return { mesh, speed: spec.speed, baseOpacity: spec.opacity, baseScale: 1 };
  });

  // --- debris ---------------------------------------------------------------
  const debrisCount = 520;
  const positions = new Float32Array(debrisCount * 3);
  for (let i = 0; i < debrisCount; i++) {
    // A shell rather than a ball: an even spread on the sphere, jittered outward.
    const theta = Math.acos(2 * ((i + 0.5) / debrisCount) - 1);
    const phi = i * 2.39996; // golden angle, so the points never band
    const radius = 2.25 + (i % 7) * 0.09;
    positions[i * 3] = Math.sin(theta) * Math.cos(phi) * radius;
    positions[i * 3 + 1] = Math.cos(theta) * radius;
    positions[i * 3 + 2] = Math.sin(theta) * Math.sin(phi) * radius;
  }
  const debrisGeometry = new THREE.BufferGeometry();
  debrisGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const debris = new THREE.Points(
    debrisGeometry,
    new THREE.PointsMaterial({
      color: PALETTE.rings,
      size: 0.028,
      transparent: true,
      opacity: 0.7,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    }),
  );
  group.add(debris);

  // --- scan rings -----------------------------------------------------------
  const rings = [0, 1, 2].map((i) => {
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(1.7 + i * 0.22, 0.005, 8, 160),
      new THREE.MeshBasicMaterial({
        color: i === 1 ? PALETTE.core : PALETTE.rings,
        transparent: true,
        opacity: 0.38,
        blending: THREE.AdditiveBlending,
      }),
    );
    ring.rotation.x = Math.PI / 2 + i * 0.4;
    ring.rotation.z = i * 0.7;
    group.add(ring);
    return ring;
  });

  // --- post -----------------------------------------------------------------
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.9, 0.6, 0.12);
  composer.addPass(bloom);
  const chroma = new ShaderPass(ChromaticAberrationShader);
  composer.addPass(chroma);

  // --- state ----------------------------------------------------------------
  let level = 0;
  let smoothed = 0;
  let mode: OrbMode = "idle";
  let spinX = 0;
  let spinY = 0;
  let momentumX = 0;
  let momentumY = 0;
  let disposed = false;
  // Timer, not Clock: Clock is deprecated, and Timer's Page Visibility support
  // means a backgrounded tab does not return with one enormous delta.
  const timer = new THREE.Timer();
  timer.connect(document);

  function resize() {
    const width = canvas.clientWidth || 1;
    const height = canvas.clientHeight || 1;
    if (canvas.width === width && canvas.height === height) return;
    renderer.setSize(width, height, false);
    composer.setSize(width, height);
    bloom.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }

  function tint(color: number) {
    coreMaterial.color.setHex(color === PALETTE.error ? PALETTE.error : PALETTE.core);
    for (const shell of shells) (shell.mesh.material as THREE.MeshBasicMaterial).color.setHex(color);
  }

  function frame() {
    if (disposed) return;
    resize();
    timer.update();
    const delta = Math.min(timer.getDelta(), 0.1);
    const time = timer.getElapsed();

    // Ease toward the measured level so a dropped frame never makes it stutter.
    smoothed += (level - smoothed) * Math.min(1, delta * 9);
    const idle = mode === "idle" || mode === "connecting";
    const breath = idle ? 0.5 + Math.sin(time * 1.4) * 0.5 : 1;
    const energy = idle ? 0.12 * breath : 0.25 + smoothed * 1.35;

    core.scale.setScalar(1 + energy * 0.35);
    core.rotation.y += delta * (0.35 + smoothed * 2.2);
    coreMaterial.opacity = 0.45 + energy * 0.5;
    halo.scale.setScalar(1 + energy * 0.5);
    (halo.material as THREE.MeshBasicMaterial).opacity = 0.06 + energy * 0.22;

    spiral.rotation.y -= delta * (0.6 + smoothed * 2.6);
    (spiral.material as THREE.LineBasicMaterial).opacity = 0.28 + energy * 0.5;

    for (const shell of shells) {
      shell.mesh.rotation.y += delta * shell.speed;
      shell.mesh.rotation.x += delta * shell.speed * 0.45;
      shell.mesh.scale.setScalar(1 + smoothed * 0.09);
      (shell.mesh.material as THREE.MeshBasicMaterial).opacity = shell.baseOpacity * (0.65 + energy * 0.9);
    }

    debris.rotation.y += delta * 0.045;
    debris.rotation.x = Math.sin(time * 0.12) * 0.12;
    (debris.material as THREE.PointsMaterial).opacity = 0.4 + energy * 0.45;

    rings.forEach((ring, i) => {
      ring.rotation.z += delta * (0.22 + i * 0.09) * (i % 2 === 0 ? 1 : -1);
      // The sweep: each ring rises and falls out of phase with the others.
      ring.position.y = Math.sin(time * 0.55 + i * 2.1) * 0.85;
      (ring.material as THREE.MeshBasicMaterial).opacity = 0.16 + energy * 0.45;
    });

    // Drag momentum, then a slow drift so the orb is never quite still.
    spinY += momentumY;
    spinX += momentumX;
    momentumY *= 0.92;
    momentumX *= 0.92;
    group.rotation.y = spinY + time * 0.05;
    group.rotation.x = THREE.MathUtils.clamp(spinX, -0.9, 0.9);

    bloom.strength = 0.65 + energy * 0.9;
    chroma.uniforms.amount.value = 0.0012 + smoothed * 0.004;

    camera.position.z += (distance - camera.position.z) * Math.min(1, delta * 6);
    composer.render();
    requestAnimationFrame(frame);
  }

  resize();
  requestAnimationFrame(frame);

  return {
    setLevel: (value: number) => {
      level = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
    },
    setMode: (next: OrbMode) => {
      mode = next;
      tint(next === "error" ? PALETTE.error : PALETTE.shells);
    },
    spinBy: (dx: number, dy: number) => {
      momentumY += dx;
      momentumX += dy;
    },
    zoomBy: (factor: number) => {
      distance = THREE.MathUtils.clamp(distance * factor, MIN_DISTANCE, MAX_DISTANCE);
    },
    reset: () => {
      spinX = spinY = momentumX = momentumY = 0;
      distance = 5.2;
    },
    dispose: () => {
      disposed = true;
      timer.dispose();
      composer.dispose();
      renderer.dispose();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.Points || object instanceof THREE.Line) {
          object.geometry.dispose();
          const material = object.material;
          if (Array.isArray(material)) material.forEach((m) => m.dispose());
          else material.dispose();
        }
      });
    },
  };
}
