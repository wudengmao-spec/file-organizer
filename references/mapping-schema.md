# 文件格式说明

## map.json —— 语义与机械之间的接口

由你（语义判断方）写，脚本（机械约束方）读。

```json
{
  "src": "D:\\杂乱的下载目录",
  "root": "D:\\归集",
  "max_depth": 3,
  "l1_max": 7,
  "l23_max": 3,
  "max_path_len": 250,
  "moves": [
    { "from": "D:\\杂乱的下载目录\\claude-code-skill", "to": "01-工具/开发/claude-code-skill" },
    { "from": "D:\\杂乱的下载目录\\2024年报.pdf", "to": "02-文档/财务/2024年报.pdf" }
  ]
}
```

| 字段 | 必需 | 说明 |
|---|---|---|
| `src` | 是 | 源根目录。`from` 里的相对路径以此为基准 |
| `root` | 是 | 目标根目录。`to` 拼在它下面 |
| `moves` | 是 | 移动清单，数组 |
| `max_depth` | 否 | 默认 3 |
| `l1_max` | 否 | 默认 7 |
| `l23_max` | 否 | 默认 3 |
| `max_path_len` | 否 | 默认 250 |

`moves[]` 每项：

| 字段 | 说明 |
|---|---|
| `from` | 源路径。可以是单文件，也可以是整个目录 |
| `to` | 相对 `root` 的目标路径，用 `/` 分隔，不能含 `..` |

**`from` 用绝对路径。** 脚本按 `os.path.abspath(mv["from"])` 解析，相对路径会
按当前工作目录而不是 `src` 解析，写相对路径会搬错文件。

## 扇出上限是"逐节点"语义

`l1_max` / `l23_max` 约束的是**每个目录的直接子项数**，不是"整层总数"。

```
✅ 根/            4 个子目录  （4 ≤ 7）
   A/            3 个子目录  （3 ≤ 3）
   B/            3 个子目录  （3 ≤ 3）
   C/            2 个子目录  （2 ≤ 3）
```

整棵树的第 2 层可以有 3+3+2 = 8 个目录 —— 只要没有一个父目录超过 3 个子项。

**如果按"整层总数 ≤3"理解**，根下 4 个语义域就得被迫并进 3 个桶，分类质量直接崩。
所以 `l1_max=7` 给根放宽到 7，第 2/3 层严格 3。

第 1 层子项数 = 映射里所有 `to` 的第一段去重后的个数。
第 N 层子项数 = 同一个父路径下，第 N 段的不同取值个数。

**不要放宽约束参数来绕过校验。** `max_depth: 5` 能让校验通过，但违约的是约定
本身 —— 校验器是守门人，不是障碍。

顶层字段也可以被命令行同名参数覆盖（`--max-depth` 等），命令行优先。

## inventory.json —— 盘点的结构化结果

脚本生成，你只读。

```json
{
  "generated_at": "2026-10-24T20:30:00",
  "src": "D:\\杂乱的下载目录",
  "file_count": 128,
  "total_bytes": 48239104,
  "ext_distribution": [[".pdf", 23], [".png", 41], [".md", 18]],
  "top_level": [
    {
      "name": "claude-code-skill",
      "title": "File Organizer Skills for Claude Code & Cowork",
      "files": 12,
      "bytes": 48123,
      "sample_exts": [".md", ".json"],
      "sample_files": ["claude-code-skill/SKILL.md", "claude-code-skill/README.md"]
    }
  ],
  "files": [
    { "rel": "claude-code-skill/SKILL.md", "size": 11813, "mtime": "2026-01-13" }
  ],
  "duplicate_groups": [
    { "sha256": "a1b2c3...", "size": 20480, "paths": ["a/report.pdf", "b/report.pdf"] }
  ],
  "protected_paths": [".env", "config/credentials.json"],
  "symlinks": ["shortcut-to-drive"],
  "atomic_bundles": ["Photos.photoslibrary"]
}
```

| 字段 | 用途 |
|---|---|
| `top_level[].title` | **设计分类轴的首要依据**。目录自带的 README/AGENTS 首行 |
| `top_level[].sample_files` | 顶层目录里的样例文件名，帮助你判断归属 |
| `top_level[].sample_exts` | 格式分布，旁证 |
| `duplicate_groups` | 同内容重复组。保留哪份是判断题 |
| `protected_paths` | 被排除的敏感路径（未读取未移动） |
| `symlinks` | 符号链接（未跟随） |
| `atomic_bundles` | 原子包（整体处理） |

## rollback.json —— 回滚契约

`apply --apply` 自动生成在 `map.json` 同目录（`map.rollback.json`）。

```json
{
  "generated_at": "2026-10-24T20:35:00",
  "root": "D:\\归集",
  "entries": [
    { "to": "D:\\归集\\01-工具\\开发\\claude-code-skill",
      "from": "D:\\杂乱的下载目录\\claude-code-skill",
      "files": 12, "bytes": 48123, "sha256": "" }
  ],
  "collisions": [
    { "target": "D:\\归集\\02-文档\\财务\\年报.pdf", "renamed_to": "D:\\归集\\02-文档\\财务\\年报_1.pdf" }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `entries[]` | 每条成功移动的记录。回滚按逆序执行 |
| `entries[].files` / `bytes` | 移动后统计，对账用 |
| `entries[].sha256` | 单文件移动时记录移动前哈希；目录移动为空 |
| `collisions[]` | 同名自动改名的记录 |

**这个文件是唯一的回滚依据。** 整理完成后提醒用户它的位置 —— 用户过两天
发现结构不对，全靠它。

## junk.txt —— 清理清单

`clean` 子命令的输入，两种格式都支持：

```text
# 注释行会被忽略
安装包/setup_v3.exe
旧版本/setup_v2.exe
```

```json
["安装包/setup_v3.exe", "旧版本/setup_v2.exe"]
```

路径相对 `--root` 指定的目标根。

**只写高置信度垃圾**：零字节文件、确认过的孤立安装包、已验证保留副本的精确重复。
有疑问的留在原地 —— 清理的收益远小于误删的代价。
