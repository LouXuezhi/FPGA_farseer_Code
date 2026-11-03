# Parametric model of the cooling line (Blender). tools/ui/pipe_panel.py runs its
# geometry / material code for the screen panel.
"""
液冷管路参数化模型（Blender 5.x 后台运行）

只建管路本体：透明管段、10 个不锈钢三通球阀（细化）、生料带接头、下方支撑杆、两端弯头。
加热片 / 测温探头 / 线缆不建。

用法:
  Blender -b -P pipe_model.py -- --out DIR [--mode real|heat] [--heat values.json]
                                   [--segs 32] [--samples 128] [--no-render]
  --mode heat 时按管段数值着色（values.json: {"values": [...], "vmin":..., "vmax":...}），
  不给文件则用演示数据。
"""
import argparse
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

# ---------------- 几何参数（米），按视频帧比例估计 ----------------
P = dict(
    n_valves=10,
    pitch=0.165,          # 阀门中心距
    tube_od=0.027,        # 透明管外径
    tube_id=0.021,
    adapter_r=0.0150,     # 生料带接头半径
    adapter_len=0.012,
    half_len=0.047,       # 阀体中心到端口端面
    rod_r=0.0065,          # 下方不锈钢支撑杆
    rod_y=0.006,
    rod_z=-0.032,
    handle_yaw=225.0,     # 手柄朝向（俯视，度；225 = 朝左前方）
    handle_pitch=6.0,
    stub_len=0.07,        # 两端伸出管段
)

ROT_Z_TO_X = Matrix.Rotation(math.pi / 2, 4, 'Y')    # 局部 Z -> 世界 +X
ROT_Z_TO_NY = Matrix.Rotation(math.pi / 2, 4, 'X')   # 局部 Z -> 世界 -Y（朝镜头）


# ---------------- 网格工具 ----------------
def lathe(bm, profile, segs, mat=0, xf=Matrix(), closed=False, phase=0.0):
    """把 (r, h[, hex]) 轮廓绕局部 Z 轴旋转。
    hex=True 的环按外接圆半径 r 压成正六边形（segs 需为 6 的倍数，便于与圆孔共用拓扑）；
    r=0 收成单顶点；closed=True 首尾相连；mat 可为 int 或逐段列表。"""
    rings = []
    for p in profile:
        r, h = p[0], p[1]
        hexa = len(p) > 2 and p[2]
        if r < 1e-9:
            rings.append([bm.verts.new(xf @ Vector((0.0, 0.0, h)))])
            continue
        ring = []
        for k in range(segs):
            a = phase + 2 * math.pi * k / segs
            rr = r * math.cos(math.pi / 6) / math.cos((a % (math.pi / 3)) - math.pi / 6) if hexa else r
            ring.append(bm.verts.new(xf @ Vector((rr * math.cos(a), rr * math.sin(a), h))))
        rings.append(ring)
    pairs = list(zip(rings[:-1], rings[1:]))
    if closed:
        pairs.append((rings[-1], rings[0]))
    for i, (a, b) in enumerate(pairs):
        m = mat[min(i, len(mat) - 1)] if isinstance(mat, (list, tuple)) else mat
        if len(a) == 1 and len(b) == 1:
            continue
        for k in range(segs):
            k2 = (k + 1) % segs
            if len(a) == 1:
                vs = (a[0], b[k], b[k2])
            elif len(b) == 1:
                vs = (a[k], a[k2], b[0])
            else:
                vs = (a[k], a[k2], b[k2], b[k])
            bm.faces.new(vs).material_index = m


def rbox(bm, size, mat, xf=Matrix(), bevel=0.0, bsegs=2):
    """倒角长方体，size=(x, y, z)，中心在原点后再用 xf 变换。"""
    tmp = bmesh.new()
    bmesh.ops.create_cube(tmp, size=1.0)
    bmesh.ops.scale(tmp, vec=Vector(size), verts=tmp.verts)
    if bevel > 0:
        bmesh.ops.bevel(tmp, geom=list(tmp.edges), offset=bevel, segments=bsegs,
                        profile=0.5, affect='EDGES')
    bmesh.ops.transform(tmp, matrix=xf, verts=tmp.verts)
    for f in tmp.faces:
        f.material_index = mat
    merge(bm, tmp)


