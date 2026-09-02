/**
 * Three.js STL viewer: orbit, zoom, and a light that makes terraces readable.
 *
 * The mesh is what sells the result — the whole point of showing it before the
 * download. Everything here is disposed on unmount: a WebGL context left behind
 * per generation exhausts the browser's context pool after a handful of runs.
 */
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

interface Props {
  url: string;
}

export default function ModelViewer({ url }: Props) {
  const mount = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    const host = mount.current;
    if (!host) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x111214);

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 5000);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    host.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    // Lighting is not decoration here: the terraces are 0.2mm steps on a 180mm
    // panel, so they are visible only through the shading difference between a
    // flat top and its vertical riser. That needs a strongly directional key
    // coming from the side and very little ambient — a soft, frontal setup
    // washes the relief out into a blank rectangle.
    scene.add(new THREE.AmbientLight(0xffffff, 0.22));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(-0.85, 0.9, 0.5);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x8ba6d8, 0.45);
    fill.position.set(1.2, -0.6, 0.8);
    scene.add(fill);

    let mesh: THREE.Mesh | null = null;
    let frame = 0;
    let disposed = false;

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = host;
      if (!w || !h) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);

    new STLLoader().load(
      url,
      (geometry) => {
        if (disposed) {
          geometry.dispose();
          return;
        }
        geometry.computeVertexNormals();
        geometry.center();

        const material = new THREE.MeshStandardMaterial({
          color: 0xcccccc,
          roughness: 0.85,
          metalness: 0.02,
          flatShading: true,
        });
        mesh = new THREE.Mesh(geometry, material);
        scene.add(mesh);

        // The engine builds the panel in XY with the relief rising along +Z, so
        // the artwork already faces the camera down -Z. Keep it upright and
        // tilt slightly off-axis: dead-on hides the terraces, a flat-on-the-floor
        // view foreshortens the whole panel into a trapezium.
        const size = new THREE.Box3().setFromObject(mesh).getSize(new THREE.Vector3());
        const span = Math.max(size.x, size.y, size.z);
        camera.position.set(span * 0.22, span * 0.2, span * 1.35);
        camera.near = span / 100;
        camera.far = span * 20;
        controls.target.set(0, 0, 0);
        resize();
        setState("ready");
      },
      undefined,
      (err) => {
        setError(err instanceof Error ? err.message : "could not load the model");
        setState("error");
      },
    );

    const animate = () => {
      frame = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      if (mesh) {
        mesh.geometry.dispose();
        (mesh.material as THREE.Material).dispose();
      }
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [url]);

  return (
    <div className="viewer">
      <div ref={mount} className="viewer-canvas" />
      {state === "loading" && <div className="viewer-overlay">Loading model…</div>}
      {state === "error" && <div className="viewer-overlay error">{error}</div>}
      {state === "ready" && <div className="viewer-hint">drag to orbit · scroll to zoom</div>}
    </div>
  );
}
