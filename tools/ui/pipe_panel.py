"""Pipe panel for the FPGA screen: Blender render of the cooling line, two passes.

    Blender -b -P tools/ui/pipe_panel.py -- --out build/ui --w 1552 --h 220 [--samples 64]

Geometry and materials come from pipe_model_snapshot.py (the modelling session's
script, unchanged). Only the stage differs: dark studio, orthographic camera.
The coolant is rendered twice as a pure emitter, black (pass "a") and white
(pass "b"); everything else is identical, so per pixel
    a = what the screen shows when the water is dark
    b - a = how much water light reaches the camera (through the acrylic tube)
and the PL recolors the water as a + (b - a) * colormap[T(x)] at 60 Hz.
Also writes panel.json: image size, px per metre, valve and segment x in pixels.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "pipe_model_snapshot.py"), encoding="utf-8").read()
src = src[:src.rstrip().rfind("main()")]                     # library only, no render
exec(compile(src, "pipe_model_snapshot.py", "exec"))         # noqa: S102 (our own snapshot)


def water_emitter(name, value):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (value, value, value, 1)
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


def stage(xs, w, h, samples):
    sc = bpy.context.scene
    sc.world = bpy.data.worlds.new("World")
    sc.world.use_nodes = True
    bg = sc.world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.03, 0.04, 0.06, 1)
    bg.inputs[1].default_value = 0.35
    add_area("key", (-0.6, -1.0, 0.9), (0, 0, 0), 1.2, 60)
    add_area("fill", (1.0, -0.9, 0.1), (0, 0, 0), 1.4, 18, (0.85, 0.92, 1.0))
    add_area("rim", (0, 0.8, 0.6), (0, 0, 0), 2.5, 30, (0.7, 0.85, 1.0))
    cam = bpy.data.objects.new("cam_panel", bpy.data.cameras.new("cam_panel"))
    cam.data.type = 'ORTHO'
    span = (xs[-1] - xs[0]) + 2 * (P["half_len"] + P["adapter_len"] + P["stub_len"]) + 0.10
    cam.data.ortho_scale = span
    tilt = math.radians(14)
    tgt = Vector((0.0, 0.0, -0.012))
    cam.location = tgt + Vector((0, -math.cos(tilt), math.sin(tilt))) * 2.0
    cam.rotation_euler = (tgt - cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam.data.clip_start, cam.data.clip_end = 0.01, 10
    sc.collection.objects.link(cam)
    sc.camera = cam
    sc.render.engine = 'CYCLES'
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'METAL'
        prefs.get_devices()
        for d in prefs.devices:
            d.use = True
        sc.cycles.device = 'GPU'
    except Exception as e:
        print("GPU unavailable:", e)
    sc.cycles.samples, sc.cycles.use_denoising = samples, True
    sc.cycles.seed = 7                                            # same noise in both passes
    sc.cycles.max_bounces, sc.cycles.transmission_bounces, sc.cycles.transparent_max_bounces = 16, 16, 16
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
    sc.render.film_transparent = True                            # composited over the UI panel colour
    sc.view_settings.view_transform = 'Standard'                 # linear-in-display: a/b mix stays valid
    sc.render.image_settings.file_format = 'PNG'
    sc.render.image_settings.color_depth = '16'
    sc.render.image_settings.color_mode = 'RGBA'
    return cam, span


def main():
    a = sys.argv[sys.argv.index("--") + 1:]
    opt = dict(out="build/ui", w=1552, h=220, samples=64)
    for k, v in zip(a[0::2], a[1::2]):
        opt[k.lstrip("-")] = type(opt[k.lstrip("-")])(v)
    os.makedirs(opt["out"], exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    M = make_materials()
    coll = bpy.data.collections.new("Pipe")
    bpy.context.scene.collection.children.link(coll)
    xs, coolants = build_pipe(32, M, coll)
    cam, span = stage(xs, opt["w"], opt["h"], opt["samples"])
    ppm = opt["w"] / span                                         # ortho: x_px = w/2 + x * ppm
    seg = [[round(opt["w"] / 2 + ob.location.x * ppm, 1),
            round(opt["w"] / 2 + (ob.location.x + ob.data["length"]) * ppm, 1)] for ob in coolants]
    info = dict(w=opt["w"], h=opt["h"], px_per_m=ppm, valves_px=[round(opt["w"] / 2 + x * ppm, 1) for x in xs],
                segments_px=seg, source="pipe_model_snapshot.py")
    json.dump(info, open(os.path.join(opt["out"], "panel.json"), "w"), indent=1)
    for tag, val in (("a", 0.0), ("b", 1.0)):
        mat = water_emitter("water_" + tag, val)
        for ob in coolants:
            ob.data.materials.clear()
            ob.data.materials.append(mat)
        bpy.context.scene.render.filepath = os.path.join(opt["out"], "panel_%s.png" % tag)
        bpy.ops.render.render(write_still=True)
        print("rendered", bpy.context.scene.render.filepath)


main()