def sweep_arc(bm, r, R, turn, segs, steps, mat, xf=Matrix()):
    """沿 +X 起步、向 turn 方向弯 90° 的圆管弯头（实心，两端封口）。"""
    turn = Vector(turn).normalized()
    side = Vector((1, 0, 0)).cross(turn)
    rings = []
    for i in range(steps + 1):
        th = (math.pi / 2) * i / steps
        c = Vector((R * math.sin(th), 0, 0)) + turn * (R - R * math.cos(th))
        n1 = turn * math.cos(th) - Vector((1, 0, 0)) * math.sin(th)
        ring = [bm.verts.new(xf @ (c + r * (math.cos(2 * math.pi * k / segs) * n1 +
                                            math.sin(2 * math.pi * k / segs) * side)))
                for k in range(segs)]
        rings.append(ring)
    for a, b in zip(rings[:-1], rings[1:]):
        for k in range(segs):
            k2 = (k + 1) % segs
            bm.faces.new((a[k], a[k2], b[k2], b[k])).material_index = mat
    for ring in (rings[0], rings[-1]):
        bm.faces.new(ring).material_index = mat


def merge(dst, src):
    """把 src bmesh 追加进 dst（经由临时 Mesh），然后释放 src。"""
    tmp = bpy.data.meshes.new("_tmp")
    src.to_mesh(tmp)
    src.free()
    dst.from_mesh(tmp)
    bpy.data.meshes.remove(tmp)


def finish_mesh(name, bm, mats):
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    me.shade_smooth()
    me.set_sharp_from_angle(angle=math.radians(35))
    return me


def add_obj(name, me, loc=(0, 0, 0), coll=None):
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


