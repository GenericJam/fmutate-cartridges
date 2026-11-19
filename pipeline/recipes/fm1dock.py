"""Recipe "fm1dock": a firmware released as an fm1-dock solo app (joakimeriksson/fm1-dock's tools/build.py
--modules DIR --name ... [--pack FILE]: fm1-dock with one module of its own, which a new install starts), whose
repository holds the module (DIR, its module.cfg) and the script that makes the release (FM-1 SID's
dock/build_solo.sh), with fm1-dock at the commit the release names pinned in deps/<id>.json.

That script runs as its author wrote it, with fm1-dock's tree as FM1_DOCK: it makes what the module needs (generated
headers, the --pack data) and stops at fm1-dock's own device build (no JieLi toolchain here). The module's sources are
then compiled as fm1-dock's tools/hbmod.py compiles them (the directory's .c / .cc and module.cfg's sources, its
includes and flags, -I firmware/dock and modules/lib) for this computer, with the --pack data where the module finds
it (right after its image: module.ld's _hb_rt_load + the .ram_text's size, which has no copy here). fm1-dock's own
fake FM-1 (tests/host.c, as recipes/dinghy.py builds it) runs adapters/fm1dock.c, dock.c's host for that one module,
in place of its sample app; adapters/dinghy.c drives it."""
import os
import re
import shlex
import shutil

import deps
from common import CAT, HERE, SHIM_INC, SRC, sh, target_cc
from recipes import dinghy

ORDER = 12

_BUILD = re.compile(r"tools/build\.py\b[^\n]*?--modules\s+(\S+)")
_PACK = re.compile(r"--pack\s+(\S+)")


def _dock(top, fid):
    """the pinned fm1-dock tree (a deps path holding its module interface and its fake FM-1)"""
    for d in deps.load(fid):
        p = top / d["path"] if "path" in d else None
        if p and all((p / f).exists() for f in ("firmware/dock/hb_module.h", "modules/lib/hb_fm1.h",
                                                 "firmware/src/platform.c", "tests/host.c")):
            return d["path"]
    return None


def _path(arg, script):
    """a path the script passes (its variables expanded; $HERE / $PWD: the root it cds to)"""
    a = re.sub(r"\$\{?(HERE|PWD|OLDPWD)\}?", "", arg.strip("\"'"))
    return dinghy._expand(a, script).strip("\"'").lstrip("/")


def detect(top, fid):
    dock = _dock(top, fid)
    if not dock:
        return None
    for s in sorted(top.glob("*.sh")) + sorted(top.glob("*/*.sh")):
        t = s.read_text(errors="replace").replace("\\\n", " ")
        m = _BUILD.search(t)
        if not m:
            continue
        mod = _path(m.group(1), t)
        if not (top / mod / "module.cfg").exists():
            continue
        r = {"recipe": "fm1dock", "script": str(s.relative_to(top)), "dock": dock, "module": mod}
        p = _PACK.search(t)
        if p:
            r["pack"] = _path(p.group(1), t)
        return r
    return None


def _cfg(d):
    """module.cfg as tools/hbmod.py reads it"""
    cfg = {"cflags": "", "cxxflags": "", "sources": "", "includes": ""}
    for ln in (d / "module.cfg").read_text().splitlines():
        ln = ln.split("#", 1)[0].strip()
        if "=" in ln:
            k, v = (x.strip() for x in ln.split("=", 1))
            cfg[k] = v
    return cfg


def _module(fid, r, target, shims, log):
    """the module's objects (tools/hbmod.py's units and flags; not its hb_lib.c: the C library is here)"""
    top = SRC / fid
    dock, mod = top / r["dock"], top / r["module"]
    cfg = _cfg(mod)
    cc, cflags_t, _ = target_cc(target)
    work = CAT / "obj" / target / f"{fid}.module"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    srcs = sorted([*mod.glob("*.c"), *mod.glob("*.cc")]) + [(mod / s).resolve() for s in cfg["sources"].split()]
    defs = ["-DHB_DESC=const hb_module_t hb_module"]    # (hb_module.h's, without its ELF section)
    if r.get("pack"):
        data = (top / r["pack"]).read_bytes()
        pc = work / "pack.c"
        pc.write_text("/* the solo app's --pack data, right after the module's image (fm1-dock's tools/build.py) */\n"
                      "#include <stdint.h>\n"
                      f"const uint8_t fm1dock_pack[{len(data)}] __attribute__((aligned(4))) = {{"
                      + ",".join(str(b) for b in data) + "};\n")
        srcs.append(pc)
        # the image's end: the module's .ram_text has no copy here (start = end), its load address is the pack's
        defs += ["-D_hb_rt_load=fm1dock_pack", "-D_hb_rt_start=fm1dock_pack", "-D_hb_rt_end=fm1dock_pack"]
    shimflags = ["-I", str(SHIM_INC), "-include", str(SHIM_INC / "fm1_native_shims.h")] if shims else []
    flags = [*cc, "-fPIC", "-O2", "-w", "-ffp-contract=off", "-fvisibility=hidden", *cflags_t, *shimflags, *defs,
             "-I", str(dock / "firmware/dock"), "-I", str(dock / "modules/lib"), "-I", str(mod),
             *[f"-I{(mod / i).resolve()}" for i in cfg["includes"].split()], *shlex.split(cfg["cflags"])]
    objs = []
    for i, s in enumerate(srcs):
        o = work / f"m{i}_{s.stem}.o"
        cxx = shlex.split(cfg["cxxflags"]) if s.suffix == ".cc" else []
        if sh([*flags, *cxx, "-c", str(s), "-o", str(o)], top, log=log):
            return None
        objs.append(str(o))
    return objs


def build(fid, r, target, log, res):
    top = SRC / fid
    dock = (top / r["dock"]).resolve()
    env = dict(os.environ, FM1_DOCK=str(dock), FM1_HARBOR=str(dock), JIELI_TOOLCHAIN="/nonexistent",
               JIELI_DOCKER="0", AC79_SDK="/nonexistent")
    sh(["sh", r["script"]], top, env, log)              # (stops at fm1-dock's device build: the rest is made by then)
    if r.get("pack") and not (top / r["pack"]).exists():
        log.append(f"fm1dock: no {r['pack']} after {r['script']}")
        return None, "script"
    rd = dinghy._detect(dock, fid)
    if not rd:
        log.append(f"fm1dock: {r['dock']} is not a Dinghy tree with a fake FM-1")
        return None, "recipe"
    rd.update(root=str(dock), app_from=str(HERE / "adapters/fm1dock.c"))   # (absolute: dinghy.py joins them)
    for shims in (False, True):
        objs = _module(fid, r, target, shims, log)
        if objs:
            break
    if not objs:
        return None, "compile"
    rd["objs"] = objs
    res["dinghy"] = {k: v for k, v in rd.items() if k not in ("root", "app_from", "objs")}
    return dinghy.build(fid, rd, target, log, res)
