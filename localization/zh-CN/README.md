# SanctuaryPort 简体中文启动器

汉化作者 **A-FEI-** · [GBTGAME 论坛](https://bbs.gbtgame.me)

适用于已有的 SanctuaryPort Windows 游戏目录。首次安装时，把汉化发布包中的 `SanctuaryPort.exe` 解压到现有启动器所在目录。以后可在首页右上角的版本菜单或「高级 → 检查更新」安装新版。启动时检查更新由「高级」中的开关控制。

更新器通过 GitHub API 获取本分支的发布记录，从同一个 ZIP 并行试读最多 256 KiB，对 GitHub 直连、GHFast、GH-Proxy 按实际吞吐量排序。完整下载失败或 SHA-256 与 GitHub 的记录不符时，自动尝试下一条线路。线路速度随网络变化，每次下载重新测量。版本查询需要能访问 GitHub API。

Windows 汉化更新只安装启动器。游戏内菜单使用原有汉化运行库，剧情和语音使用当前游戏目录中的语言包。单独安装此 ZIP 需要已有运行环境。

## 跟进上游

在 `zh-CN` 分支运行（需要 Git 2.38 或更高版本）：

```powershell
python -X utf8 localization/zh-CN/sync.py
```

同步工具先预演合并，按键核对启动器词表、奖杯及外部启动器设置的已有译文。遇到合并冲突或译文变化时列出问题并停止，分支仍保持在同步前的提交。合并后核对上游改名或新增的英文条目，并运行以下命令测试和构建：

```powershell
python -X utf8 launcher-rust/tools/i18n_audit.py zh
cargo test --manifest-path launcher-rust/Cargo.toml --locked --release --lib core::update
python localization/zh-CN/package.py --revision 1 --out dist
```

同一上游版本的汉化修订号依次递增。构建脚本输出 ZIP、校验文件和发布标签，例如 `zh-CN-v0.6.7-zh1`。把汉化分支推送到自己的 fork，并使用这个标签在汉化提交上发布这两个附件。更新器识别名为 `SanctuaryPort-launcher-zh-CN.zip` 的附件。

启动器词条保存在 `localization/zh-CN/launcher.json`，奖杯页面词条在 `trophy-ui.json`。查找时优先采用分支译文，新条目可沿用上游翻译。奖杯内容位于 `assets/trophies/Diablo_III.json`，外部启动器设置位于 `recomplauncher.json`。