# ---------------- 材质 ----------------
def principled(name, color, metallic=0.0, rough=0.5, transmission=0.0, ior=1.45,
               alpha=1.0, coat=0.0, emission=None, emit_strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    vals = {"Base Color": (*color, 1.0), "Metallic": metallic, "Roughness": rough,
            "Transmission Weight": transmission, "IOR": ior, "Alpha": alpha,
            "Coat Weight": coat}
    if emission is not None:
        vals["Emission Color"] = (*emission, 1.0)
        vals["Emission Strength"] = emit_strength
    for k, v in vals.items():
        if k in b.inputs:
            b.inputs[k].default_value = v
    m.diffuse_color = (*color, alpha)
    return m


def make_materials():
    return dict(
        cast=principled("SS_cast", (0.62, 0.62, 0.60), metallic=1.0, rough=0.32),
        bright=principled("SS_polished", (0.80, 0.80, 0.78), metallic=1.0, rough=0.14),
        bore=principled("SS_bore", (0.45, 0.45, 0.44), metallic=1.0, rough=0.40),
        blue=principled("Vinyl_blue", (0.006, 0.035, 0.40), rough=0.35, coat=0.3),
        ptfe=principled("PTFE_tape", (0.92, 0.92, 0.90), rough=0.75),
        pvc=principled("PVC_white", (0.88, 0.88, 0.86), rough=0.45),
        tube=principled("Acrylic_clear", (1.0, 1.0, 1.0), rough=0.03, transmission=1.0, ior=1.49),
        water=principled("Coolant", (0.80, 0.92, 1.0), rough=0.02, transmission=1.0, ior=1.33),
        wall=principled("Wall", (0.55, 0.56, 0.55), rough=0.85),
    )


# 色标取自参考图（ParaView 色条逐像素采样，sRGB 0-255）：深蓝 → 蓝 → 浅青 → 浅黄 → 橙 → 深红
HEAT_STOPS_SRGB = [
    (0.00, (31, 36, 138)), (0.08, (37, 59, 160)), (0.16, (38, 110, 194)), (0.25, (33, 162, 228)),
    (0.33, (66, 203, 245)), (0.41, (142, 228, 245)), (0.49, (214, 243, 199)), (0.53, (235, 234, 170)),
    (0.61, (250, 199, 107)), (0.70, (253, 164, 70)), (0.78, (233, 122, 41)), (0.86, (207, 79, 14)),
    (0.94, (186, 43, 29)), (1.00, (176, 19, 30)),
]


def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def heat_color(t, linear=True):
    """0..1 -> 参考图色标；默认返回线性 RGB（Blender 着色用）。"""
    t = min(max(t, 0.0), 1.0)
    for (t0, c0), (t1, c1) in zip(HEAT_STOPS_SRGB[:-1], HEAT_STOPS_SRGB[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0)
            c = [a + (b - a) * u for a, b in zip(c0, c1)]
            return tuple(srgb_to_linear(v) for v in c) if linear else tuple(v / 255 for v in c)
    return heat_color(1.0, linear)


# ---------------- 阀门（局部：管轴 X，上 Z，正面 -Y） ----------------
ROT_Z_TO_NX = Matrix.Rotation(-math.pi / 2, 4, 'Y')


def build_valve(segs, M):
    bm = bmesh.new()
    s6 = max(12, segs // 6 * 6)
    CAST, BRIGHT, BLUE, BORE = 0, 1, 2, 3
    # 阀体：倒角方块 + 里面的球（从端口能看到）
    rbox(bm, (0.050, 0.046, 0.044), CAST, bevel=0.009, bsegs=3)
    R = 0.0165
    lathe(bm, [(R * math.sin(math.pi * i / 12), -R * math.cos(math.pi * i / 12)) for i in range(13)],
          s6, BRIGHT)
    # ±X 端口：圆颈 + 六角头 + 内孔
    port_x = [(0.0165, 0.020), (0.0165, 0.031), (0.0185, 0.032, True), (0.0185, 0.046, True),
              (0.0175, 0.047, True), (0.0128, 0.047), (0.0128, 0.026), (0.0100, 0.020)]
    for xf in (ROT_Z_TO_X, ROT_Z_TO_NX):
        lathe(bm, port_x, s6, [CAST] * 5 + [BORE] * 3, xf=xf, closed=True)
    # 正面第三通口（-Y），内孔带假螺纹
    port_y = [(0.0150, 0.016), (0.0150, 0.027), (0.0175, 0.028, True), (0.0175, 0.036, True),
              (0.0165, 0.037, True), (0.0115, 0.037), (0.0108, 0.0345), (0.0115, 0.032),
              (0.0108, 0.0295), (0.0115, 0.027), (0.0100, 0.020)]
    lathe(bm, port_y, s6, [CAST] * 5 + [BORE] * 6, xf=ROT_Z_TO_NY, closed=True)
    # 顶部：安装平台、填料压盖、限位板
    rbox(bm, (0.040, 0.034, 0.006), CAST, xf=Matrix.Translation((0, 0, 0.025)), bevel=0.002)
    lathe(bm, [(0, 0.027), (0.0085, 0.027), (0.0085, 0.035), (0.0105, 0.036, True),
               (0.0105, 0.041, True), (0, 0.041)], s6, CAST)
    rbox(bm, (0.030, 0.024, 0.0018), BRIGHT, xf=Matrix.Translation((0, 0, 0.0420)), bevel=0.0005)
    # 手柄：钢片 + 蓝色胶套
    hx = (Matrix.Translation((0, 0, 0.0445)) @ Matrix.Rotation(math.radians(P["handle_yaw"]), 4, 'Z')
          @ Matrix.Rotation(-math.radians(P["handle_pitch"]), 4, 'Y'))
    rbox(bm, (0.135, 0.016, 0.003), BRIGHT, xf=hx @ Matrix.Translation((0.0555, 0, 0)), bevel=0.0008)
    rbox(bm, (0.095, 0.020, 0.0075), BLUE, xf=hx @ Matrix.Translation((0.080, 0, 0.0005)),
         bevel=0.003, bsegs=3)
    # 阀杆：垫片 + 六角螺母 + 杆头
    lathe(bm, [(0, 0.0460), (0.011, 0.0460), (0.011, 0.0470), (0, 0.0470)], s6, BRIGHT)
    lathe(bm, [(0, 0.0470), (0.0085, 0.0470, True), (0.0085, 0.0535, True), (0.0072, 0.0545, True),
               (0, 0.0545)], s6, BRIGHT)
    lathe(bm, [(0, 0.0545), (0.0042, 0.0545), (0.0042, 0.0575), (0, 0.0575)], s6, BRIGHT)
    return finish_mesh("ball_valve_3way", bm, [M["cast"], M["bright"], M["blue"], M["bore"]])


# ---------------- 装配 ----------------
def tube_mesh(name, L, segs, M):
    bm = bmesh.new()
    ro, ri = P["tube_od"] / 2, P["tube_id"] / 2
    lathe(bm, [(ri, 0), (ro, 0), (ro, L), (ri, L)], segs, 0, closed=True)
    tube = finish_mesh(name, bm, [M["tube"]])
    bm = bmesh.new()
    lathe(bm, [(0, 0.0005), (ri - 0.0003, 0.0005), (ri - 0.0003, L - 0.0005), (0, L - 0.0005)], segs, 0)
    cool = finish_mesh(name + "_coolant", bm, [M["water"]])
    cool["length"] = L
    return tube, cool


def build_pipe(segs, M, coll):
    n, pitch = P["n_valves"], P["pitch"]
    xs = [(i - (n - 1) / 2) * pitch for i in range(n)]
    valve_me = build_valve(segs, M)
    for i, x in enumerate(xs):
        add_obj(f"valve_{i + 1:02d}", valve_me, (x, 0, 0), coll)

    ar, al, h0 = P["adapter_r"], P["adapter_len"], P["half_len"] - 0.002
    bm = bmesh.new()
    lathe(bm, [(0, 0), (ar, 0), (ar * 1.03, al * 0.5), (ar, al), (0, al)], segs, 0)
    adapter_me = finish_mesh("ptfe_adapter", bm, [M["ptfe"]])
    for i, x in enumerate(xs):
        for sgn in (1, -1):
            ob = add_obj(f"adapter_{i + 1:02d}{'R' if sgn > 0 else 'L'}", adapter_me, (x + sgn * h0, 0, 0), coll)
            ob.rotation_euler = (0, sgn * math.pi / 2, 0)

    # 管段：0 = 左端伸出段，1..n-1 = 阀间，n = 右端伸出段（从入口左侧起编号）
    reach = h0 + al - 0.003
    L_mid = pitch - 2 * reach
    mid_t, mid_c = tube_mesh("tube_mid", L_mid, segs, M)
    stub_t, stub_c = tube_mesh("tube_stub", P["stub_len"], segs, M)
    segments = [(xs[0] - reach - P["stub_len"], stub_t, stub_c)]
    segments += [(xs[i] + reach, mid_t, mid_c) for i in range(n - 1)]
    segments += [(xs[-1] + reach, stub_t, stub_c)]
    coolants = []
    for k, (x0, tm, cm) in enumerate(segments):
        for me, tag in ((tm, "tube"), (cm, "coolant")):
            ob = add_obj(f"{tag}_{k:02d}", me, (x0, 0, 0), coll)
            ob.rotation_euler = (0, math.pi / 2, 0)
            ob["segment"] = k
            if tag == "coolant":
                coolants.append(ob)

    # 下方支撑杆
    xa, xb = xs[0] - 0.12, xs[-1] + 0.12
    bm = bmesh.new()
    lathe(bm, [(0, 0), (P["rod_r"], 0), (P["rod_r"], xb - xa), (0, xb - xa)], segs, 0,
          xf=Matrix.Translation((xa, P["rod_y"], P["rod_z"])) @ ROT_Z_TO_X)
    add_obj("support_rod", finish_mesh("support_rod", bm, [M["bright"]]), coll=coll)

    # 两端：左 PVC 直接头 + 下弯；右 不锈钢六角接头 + 弯头；都接一段竖管
    xl, xr = xs[0] - reach - P["stub_len"], xs[-1] + reach + P["stub_len"]
    for side, x_end, mat, r, hexa in (("L", xl, M["pvc"], 0.0165, False), ("R", xr, M["cast"], 0.0150, True)):
        dirx = -1 if side == "L" else 1
        base = Matrix.Translation((x_end, 0, 0)) @ (Matrix.Rotation(math.pi, 4, 'Z') if dirx < 0 else Matrix())
        bm = bmesh.new()
        lathe(bm, [(0, -0.004), (r + 0.0015, -0.004, hexa), (r + 0.0015, 0.026, hexa), (0, 0.026)],
              max(12, segs // 6 * 6), 0, xf=base @ ROT_Z_TO_X)
        Rb = 0.032
        sweep_arc(bm, r, Rb, (0, 0, -1), segs, 12, 0, xf=base @ Matrix.Translation((0.026, 0, 0)))
        lathe(bm, [(0, -Rb - 0.14), (r, -Rb - 0.14), (r, -Rb), (0, -Rb)], segs, 0,
              xf=base @ Matrix.Translation((0.026 + Rb, 0, 0)))
        add_obj(f"end_{side}", finish_mesh(f"end_{side}", bm, [mat]), coll=coll)
    return xs, coolants


def temp_profile(spec, xs):
    """沿管轴的温度场 T(x)。spec 二选一：
      {"points": [[x_m, T], ...]}                         —— 实测点，线性插值
      {"inlet": T0, "outlet": T1, "hotspots": [{"valve": 6, "dT": 6, "sigma": 0.08}, ...]}
    hotspots 也可写 "x"（米）代替 "valve"（1 起编号）。"""
    if "points" in spec:
        pts = sorted(spec["points"])
        def T(x):
            if x <= pts[0][0]:
                return pts[0][1]
            for (x0, t0), (x1, t1) in zip(pts[:-1], pts[1:]):
                if x <= x1:
                    return t0 + (t1 - t0) * (x - x0) / (x1 - x0)
            return pts[-1][1]
        return T
    xa, xb = xs[0] - 0.12, xs[-1] + 0.12
    n, pitch = P["n_valves"], P["pitch"]   # valve 可为小数，6.5 = 阀 6 与阀 7 之间
    hs = [(h.get("x", (h.get("valve", 1) - 1 - (n - 1) / 2) * pitch), h["dT"], h.get("sigma", 0.08))
          for h in spec.get("hotspots", [])]
    def T(x):
        u = min(max((x - xa) / (xb - xa), 0.0), 1.0)
        return (spec["inlet"] + (spec["outlet"] - spec["inlet"]) * u
                + sum(dT * math.exp(-0.5 * ((x - xc) / sg) ** 2) for xc, dT, sg in hs))
    return T


def heat_water_material():
    m = bpy.data.materials.new("Coolant_heat")
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes.get("Principled BSDF")
    attr = nt.nodes.new("ShaderNodeVertexColor")
    attr.layer_name = "temp_color"
    nt.links.new(attr.outputs["Color"], b.inputs["Base Color"])
    nt.links.new(attr.outputs["Color"], b.inputs["Emission Color"])
    for k, v in (("Roughness", 0.15), ("Emission Strength", 0.85), ("Transmission Weight", 0.0)):
        b.inputs[k].default_value = v
    return m


def apply_heat(coolants, T, vmin, vmax, segs, ring_dz=0.003):
    """把每段冷却液换成独立网格：轴向细分 + 逐顶点颜色，跨阀连续渐变。"""
    mat = heat_water_material()
    ri = P["tube_id"] / 2 - 0.0003
    for ob in coolants:
        L = ob.data["length"]
        n = max(4, int(L / ring_dz))
        prof = [(0, 0.0005)] + [(ri, 0.0005 + (L - 0.001) * i / n) for i in range(n + 1)] + [(0, L - 0.0005)]
        bm = bmesh.new()
        lathe(bm, prof, segs, 0)
        me = finish_mesh(f"coolant_heat_{ob['segment']:02d}", bm, [mat])
        col = me.color_attributes.new("temp_color", 'FLOAT_COLOR', 'POINT')
        x0 = ob.location.x
        for v in me.vertices:   # 局部 z 经 rot_y(90°) 变为世界 +x
            t = (T(x0 + v.co.z) - vmin) / (vmax - vmin + 1e-9)
            col.data[v.index].color = (*heat_color(t), 1.0)
        me.color_attributes.active_color = col
        ob.data = me


# ---------------- 灯光与相机 ----------------
def add_area(name, loc, target, size, power, color=(1, 1, 1)):
    li = bpy.data.lights.new(name, 'AREA')
    li.size, li.energy, li.color = size, power, color
    ob = bpy.data.objects.new(name, li)
    ob.location = loc
    ob.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.collection.objects.link(ob)


def add_camera(name, loc, target, lens):
    cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    cam.data.lens, cam.data.clip_start = lens, 0.01
    cam.location = loc
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.collection.objects.link(cam)
    return cam


def setup_stage(M, xs, samples):
    sc = bpy.context.scene
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0.11, -0.3), rotation=(math.pi / 2, 0, 0))
    wall = bpy.context.active_object
    wall.name, wall.scale = "wall", (4.0, 2.5, 1)
    wall.data.materials.append(M["wall"])
    sc.world = sc.world or bpy.data.worlds.new("World")
    bg = sc.world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.55, 0.57, 0.60, 1)
    bg.inputs[1].default_value = 0.18
    add_area("key", (-0.8, -1.2, 1.0), (0, 0, 0), 1.0, 45)
    add_area("fill", (1.2, -1.0, 0.2), (0, 0, 0), 1.2, 14, (0.92, 0.96, 1.0))
    add_area("top_strip", (0, -0.35, 0.8), (0, 0, 0), 2.0, 22)
    cams = dict(
        hero=add_camera("cam_hero", (-0.75, -2.10, 0.55), (0.13, 0, -0.04), 37),
        closeup=add_camera("cam_closeup", (xs[3] - 0.13, -0.30, 0.16), (xs[3] + 0.02, 0, 0.005), 55),
        front=add_camera("cam_front", (0.15, -2.1, 0.02), (0.15, 0, -0.03), 30),
    )
    sc.render.engine = 'CYCLES'
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'METAL'
        prefs.get_devices()
        for d in prefs.devices:
            d.use = True
        sc.cycles.device = 'GPU'
    except Exception as e:  # 没有 Metal 就用 CPU
        print("GPU unavailable:", e)
    c = sc.cycles
    c.samples, c.use_denoising = samples, True
    c.max_bounces, c.glossy_bounces, c.transmission_bounces, c.transparent_max_bounces = 16, 8, 16, 16
    sc.render.resolution_x, sc.render.resolution_y = 1920, 1080
    sc.view_settings.view_transform = 'AgX'
    return cams


def emission_mat(name, color=None, use_vcol=False):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    if use_vcol:
        vc = nt.nodes.new("ShaderNodeVertexColor")
        vc.layer_name = "bar_color"
        nt.links.new(vc.outputs["Color"], em.inputs["Color"])
    else:
        em.inputs["Color"].default_value = (*color, 1)
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


def add_colorbar(cam, vmin, vmax, label, nticks=6, dist=0.12):
    """在相机前方右侧放竖直色标（随相机），只给该相机渲染时显示。"""
    sc = bpy.context.scene
    hw = dist * cam.data.sensor_width / (2 * cam.data.lens)
    hh = hw * sc.render.resolution_y / sc.render.resolution_x
    w, h = 0.022 * hw * 2, 0.55 * hh * 2
    x0, y0 = hw * 0.80, -h / 2
    objs = []
    me = bpy.data.meshes.new(f"cbar_{cam.name}")
    n = 64
    verts = [(x0 + dx, y0 + h * i / n, -dist) for i in range(n + 1) for dx in (0, w)]
    faces = [(2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2) for i in range(n)]
    me.from_pydata(verts, [], faces)
    col = me.color_attributes.new("bar_color", 'FLOAT_COLOR', 'POINT')
    for i in range(n + 1):
        for k in range(2):
            col.data[2 * i + k].color = (*heat_color(i / n), 1)
    me.materials.append(emission_mat(f"cbar_mat_{cam.name}", use_vcol=True))
    objs.append(bpy.data.objects.new(f"cbar_{cam.name}", me))
    pad = 0.06 * hh * 2   # 白底图例面板，避免压在模型上看不清
    pm = bpy.data.meshes.new(f"cbar_panel_{cam.name}")
    px0, px1, py0, py1, pz = x0 - pad * 0.6, hw * 0.985, y0 - pad, y0 + h + pad * 1.6, -dist - 0.0005
    pm.from_pydata([(px0, py0, pz), (px1, py0, pz), (px1, py1, pz), (px0, py1, pz)], [], [(0, 1, 2, 3)])
    pm.materials.append(emission_mat(f"cbar_panel_mat_{cam.name}", (0.92, 0.92, 0.92)))
    objs.append(bpy.data.objects.new(pm.name, pm))
    txt_mat = emission_mat(f"cbar_txt_{cam.name}", (0.02, 0.02, 0.02))
    fs = 0.032 * hh * 2
    labels = [(y0 + h * i / (nticks - 1), f"{vmin + (vmax - vmin) * i / (nticks - 1):g}") for i in range(nticks)]
    labels.append((y0 + h + fs * 1.3, label))
    for j, (y, t) in enumerate(labels):
        cu = bpy.data.curves.new(f"cbar_t{j}_{cam.name}", 'FONT')
        cu.body, cu.size = t, fs
        cu.align_x = 'LEFT' if j < nticks else 'CENTER'
        cu.align_y = 'CENTER'
        cu.materials.append(txt_mat)
        ob = bpy.data.objects.new(cu.name, cu)
        ob.location = (x0 + w * 1.6, y, -dist) if j < nticks else (x0 + w / 2, y, -dist)
        objs.append(ob)
        if j < nticks:  # 刻度短线
            tm = bpy.data.meshes.new(f"cbar_k{j}_{cam.name}")
            tm.from_pydata([(x0 + w, y - fs * 0.04, -dist), (x0 + w * 1.35, y - fs * 0.04, -dist),
                            (x0 + w * 1.35, y + fs * 0.04, -dist), (x0 + w, y + fs * 0.04, -dist)], [], [(0, 1, 2, 3)])
            tm.materials.append(txt_mat)
            objs.append(bpy.data.objects.new(tm.name, tm))
    for ob in objs:
        sc.collection.objects.link(ob)
        ob.parent = cam
        ob.visible_shadow = ob.visible_glossy = ob.visible_transmission = ob.visible_diffuse = False
    return objs


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["real", "heat"], default="real")
    ap.add_argument("--heat")
    ap.add_argument("--segs", type=int, default=32)
    ap.add_argument("--samples", type=int, default=128)
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    os.makedirs(args.out, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    M = make_materials()
    coll = bpy.data.collections.new("Pipe")
    bpy.context.scene.collection.children.link(coll)
    xs, coolants = build_pipe(args.segs, M, coll)
    suffix = ""
    heat = None
    if args.mode == "heat":
        spec = json.load(open(args.heat)) if args.heat else {   # 演示：22→27 °C 沿程升温，阀 6-7 间强热点、阀 3-4 间弱热点
            "inlet": 22.0, "outlet": 27.0,
            "hotspots": [{"valve": 6.5, "dT": 8.0, "sigma": 0.17}, {"valve": 3.5, "dT": 2.5, "sigma": 0.10}]}
        T = temp_profile(spec, xs)
        samp = [T(xs[0] - 0.12 + i * 0.002) for i in range(int((xs[-1] - xs[0] + 0.24) / 0.002) + 1)]
        lo, hi = spec.get("vmin", min(samp)), spec.get("vmax", max(samp))
        step = next(st for st in (0.5, 1, 2, 5, 10, 20, 50) if (hi - lo) / st <= 6)   # 刻度取整
        vmin, vmax = math.floor(lo / step) * step, math.ceil(hi / step) * step
        apply_heat(coolants, T, vmin, vmax, args.segs)
        heat = (vmin, vmax, spec.get("label", "T (°C)"), round((vmax - vmin) / step) + 1)
        print(f"heat range {vmin} .. {vmax}, field {min(samp):.2f} .. {max(samp):.2f}")
        suffix = "_heat"

    tris = sum(sum(len(p.vertices) - 2 for p in me.polygons) for me in bpy.data.meshes)
    tris_scene = sum(sum(len(p.vertices) - 2 for p in ob.data.polygons) for ob in coll.objects)
    print(f"unique-mesh tris {tris}, scene tris {tris_scene}, objects {len(coll.objects)}")

    bpy.ops.object.select_all(action='DESELECT')
    for ob in coll.objects:
        ob.select_set(True)
    glb = os.path.join(args.out, f"pipe{suffix}.glb")
    bpy.ops.export_scene.gltf(filepath=glb, export_format='GLB', use_selection=True)
    print(f"glb {os.path.getsize(glb) / 1e6:.2f} MB -> {glb}")

    cams = setup_stage(M, xs, args.samples)
    blend = os.path.join(args.out, f"pipe{suffix}.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
    print(f"blend {os.path.getsize(blend) / 1e6:.2f} MB -> {blend}")
    sc = bpy.context.scene
    bars = {}
    if heat:
        sc.view_settings.view_transform = 'Standard'   # 色标颜色不被 AgX 压暗；灯光相应调暗
        for ob in sc.objects:
            if ob.type == 'LIGHT':
                ob.data.energy *= 0.45
        sc.world.node_tree.nodes["Background"].inputs[1].default_value *= 0.5
        for name, cam in cams.items():
            bars[name] = add_colorbar(cam, *heat, dist=0.10 if name == "closeup" else 0.15)
    for name, cam in cams.items():
        for k, objs in bars.items():
            for ob in objs:
                ob.hide_render = (k != name)
        sc.camera = cam
        if args.no_render:
            continue
        sc.render.filepath = os.path.join(args.out, f"render_{name}{suffix}.png")
        bpy.ops.render.render(write_still=True)
        print("rendered", sc.render.filepath)


main()
