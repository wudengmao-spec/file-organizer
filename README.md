# file-organizer

给 AI 编程助手用的文件整理 Skill：把杂乱目录整理成固定层级的可检索树。

**核心分工：语义判断归 AI，机械约束归脚本。**
分类轴怎么定、哪个文件属于哪类、哪些是重复、哪些是垃圾 —— 由 AI 判断并写进
`map.json`；深度、层宽、路径长度、禁删、同名不覆盖、敏感路径 —— 由脚本强制
校验，不通过就拒绝执行。

## 约束

| 约束 | 值 |
|---|---|
| 层级深度 | ≤ 3 |
| 第 1 层子项数 | ≤ 7 |
| 第 2/3 层子项数 | ≤ 3 |
| 目标路径长度 | ≤ 250 字符（Windows MAX_PATH 保护） |
| 删除 | 禁止（清理只走暂存区 / 回收站，均可恢复） |
| 同名覆盖 | 禁止（自动改名并记录，事后可回滚） |
| 敏感路径 | 完全排除（`.env`、密钥、证书、浏览器 profile） |

## 安装

```bash
git clone https://github.com/wudengmao-spec/file-organizer.git
cp -r file-organizer ~/.minimax/skills/file-organizer
```

Windows（PowerShell）：

```powershell
git clone https://github.com/wudengmao-spec/file-organizer.git
Copy-Item -Recurse file-organizer "$env:USERPROFILE\.minimax\skills\file-organizer"
```

装完重启 AI 助手即可生效。

## 用法

```
帮我整理一下 D:\杂乱的下载目录
```

AI 会走完整流程：盘点 → 设计层级 → 写映射 → 校验 → 执行 → 对账。
也可以直接手动调用脚本：

```bash
# 盘点（只读）
python scripts/organize.py inventory --src "<目录>" --out "<工作目录>/inventory.json"

# 校验映射（只读，不改任何文件）
python scripts/organize.py apply --src "<目录>" --map "<工作目录>/map.json"

# 执行（落地后自动对账并生成回滚表）
python scripts/organize.py apply --src "<目录>" --map "<工作目录>/map.json" --apply

# 对账 / 回滚
python scripts/organize.py verify --src "<目录>" --map "<工作目录>/map.json"
python scripts/organize.py rollback --rollback-map "<工作目录>/map.rollback.json" --apply

# 清理垃圾（默认进暂存区，不删）
python scripts/organize.py clean --root "<目标根>" --targets "<工作目录>/junk.txt"
```

## 目录结构

```
file-organizer/
├── SKILL.md                      # 主文件：语义决策流程 + 硬约束
├── scripts/
│   ├── organize.py               # 执行器：约束校验 / 移动 / 对账 / 回滚 / 清理
│   └── test_organize.py          # 端到端测试（42 项断言）
└── references/
    ├── hierarchy-rules.md        # 层级设计规则
    ├── safety-rules.md           # 安全边界详解
    ├── mapping-schema.md         # map.json / inventory.json 字段说明
    └── eval-notes.md             # 验证记录（存根）
```

## 测试

```bash
python scripts/test_organize.py
```

覆盖：盘点计数、敏感路径排除、符号链接、原子包、同内容去重，以及
超层宽 / 超深度 / 超长路径 / 敏感源 / 越界目标五类约束拦截，
执行后文件数与字节数守恒，整批回滚，同名不覆盖，清理暂存区可恢复。

## 设计要点

- **按"你会怎么找"分，不按"它是什么格式"分。** 找文件时想的是"项目A的封面图"，
  不是"哪张 png"。格式只能是最内层的区分手段，且要让位于项目归属。
- **项目目录不拆散。** 自带 README/AGENTS 的目录整体归位，内部文件不按类型抽散。
- **证据不足就别猜。** 读不出语义线索的项留在原地列进待定清单，
  不塞进某个像样的桶里 —— 错误的归类比不归类代价更高。
- **敏感路径是被保护，不是被丢弃。** 盘点阶段即排除，未读取、未修改、未移动。
- **没有硬删除路径。** 清理只走暂存区或系统回收站，全程可恢复。

## License

MIT
