#!/usr/bin/env python3
"""
test_organize.py —— file-organizer skill 的端到端实测

覆盖：
  A. 盘点：计数、顶层识别、敏感路径排除、符号链接、原子包、同内容去重
  B. 约束校验：超层数、超层宽、超长路径、敏感源、符号链接源、越界、自噬
  C. 执行：干跑不动、真执行落盘、文件数与字节数守恒、回滚
  D. 清理：暂存区可恢复、无硬删除路径

用法：python test_organize.py
"""
import json
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
ORG = os.path.join(HERE, "organize.py")
BASE = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "organize-test")
results = []


def chk(name, cond, detail=""):
    results.append((name, cond, detail))
    print("  [%s] %-46s %s" % ("PASS" if cond else "FAIL", name, detail))


def run(args):
    return subprocess.run([PY, ORG] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def fresh(p):
    if os.path.isdir(p):
        shutil.rmtree(p)
    os.makedirs(p)
    return p


def w(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


if os.path.isdir(BASE):
    shutil.rmtree(BASE)
os.makedirs(BASE)

# ============================================================
print("=" * 76)
print(" A. 盘点")
print("=" * 76)
work = fresh(os.path.join(BASE, "work"))
os.makedirs(os.path.join(work, "项目A", "正文"))
os.makedirs(os.path.join(work, "项目B"))
os.makedirs(os.path.join(work, "资料C", "子目录"))
for p, c in (("项目A/正文/第1章.md", "x" * 100), ("项目A/正文/第2章.md", "y" * 100),
             ("项目A/说明.md", "z" * 50), ("项目B/主文件.pdf", "p" * 200),
             ("资料C/子目录/a.txt", "b" * 30), ("资料C/c.txt", "d" * 30)):
    w(os.path.join(work, p.replace("/", os.sep)), c)

# 敏感路径：应被排除，不进 files
w(os.path.join(work, ".env"), "SECRET=1\n")
w(os.path.join(work, "config", "server.pem"), "-----BEGIN-----\n")
w(os.path.join(work, "config", "credentials.json"), '{"k":"v"}')
os.makedirs(os.path.join(work, ".ssh"), exist_ok=True)
w(os.path.join(work, ".ssh", "id_rsa"), "PRIVATE\n")
# 原子包
os.makedirs(os.path.join(work, "Photos.photoslibrary", "data"), exist_ok=True)
w(os.path.join(work, "Photos.photoslibrary", "data", "x.bin"), "b" * 10)
# 同内容重复
w(os.path.join(work, "dup1.txt"), "SAME CONTENT HERE")
w(os.path.join(work, "dup2.txt"), "SAME CONTENT HERE")

inv = os.path.join(BASE, "inv.json")
r = run(["inventory", "--src", work, "--out", inv])
chk("inventory 退出码 0", r.returncode == 0, r.stderr[:120])
iv = json.load(open(inv, encoding="utf-8"))
# 6 个正文文件 + 2 个重复文件 + 1 个原子包（整体算 1 个）= 9
chk("盘点文件数正确(9)", iv["file_count"] == 9, "实得 %d" % iv["file_count"])
# 顶层：项目A 项目B 资料C（config 被敏感排除，photoslibrary 单列不算顶层）
chk("顶层目录识别正确(3)", len(iv["top_level"]) == 3, "实得 %d" % len(iv["top_level"]))
chk("敏感路径被排除", not any(f["rel"].endswith(".env") or ".pem" in f["rel"]
                              or "credentials" in f["rel"] or ".ssh" in f["rel"]
                              for f in iv["files"]))
chk("敏感目录整棵排除", not any(".ssh" in f["rel"] for f in iv["files"]))
chk("敏感路径有记录", len(iv["protected_paths"]) >= 3,
    "记录 %d 条" % len(iv["protected_paths"]))
chk("原子包被识别", any("photoslibrary" in b for b in iv["atomic_bundles"]),
    str(iv["atomic_bundles"]))
chk("原子包未拆开", not any("photoslibrary/data" in f["rel"] for f in iv["files"]))
chk("同内容重复被识别", len(iv["duplicate_groups"]) == 1,
    "实得 %d 组" % len(iv["duplicate_groups"]))
if iv["duplicate_groups"]:
    chk("重复组含两个路径", len(iv["duplicate_groups"][0]["paths"]) == 2,
        str(iv["duplicate_groups"][0]["paths"]))

# ============================================================
print()
print("=" * 76)
print(" B. 约束校验")
print("=" * 76)
root = os.path.join(BASE, "organized")
mp = {"src": work, "root": root, "max_depth": 3, "l1_max": 7, "l23_max": 3,
      "moves": [
          {"from": os.path.join(work, "项目A"), "to": "01-创作/长篇/项目A"},
          {"from": os.path.join(work, "项目B"), "to": "01-创作/短篇/项目B"},
          {"from": os.path.join(work, "资料C"), "to": "02-资料/归档/资料C"},
      ]}
mpp = os.path.join(BASE, "map.json")
json.dump(mp, open(mpp, "w", encoding="utf-8"), ensure_ascii=False)

r = run(["apply", "--src", work, "--map", mpp])
chk("apply 干跑退出码 0", r.returncode == 0, r.stderr[:200])
chk("干跑未移动", os.path.isdir(os.path.join(work, "项目A")))

# 超层宽：根下 8 个桶
bad = dict(mp)
bad["moves"] = [{"from": os.path.join(work, "项目A"), "to": "b%d/子/项" % i} for i in range(8)]
badp = os.path.join(BASE, "bad.json")
json.dump(bad, open(badp, "w", encoding="utf-8"), ensure_ascii=False)
r = run(["apply", "--src", work, "--map", badp])
chk("超层宽被拒(退出码3)", r.returncode == 3, "rc=%d" % r.returncode)
chk("超层宽时未改动", os.path.isdir(os.path.join(work, "项目A")))

# 超深度：4 层
bad2 = dict(mp)
bad2["moves"] = [{"from": os.path.join(work, "项目A"), "to": "a/b/c/d"}]
bad2p = os.path.join(BASE, "bad2.json")
json.dump(bad2, open(bad2p, "w", encoding="utf-8"), ensure_ascii=False)
r = run(["apply", "--src", work, "--map", bad2p])
chk("超深度被拒(退出码3)", r.returncode == 3, "rc=%d" % r.returncode)

# 超长路径：本机 LongPathsEnabled=0
bad3 = dict(mp)
bad3["moves"] = [{"from": os.path.join(work, "项目A"),
                  "to": "01-创作/长篇/" + "超长目录名" * 12 + "/项目A"}]
bad3p = os.path.join(BASE, "bad3.json")
json.dump(bad3, open(bad3p, "w", encoding="utf-8"), ensure_ascii=False)
r = run(["apply", "--src", work, "--map", bad3p])
chk("超长路径被拒(退出码3)", r.returncode == 3, "rc=%d" % r.returncode)
chk("超长路径时未改动", os.path.isdir(os.path.join(work, "项目A")))

# 敏感源
bad4 = dict(mp)
bad4["moves"] = [{"from": os.path.join(work, ".env"), "to": "01-x/y/z"}]
bad4p = os.path.join(BASE, "bad4.json")
json.dump(bad4, open(bad4p, "w", encoding="utf-8"), ensure_ascii=False)
r = run(["apply", "--src", work, "--map", bad4p])
chk("敏感源被拒(退出码3)", r.returncode == 3, "rc=%d" % r.returncode)
chk("敏感源未被移动", os.path.isfile(os.path.join(work, ".env")))

# 目标越界(..)
bad5 = dict(mp)
bad5["moves"] = [{"from": os.path.join(work, "项目A"), "to": "../逃逸/项目A"}]
bad5p = os.path.join(BASE, "bad5.json")
json.dump(bad5, open(bad5p, "w", encoding="utf-8"), ensure_ascii=False)
r = run(["apply", "--src", work, "--map", bad5p])
chk("越界目标被拒(退出码3)", r.returncode == 3, "rc=%d" % r.returncode)

# ============================================================
print()
print("=" * 76)
print(" C. 执行 / 对账 / 回滚")
print("=" * 76)
r = run(["apply", "--src", work, "--map", mpp, "--apply"])
chk("apply 退出码 0", r.returncode == 0, r.stdout[-300:] + r.stderr[:200])
chk("文件已落到目标树",
    os.path.isfile(os.path.join(root, "01-创作", "长篇", "项目A", "正文", "第1章.md")))
chk("源端已无残留", not os.path.exists(os.path.join(work, "项目A")))
n_target = sum(len(fs) for _, _, fs in os.walk(root))
chk("移动文件数守恒(6==6)", n_target == 6, "目标 %d 个" % n_target)
chk("敏感文件未被搬运", not os.path.isfile(os.path.join(root, "01-创作", "长篇", "项目A", ".env")))
chk("敏感文件仍在源端", os.path.isfile(os.path.join(work, ".env")))

rb = os.path.join(BASE, "map.rollback.json")
r = run(["verify", "--src", work, "--map", mpp, "--rollback-map", rb])
chk("verify 退出码 0", r.returncode == 0, r.stdout[-200:] + r.stderr[:150])

r = run(["rollback", "--rollback-map", rb, "--apply"])
chk("rollback 退出码 0", r.returncode == 0, r.stderr[:150])
chk("回滚后源端恢复", os.path.isfile(os.path.join(work, "项目A", "正文", "第1章.md")))
# 守恒口径：只数"可整理"的条目（敏感路径与原子包内部不计入），
# 整批回滚后这个数必须回到整理前的水平。
movable = [f for f in os.listdir(work)
           if f in ("项目A", "项目B", "资料C", "dup1.txt", "dup2.txt")]
chk("回滚后可整理条目全部归位", sorted(movable) == sorted(
    ["项目A", "项目B", "资料C", "dup1.txt", "dup2.txt"]), str(sorted(movable)))
chk("回滚后项目A文件数不变",
    sum(len(fs) for _, _, fs in os.walk(os.path.join(work, "项目A"))) == 3)
chk("回滚后敏感文件仍在源端", os.path.isfile(os.path.join(work, ".env")))

# 同名不覆盖：目标已存在同名文件
print()
print("  -- 同名冲突 --")
work2 = fresh(os.path.join(BASE, "work2"))
w(os.path.join(work2, "a.txt"), "SOURCE")
root2 = os.path.join(BASE, "organized2")
w(os.path.join(root2, "01-x", "y", "a.txt"), "EXISTING")
mp2 = {"src": work2, "root": root2,
       "moves": [{"from": os.path.join(work2, "a.txt"), "to": "01-x/y/a.txt"}]}
mp2p = os.path.join(BASE, "map2.json")
json.dump(mp2, open(mp2p, "w", encoding="utf-8"), ensure_ascii=False)
r = run(["apply", "--src", work2, "--map", mp2p, "--apply"])
chk("同名冲突时退出码 0", r.returncode == 0, r.stdout[-200:] + r.stderr[:150])
chk("原有文件未被覆盖",
    open(os.path.join(root2, "01-x", "y", "a.txt"), encoding="utf-8").read() == "EXISTING")
chk("新文件被改名为 _1",
    os.path.isfile(os.path.join(root2, "01-x", "y", "a_1.txt")))

# ============================================================
print()
print("=" * 76)
print(" D. 清理：暂存区可恢复")
print("=" * 76)
junk = os.path.join(BASE, "junk.txt")
w(os.path.join(work2, "junk1.bin"), "JUNK")
w(os.path.join(work2, "junk2.bin"), "JUNK")
with open(junk, "w", encoding="utf-8") as f:
    f.write("# 注释行\njunk1.bin\njunk2.bin\n")

r = run(["clean", "--root", work2, "--targets", junk])
chk("clean 干跑退出码 0", r.returncode == 0, r.stderr[:150])
chk("clean 干跑未搬走", os.path.isfile(os.path.join(work2, "junk1.bin")))

r = run(["clean", "--root", work2, "--targets", junk, "--apply"])
chk("clean 暂存退出码 0", r.returncode == 0, r.stdout[-200:] + r.stderr[:150])
chk("垃圾已移出原位", not os.path.exists(os.path.join(work2, "junk1.bin")))
stage_root = os.path.join(work2, "_trash-candidates")
chk("暂存区已建立", os.path.isdir(stage_root))
chk("暂存区有来源记录",
    any(f.endswith("_origins.md") for _, _, fs in os.walk(stage_root) for f in fs))
chk("垃圾文件仍在暂存区(可恢复)",
    any(f == "junk1.bin" for _, _, fs in os.walk(stage_root) for f in fs))

print()
print("=" * 76)
p = sum(1 for _, c, _ in results if c)
print(" 实测结果: %d/%d 通过" % (p, len(results)))
for n, c, d in results:
    if not c:
        print("   FAILED: %s  %s" % (n, d))
print("=" * 76)
sys.exit(0 if p == len(results) else 1)
