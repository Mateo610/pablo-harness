# M9 · Specialist lanes: image and 3D first

**Goal.** The `media` lane turns a request into a job on the right worker and returns files as
evidence (image previews, mesh files, a Blender render).

**Steps**
1. `src/pablo/media.py`: a dispatcher on 127.0.0.1:8005 that speaks the OpenAI chat shape to the
   harness but internally routes: image → Qwen-Image (or best current open image model; check
   license), 3D → TRELLIS.2 → Blender (headless, via a script or Blender MCP) for a turntable render.
2. Media jobs are long and VRAM-hungry: they go through the M4 swapper and default to the overnight
   queue unless I mark them "now".
3. Results land in `~/.pablo/artifacts/<run-id>/`; the run's verdict lists them so the cockpit can
   show thumbnails (UI is core track).
4. Video (LTX/Wan) and motion (Kimodo/HY-Motion) are follow-ups; stub them with clear errors.

**Acceptance**
- "Render a 3D planter housing concept" produces a mesh + render; "make a logo sketch" produces images.
- Media traces are `train_ok: false`. BUILD_PLAN M9 done.
