# SanctuaryPort 简体中文汉化

汉化作者：**A-FEI-** · [GBTGAME 论坛](https://bbs.gbtgame.me)

本分支**只保存汉化本身**，不含任何上游代码，也不修改上游的任何文件。上游更新由
`main` 分支自动跟随，汉化在**构建时**注入到一份干净的上游检出里，因此永远不会有合并冲突。

## 结构

```
data/launcher.json     启动器界面词条（英文 → 中文），构建时合并进 assets/i18n.json 的 extra.zh
data/trophies.json     奖杯名与说明（按奖杯 id），构建时写进 assets/trophies/Diablo_III.json
build/apply.py         把上面两份数据应用到一份上游检出
build/package.py       把编译好的启动器打成更新包（SanctuaryPort.exe + update.json）
dll/                   游戏内署名的运行时替换（SDL3 代理 DLL，构建中）
```

启动器的界面文案和奖杯表是 `include_str!` **编译进 exe** 的，运行时没有文件可读，
所以汉化只能发生在构建阶段；这也是本分支只保存数据、由构建去注入的原因。

## 自动流程

| 工作流 | 作用 |
|---|---|
| `.github/workflows/sync-upstream.yml` | 每 6 小时把 `main` 强制同步到 `CaduGimenes/SanctuaryPort` 的最新提交，并在有变化时触发汉化构建 |
| `.github/workflows/localization.yml` | 每天/手动：取上游最新 release 标签 → 克隆该标签 → 应用汉化 → 编译启动器 → 发布 `zh-CN-v<版本>-zh<修订>` |

更新包只含启动器（约 20 MB）。`run.py`、`scripts/`、`bin/` 属于端口发布，不能跨版本混装。

## 手动重建

翻译改了以后，用新修订号重新发布同一上游版本：

```
Actions → localization → Run workflow → revision: 2
```

只有 `zh-CN-v<版本>-zh<修订>` 尚未发布时才会构建，所以重复触发是安全的。
