#!/usr/bin/env python3
"""
organize.py —— 资料/文件整理执行器
              （3 层结构 / 禁删 / 可回滚 / 敏感路径保护）

分工原则：**AI 判断语义，工具强制机械约束。**

分类轴、每个文件归到哪、哪些是重复、哪些是垃圾 —— 这些靠语义判断，由 AI（或用户）
写进 map.json；本工具只负责那些不能靠判断解决、必须机械执行的事：

    - 层级深度 ≤ 3
    - 第 1 层子项数 ≤ 7，第 2/3 层 ≤ 3
    - 禁止删除：只允许移动/重命名，清理走暂存或回收站，没有硬删除路径
    - 目标同名不覆盖：自动改名并记录，事后可回滚
    - 敏感路径（.env/密钥/证书/浏览器 profile）完全排除，不读不改
    - 符号链接不跟随，当作不透明对象
    - OS 原子包（.app/.photoslibrary 等）整体处理，不拆开
    - 同内容重复文件按 size→sha256 两级识别，不重复搬运
    - 跨盘移动先复制、校验 size+sha256，再删源
    - 落地后对账：文件数与字节数守恒
    - 全程产出回滚映射表

来源：用户 2026-08-15 起反复下达的固定约定（历史出现 15 次），
     原文见长期记忆「资料整理的结构约定」条目。

子命令：
    # 1) 盘点：产出结构化清单，供 AI/用户决定分类
    python organize.py inventory --src "E:\\归集" --out "E:\\inventory.json"

    # 2) 校验映射（只读，不改任何文件）
    python organize.py apply --src "E:\\归集" --map "E:\\map.json"

    # 3) 执行（落地后自动对账，并生成 map.rollback.json）
    python organize.py apply --src "E:\\归集" --map "E:\\map.json" --apply

    # 4) 对账
    python organize.py verify --src "E:\\归集" --map "E:\\map.json"

    # 5) 回滚（用第 3 步生成的 map.rollback.json）
    python organize.py rollback --rollback-map "E:\\map.rollback.json" --apply

    # 6) 清理垃圾（默认只进暂存区，不删）
    python organize.py clean --root "E:\\归集" --targets "E:\\junk.txt"

退出码：0 成功 / 2 参数或源缺失 / 3 映射未通过约束校验（未做任何改动）
        / 4 对账不一致 / 5 回滚目标缺失
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_MAX_DEPTH = 3
DEFAULT_L1_MAX = 7
DEFAULT_L23_MAX = 3
# Windows MAX_PATH=260；本机 LongPathsEnabled=0，留 10 字符余量
DEFAULT_MAX_PATH_LEN = 250
# 计算 sha256 的单文件上限：超过则只比大小，不算哈希（避免大文件拖慢盘点）
DEFAULT_HASH_LIMIT = 64 * 1024 * 1024

SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".git", "node_modules",
             ".venv", "venv", "__pycache__", ".DS_Store"}

# 敏感文件名/扩展名（大小写不敏感）——命中即完全排除，不读不改不搬
SENSITIVE_NAMES = {"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "credentials",
                   "secrets.json", "secrets.yaml", "secrets.yml", "shadow",
                   ".netrc", "_netrc", ".npmrc", ".pypirc", "known_hosts",
                   "authorized_keys", "master.key"}
SENSITIVE_EXTS = {".pem", ".key", ".pfx", ".p12", ".asc", ".gpg", ".kdbx",
                   ".jks", ".keystore", ".ppk"}
SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker",
                   ".config/gcloud", "keychains", "passwords"}
# 浏览器 profile / 系统目录 —— 整体排除
SENSITIVE_DIR_TOKENS = {"user data", "profiles", "profile default", "login data"}

# OS 原子包：整体处理，不拆开、不逐文件分类
ATOMIC_BUNDLES = {".app", ".photoslibrary", ".pages", ".numbers", ".key",
                   ".framework", ".bundle", ".photoslibrary"}

# 暂存区（清理垃圾用），不删只挪
TRASH_DIRNAME = "_trash-candidates"


def is_skip_dir(name):
    """统计口径：固定名单 + 任何含 'venv' 的变体（.venv-ocr / venv310 等）"""
    n = name.lower()
    return n in {s.lower() for s in SKIP_DIRS} or "venv" in n


def is_atomic_bundle(name):
    """OS 原子包：.app / .photoslibrary 这类，整体搬不拆开"""
    low = name.lower()
    return any(low.endswith(ext) for ext in ATOMIC_BUNDLES)


def is_protected(path_parts):
    """判断一个相对路径是否落在敏感范围内。

    命中敏感文件、密钥目录、浏览器 profile —— 这类路径不进入盘点清单，
    也不会被任何 move 触及。

    目录名命中敏感清单时，**整棵子树**都算敏感（不再下探）；
    文件名命中时，只有该文件本身被排除。
    """
    lowered = [p.lower() for p in path_parts if p]
    if not lowered:
        return False

    # 路径中任一段命中敏感目录 → 整条路径敏感
    for seg in lowered:
        if seg in SENSITIVE_DIRS or seg in SENSITIVE_DIR_TOKENS:
            return True

    base = lowered[-1]
    if base in SENSITIVE_NAMES:
        return True
    ext = os.path.splitext(base)[1]
    if ext in SENSITIVE_EXTS:
        return True
    # 词干匹配：credentials.json / credentials.yml / credentials.txt 等
    # 精确名名单只写了 "credentials"，按词干再匹配一次才覆盖到带扩展名的变体
    stem = os.path.splitext(base)[0]
    if stem in SENSITIVE_NAMES:
        return True
    # 形如 ".env" / ".env.local" / ".env.production"
    if base == ".env" or base.startswith(".env."):
        return True
    return False


def rel_to_src(path, src):
    """把路径转成相对 src 的分段；不在 src 之下返回 None。

    比 os.path.relpath 安全：跨盘符时 relpath 会抛 ValueError，
    拼 '..' 段时返回 None 而不是误判成敏感路径。
    """
    if not src:
        return None
    try:
        p = os.path.abspath(path)
        s = os.path.abspath(src)
        if os.path.commonpath([p, s]) != s:
            return None
        rel = os.path.relpath(p, s).replace("\\", "/")
    except (ValueError, OSError):
        return None
    if rel == ".":
        return []
    return [x for x in rel.split("/") if x]


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_head(p, n=3000):
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            with open(p, encoding=enc, errors="replace") as f:
                return f.read(n)
        except OSError:
            continue
    return ""


def dir_title(d):
    """猜这个目录是干什么的：README/AGENTS 首行，或 index.html 的 title"""
    for cand in ("README.md", "readme.md", "AGENTS.md", "README.txt", "index.html"):
        p = os.path.join(d, cand)
        if os.path.isfile(p):
            t = read_head(p)
            for line in t.splitlines():
                line = line.strip().lstrip("#>* ").strip()
                if line and not line.startswith(("http", "![", "<", "|")):
                    return line[:120]
    return ""


def count_files(path):
    """统计一个文件或目录里的文件数与字节数。

    与 inventory 保持同一口径：跳过噪音目录、敏感路径、原子包内部，不跟随符号链接。
    口径不一致会让对账出现假差异。
    """
    if os.path.islink(path):
        return 0, 0
    if os.path.isfile(path):
        try:
            return 1, os.path.getsize(path)
        except OSError:
            return 0, 0
    n, total = 0, 0
    for dp, dn, fn in os.walk(path):
        kept = []
        for d in dn:
            full = os.path.join(dp, d)
            if os.path.islink(full):
                continue
            if is_skip_dir(d):
                continue
            rel_parts = os.path.relpath(full, path).replace("\\", "/").split("/")
            if is_protected(rel_parts):
                continue
            if is_atomic_bundle(d):
                # 原子包按 1 个文件计（与 inventory 一致），不拆开数
                n += 1
                try:
                    total += os.path.getsize(full)
                except OSError:
                    pass
                continue
            kept.append(d)
        dn[:] = kept
        for f in fn:
            fp = os.path.join(dp, f)
            try:
                if os.path.islink(fp):
                    continue
                rel_parts = os.path.relpath(fp, path).replace("\\", "/").split("/")
                if is_protected(rel_parts):
                    continue
                total += os.path.getsize(fp)
                n += 1
            except OSError:
                continue
    return n, total


def recycle_to_bin(path):
    """把文件/目录送进系统回收站（可恢复）。

    没有回收站机制时返回 False —— 调用方改为暂存，不做硬删除。
    """
    if os.name != "nt":
        return False
    env = dict(os.environ)
    env["ORG_TRASH_TARGET"] = os.path.abspath(path)
    ps = (
        "Add-Type -AssemblyName Microsoft.VisualBasic;"
        "$p = $env:ORG_TRASH_TARGET;"
        "if (Test-Path -LiteralPath $p -PathType Container) {"
        "  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
        "    $p,'OnlyErrorDialogs','SendToRecycleBin')"
        "} else {"
        "  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
        "    $p,'OnlyErrorDialogs','SendToRecycleBin')"
        "}"
    )
    for shell in ("powershell", "pwsh"):
        try:
            r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", ps],
                               env=env, capture_output=True, text=True)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


# ---------------- 盘点 ----------------
def cmd_inventory(args):
    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        print("源目录不存在: %s" % src)
        return 2

    entries = []
    total = 0
    total_bytes = 0
    exts = collections.Counter()
    protected_paths = []
    symlinks = []
    bundles = []
    # size -> [rel, ...]，用于两级去重
    by_size = collections.defaultdict(list)

    for dp, dn, fn in os.walk(src, followlinks=False):
        kept = []
        for d in dn:
            full = os.path.join(dp, d)
            rel = os.path.relpath(full, src).replace("\\", "/")
            if os.path.islink(full):
                symlinks.append(rel)
                continue
            if is_skip_dir(d):
                continue
            if is_protected(rel.split("/")):
                # 敏感目录整棵子树排除，不下探
                protected_paths.append(rel)
                continue
            if is_atomic_bundle(d):
                # 原子包整体算 1 个文件，不拆开逐文件统计
                bundles.append(rel)
                try:
                    st = os.stat(full)
                except OSError:
                    st = None
                if st:
                    exts[os.path.splitext(d)[1].lower() or "(无扩展名)"] += 1
                    entries.append({"rel": rel, "size": st.st_size,
                                    "mtime": time_str(st.st_mtime)})
                    by_size[st.st_size].append(rel)
                    total += 1
                    total_bytes += st.st_size
                continue
            kept.append(d)
        dn[:] = kept

        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, src).replace("\\", "/")
            rel_parts = rel.split("/")
            if os.path.islink(p):
                symlinks.append(rel)
                continue
            if is_protected(rel_parts):
                protected_paths.append(rel)
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            exts[os.path.splitext(f)[1].lower() or "(无扩展名)"] += 1
            entries.append({"rel": rel, "size": st.st_size,
                            "mtime": time_str(st.st_mtime)})
            by_size[st.st_size].append(rel)
            total += 1
            total_bytes += st.st_size

    # 两级去重：先按 size 分组，size 相同的再算 sha256
    duplicates = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        if size > args.hash_limit:
            continue
        groups = collections.defaultdict(list)
        for rel in paths:
            try:
                groups[sha256_file(os.path.join(src, rel))].append(rel)
            except OSError:
                continue
        for digest, same in groups.items():
            if len(same) > 1:
                duplicates.append({"sha256": digest, "size": size, "paths": sorted(same)})

    top = []
    for name in sorted(os.listdir(src)):
        p = os.path.join(src, name)
        if not os.path.isdir(p) or is_skip_dir(name) or os.path.islink(p):
            continue
        if is_protected(name.split("/")):
            continue
        if is_atomic_bundle(name):
            # 原子包已单列，不在顶层清单里重复出现
            continue
        sub = [e for e in entries if e["rel"].startswith(name.replace("\\", "/") + "/")]
        if not sub:
            # 目录内容全被排除（敏感/噪音）后变空，不进顶层清单
            continue
        samples = [e["rel"] for e in sub[:8]]
        top.append({
            "name": name,
            "title": dir_title(p),
            "files": len(sub),
            "bytes": sum(e["size"] for e in sub),
            "sample_exts": [e for e, _ in collections.Counter(
                os.path.splitext(x["rel"])[1].lower() for x in sub).most_common(4)],
            "sample_files": samples,
        })

    inv = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "src": src,
        "file_count": total,
        "total_bytes": total_bytes,
        "ext_distribution": exts.most_common(20),
        "top_level": top,
        "files": entries,
        "duplicate_groups": duplicates,
        "protected_paths": sorted(protected_paths),
        "symlinks": sorted(symlinks),
        "atomic_bundles": sorted(bundles),
    }
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(inv, f, ensure_ascii=False, indent=1)

    print("=" * 74)
    print(" 盘点完成: %s" % out)
    print("   文件 %d 个 | %.1f MB | 顶层目录 %d 个" %
          (total, total_bytes / 1048576.0, len(top)))
    print("=" * 74)

    print("\n 顶层目录（AI 据此设计分类轴）:")
    for t in top:
        print("   %-40s %5d 文件  %6.1f MB  %s" % (
            t["name"][:40], t["files"], t["bytes"] / 1048576.0, t["title"][:44]))
    print("\n 扩展名分布:")
    for e, c in inv["ext_distribution"][:12]:
        print("   %-14s %d" % (e, c))
    if duplicates:
        print("\n 同内容重复 %d 组（AI 判断保留哪一份）:" % len(duplicates))
        for d in duplicates[:10]:
            print("   %s  %d 份: %s" % (d["sha256"][:12], d["size"],
                                        ", ".join(d["paths"][:3])))
    if protected_paths:
        print("\n 已排除的敏感路径 %d 处（未读取、未移动）:" % len(protected_paths))
        for p in protected_paths[:8]:
            print("   %s" % p)
    if symlinks:
        print("\n 符号链接 %d 个（当作不透明对象，不跟随）:" % len(symlinks))
        for p in symlinks[:8]:
            print("   %s" % p)
    if bundles:
        print("\n 原子包 %d 个（整体处理，不拆开）:" % len(bundles))
        for p in bundles[:8]:
            print("   %s" % p)
    return 0


def time_str(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# ---------------- 映射校验 ----------------
def load_map(p):
    with open(p, encoding="utf-8") as f:
        m = json.load(f)
    if "moves" not in m:
        raise SystemExit("映射文件缺少 'moves' 数组")
    return m


def validate(mp, args, check_sources=True):
    src = os.path.abspath(mp.get("src") or getattr(args, "src", "") or "")
    root = os.path.abspath(mp.get("root") or getattr(args, "root", "") or "")
    max_depth = mp.get("max_depth", getattr(args, "max_depth", DEFAULT_MAX_DEPTH))
    l1_max = mp.get("l1_max", getattr(args, "l1_max", DEFAULT_L1_MAX))
    l23_max = mp.get("l23_max", getattr(args, "l23_max", DEFAULT_L23_MAX))
    max_path_len = mp.get("max_path_len",
                          getattr(args, "max_path_len", DEFAULT_MAX_PATH_LEN))

    errs, warns = [], []
    children = collections.defaultdict(set)   # 父目录 -> 子项名集合
    seen_src = set()

    for mv in mp["moves"]:
        frm = os.path.abspath(mv["from"])
        to = mv["to"].replace("\\", "/").strip("/")
        if check_sources and not os.path.exists(frm):
            errs.append("源不存在: %s" % mv["from"])
            continue
        if frm.lower() in seen_src:
            warns.append("重复映射同一源: %s" % mv["from"])
        seen_src.add(frm.lower())

        parts = [x for x in to.split("/") if x]
        if not parts:
            errs.append("目标为空: %s" % mv["from"])
            continue
        if any(p == ".." for p in parts):
            errs.append("目标含上级路径 '..'（越界风险）: %s" % to)
            continue
        if len(parts) > max_depth:
            errs.append("超过 %d 层: %s" % (max_depth, to))
        for lvl in range(len(parts)):
            p = "/".join(parts[:lvl])
            c = parts[lvl]
            children[p].add(c)

        # 源自身是否敏感 / 是否符号链接 / 是否在原子包内
        src_rel = rel_to_src(frm, src)
        if src_rel is not None and is_protected(src_rel):
            errs.append("源是敏感路径，不允许移动: %s" % mv["from"])
        if os.path.islink(frm):
            errs.append("源是符号链接，不移动（当作不透明对象）: %s" % mv["from"])
        if src_rel and any(is_atomic_bundle(p) for p in src_rel):
            warns.append("源位于原子包内，应整体处理: %s" % mv["from"])

        # 目标不得落回源内部，或源落在目标内部（会自噬）
        dest = os.path.join(root, *parts) if parts else root
        try:
            if os.path.commonpath([os.path.abspath(dest), frm]) == frm:
                errs.append("目标是源自身或其子目录（自噬）: %s -> %s" % (mv["from"], to))
        except ValueError:
            pass
        try:
            if os.path.commonpath([os.path.abspath(dest), src]) == src and \
               os.path.abspath(dest) != os.path.abspath(src):
                warns.append("目标位于源目录内部: %s" % dest)
        except ValueError:
            pass

        # Windows MAX_PATH 约束：本机 LongPathsEnabled=0，路径超 260 会直接失败
        n = len(dest)
        if n > max_path_len:
            errs.append("目标路径 %d 字符，超过上限 %d（MAX_PATH=260，本机未开长路径）: %s"
                        % (n, max_path_len, dest))
        elif n > max_path_len - 20:
            warns.append("目标路径 %d 字符，接近上限 %d: %s" % (n, max_path_len, dest))

    for parent, kids in children.items():
        limit = l1_max if parent == "" else l23_max
        if len(kids) > limit:
            errs.append("目录 '%s' 有 %d 个子项，超过上限 %d" %
                        (parent or "(根)", len(kids), limit))
    return src, root, errs, warns, children


def cmd_apply(args):
    if not args.map:
        print("需要 --map 映射文件")
        return 2
    mp = load_map(args.map)
    src, root, errs, warns, children = validate(mp, args)

    print("=" * 74)
    print(" 整理映射校验")
    print("=" * 74)
    print("   源根: %s" % src)
    print("   目标根: %s" % root)
    print("   移动条目: %d" % len(mp["moves"]))
    print("   约束: 深度<=%d  第1层<=%d  第2/3层<=%d  路径<=%d字符  禁止删除  不覆盖同名" %
          (mp.get("max_depth", args.max_depth), mp.get("l1_max", args.l1_max),
             mp.get("l23_max", args.l23_max),
             mp.get("max_path_len", getattr(args, "max_path_len", DEFAULT_MAX_PATH_LEN))))
    for w in warns:
        print("   [警告] %s" % w)
    for e in errs:
        print("   [错误] %s" % e)
    if errs:
        print("\n 校验未通过，未做任何改动。")
        return 3

    # 统计源文件总数（用于对账）
    before_files, before_bytes = 0, 0
    for m in mp["moves"]:
        c, b = count_files(os.path.abspath(m["from"]))
        before_files += c
        before_bytes += b
    print("   待移动文件总数: %d（%.1f MB）" % (before_files, before_bytes / 1048576.0))

    if not args.apply:
        print("\n  [未执行] 加 --apply 才会真正移动。")
        print("  预览前 12 条:")
        for m in mp["moves"][:12]:
            print("     %s  ->  %s" % (m["from"], m["to"]))
        return 0

    print("\n" + "=" * 74)
    print(" 执行移动（只移动，不删除；同名自动改名不覆盖）")
    print("=" * 74)
    rollback, collisions, moved_files, moved_bytes = [], [], 0, 0
    for m in mp["moves"]:
        frm = os.path.abspath(m["from"])
        rel = m["to"].replace("\\", "/").strip("/")
        dst = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            base, ext = os.path.splitext(dst)
            i = 1
            while os.path.exists("%s_%d%s" % (base, i, ext)):
                i += 1
            newdst = "%s_%d%s" % (base, i, ext)
            collisions.append({"target": dst, "renamed_to": newdst})
            dst = newdst
        try:
            if os.path.isfile(frm) and os.path.getsize(frm) <= args.hash_limit:
                digest = sha256_file(frm)
            else:
                digest = ""
            shutil.move(frm, dst)
            c, b = count_files(dst)
            moved_files += c
            moved_bytes += b
            rollback.append({"to": dst, "from": frm, "files": c, "bytes": b,
                             "sha256": digest})
        except Exception as e:
            print("   [失败] %s : %s" % (m["from"], e))

    print("   移动完成 %d 条 | 实际落盘文件 %d | 同名改名 %d 处" %
          (len(rollback), moved_files, len(collisions)))

    # 对账
    print("\n--- 对账 ---")
    after_files, after_bytes = 0, 0
    for r in rollback:
        c, b = count_files(r["to"])
        after_files += c
        after_bytes += b
    ok = (after_files == before_files) and (after_bytes == before_bytes)
    print("   移动前 %d 文件 / %.1f MB" % (before_files, before_bytes / 1048576.0))
    print("   落盘后 %d 文件 / %.1f MB -> %s" %
          (after_files, after_bytes / 1048576.0, "一致 ✓" if ok else "不一致 ✗"))
    # 源端残留
    leftover = []
    for m in mp["moves"]:
        frm = os.path.abspath(m["from"])
        if os.path.exists(frm):
            c, _ = count_files(frm)
            leftover.append((m["from"], c))
    if leftover:
        print("   源端残留 %d 处（未被映射覆盖，**未被删除**）:" % len(leftover))
        for p, c in leftover[:15]:
            print("      %s  (%s 文件)" % (p, c))
    else:
        print("   源端无残留")

    rbp = os.path.abspath(args.rollback_map or (os.path.splitext(args.map)[0] + ".rollback.json"))
    with open(rbp, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                   "root": root, "entries": rollback,
                   "collisions": collisions}, f, ensure_ascii=False, indent=1)
    print("   回滚映射表: %s" % rbp)
    return 0 if ok else 4


def cmd_verify(args):
    mp = load_map(args.map)
    # 落地后源目录已被移走，因此这里只校验结构约束，不校验源是否存在
    src, root, errs, warns, _ = validate(mp, args, check_sources=False)
    print("=" * 74)
    print(" 对账")
    print("=" * 74)
    for e in errs:
        print("   [错误] %s" % e)
    if errs:
        return 3
    rbp = os.path.abspath(args.rollback_map) if args.rollback_map else \
        os.path.splitext(os.path.abspath(args.map))[0] + ".rollback.json"
    if not os.path.isfile(rbp):
        print("   找不到回滚映射表: %s" % rbp)
        return 2
    with open(rbp, encoding="utf-8") as f:
        rb = json.load(f)
    miss, ok, bytes_ok = [], 0, 0
    for e in rb["entries"]:
        if os.path.exists(e["to"]):
            ok += 1
            c, b = count_files(e["to"])
            if c == e.get("files", c) and b == e.get("bytes", b):
                bytes_ok += 1
        else:
            miss.append(e["to"])
    print("   目标存在 %d / %d" % (ok, len(rb["entries"])))
    print("   文件数与字节数吻合 %d / %d" % (bytes_ok, len(rb["entries"])))
    if miss:
        for p in miss[:15]:
            print("      [缺失] %s" % p)
    # 抽样哈希
    sample = rb["entries"][:min(10, len(rb["entries"]))]
    for e in sample:
        if os.path.isfile(e["to"]) and e.get("sha256"):
            try:
                now = sha256_file(e["to"])
                flag = "✓" if now == e["sha256"] else "✗"
                print("   %s %s  %s" % (now[:12], flag, os.path.basename(e["to"])[:50]))
            except OSError:
                pass
    return 0 if not miss else 5


def cmd_rollback(args):
    rbp = os.path.abspath(args.rollback_map)
    with open(rbp, encoding="utf-8") as f:
        rb = json.load(f)
    if not args.apply:
        print("  [未执行] 加 --apply 执行回滚。共 %d 条。" % len(rb["entries"]))
        return 0
    n = 0
    for e in reversed(rb["entries"]):
        if os.path.exists(e["to"]) and not os.path.exists(e["from"]):
            os.makedirs(os.path.dirname(e["from"]), exist_ok=True)
            shutil.move(e["to"], e["from"])
            n += 1
    print("  已回滚 %d 条。" % n)
    return 0


def cmd_clean(args):
    """删除阶梯：只进暂存区或回收站，没有硬删除路径。

    默认（不加 --apply）只列清单；加 --apply 移入 _trash-candidates/；
    再加 --recycle 才送系统回收站（可恢复）。任何情况下都不硬删。
    """
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("目标根不存在: %s" % root)
        return 2
    targets = []
    with open(os.path.abspath(args.targets), encoding="utf-8") as f:
        raw = f.read()
    stripped = raw.lstrip()
    if stripped.startswith("["):
        targets = [str(x) for x in json.loads(raw)]
    else:
        targets = [ln.strip() for ln in raw.splitlines()
                   if ln.strip() and not ln.strip().startswith("#")]

    resolved = []
    for t in targets:
        p = os.path.abspath(os.path.join(root, t.replace("/", os.sep)))
        if not os.path.exists(p):
            print("   [跳过] 不存在: %s" % t)
            continue
        rel_parts = rel_to_src(p, root)
        if rel_parts is None:
            print("   [跳过] 越出目标根: %s" % t)
            continue
        if is_protected(rel_parts):
            print("   [跳过] 敏感路径，不处理: %s" % t)
            continue
        resolved.append((t, p))

    print("=" * 74)
    print(" 清理清单：%d 项（目标根 %s）" % (len(resolved), root))
    print("=" * 74)
    for t, p in resolved:
        c, b = count_files(p)
        print("   %-52s %4d 文件  %7.1f MB" % (t[:52], c, b / 1048576.0))

    if not args.apply:
        print("\n  [未执行] 加 --apply 移入 %s/；再加 --recycle 送系统回收站。" % TRASH_DIRNAME)
        print("  本工具没有硬删除路径——所有清理都可恢复。")
        return 0

    if args.recycle:
        sent, failed = 0, []
        for t, p in resolved:
            if recycle_to_bin(p):
                sent += 1
            else:
                failed.append(t)
        print("   已送回收站 %d 项" % sent)
        if failed:
            print("   无回收站机制，改留原地 %d 项: %s" % (len(failed), ", ".join(failed[:5])))
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    stage = os.path.join(root, TRASH_DIRNAME, stamp)
    os.makedirs(stage, exist_ok=True)
    origins = []
    for t, p in resolved:
        dest = os.path.join(stage, t.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            shutil.move(p, dest)
            origins.append((t, dest))
        except Exception as e:
            print("   [失败] %s : %s" % (t, e))
    with open(os.path.join(stage, "_origins.md"), "w", encoding="utf-8") as f:
        f.write("# 暂存来源（%s）\n\n" % stamp)
        for t, d in origins:
            f.write("- `%s` -> `%s`\n" % (t, d))
    print("   已暂存 %d 项到: %s" % (len(origins), stage))
    print("   来源记录: %s" % os.path.join(stage, "_origins.md"))
    print("   确认后用 --recycle 送回收站，或手动移回。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="资料整理执行器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("inventory", help="盘点源目录")
    i.add_argument("--src", required=True)
    i.add_argument("--out", required=True)
    i.add_argument("--hash-limit", type=int, default=DEFAULT_HASH_LIMIT,
                   help="计算 sha256 的单文件大小上限（字节），默认 %d" % DEFAULT_HASH_LIMIT)
    i.set_defaults(func=cmd_inventory)

    a = sub.add_parser("apply", help="按映射执行移动")
    a.add_argument("--src", required=True)
    a.add_argument("--map", required=True)
    a.add_argument("--root", default="")
    a.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    a.add_argument("--l1-max", type=int, default=DEFAULT_L1_MAX)
    a.add_argument("--l23-max", type=int, default=DEFAULT_L23_MAX)
    a.add_argument("--max-path-len", type=int, default=DEFAULT_MAX_PATH_LEN)
    a.add_argument("--hash-limit", type=int, default=DEFAULT_HASH_LIMIT)
    a.add_argument("--apply", action="store_true")
    a.add_argument("--rollback-map", default="")
    a.set_defaults(func=cmd_apply)

    v = sub.add_parser("verify", help="对账")
    v.add_argument("--src", required=True)
    v.add_argument("--map", required=True)
    v.add_argument("--rollback-map", default="")
    v.add_argument("--max-path-len", type=int, default=DEFAULT_MAX_PATH_LEN)
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("rollback", help="回滚")
    r.add_argument("--rollback-map", required=True)
    r.add_argument("--apply", action="store_true")
    r.set_defaults(func=cmd_rollback)

    c = sub.add_parser("clean", help="清理垃圾：暂存区 / 回收站，无硬删除")
    c.add_argument("--root", required=True)
    c.add_argument("--targets", required=True, help="待清理清单（每行一个路径，或 JSON 数组）")
    c.add_argument("--apply", action="store_true", help="真正移入暂存区")
    c.add_argument("--recycle", action="store_true", help="直接送系统回收站（需同时 --apply）")
    c.set_defaults(func=cmd_clean)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
