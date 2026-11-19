"""Merge the upstream main branch only after checking existing Chinese text in the merge tree."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = "https://github.com/CaduGimenes/SanctuaryPort.git"
CATALOGS = (
    "localization/zh-CN/launcher.json",
    "localization/zh-CN/trophy-ui.json",
    "assets/trophies/Diablo_III.json",
    "recomplauncher.json",
)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8").strip()


def phrases(value, path=()):
    """Use trophy/setting IDs and choice values so that upstream may reorder entries."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from phrases(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            identity = index
            if isinstance(child, dict):
                identity = child.get("id", index)
                if "set" in child:
                    identity = json.dumps(child["set"], sort_keys=True)
            yield from phrases(child, (*path, str(identity)))
    elif isinstance(value, str) and re.search(r"[\u3400-\u9fff]", value):
        yield path, value


def texts(revision, file):
    data = json.loads(git("show", f"{revision}:{file}"))
    if file.startswith("localization/"):
        return {(key,): value for key, value in data.items()}
    return dict(phrases(data))


def main():
    if git("branch", "--show-current") != "zh-CN" or git("status", "--porcelain"):
        sys.exit("请先切换至 zh-CN，并提交或暂存到 stash 后再同步。")
    git("fetch", "--no-tags", UPSTREAM, "refs/heads/main")
    upstream = git("rev-parse", "FETCH_HEAD")
    if git("merge-base", "HEAD", upstream) == upstream:
        print("汉化分支已包含上游最新提交。")
        return
    # merge-tree writes only Git objects. A conflict leaves HEAD and the working tree intact.
    tree = git("merge-tree", "--write-tree", "HEAD", upstream).splitlines()[0]
    for file in CATALOGS:
        before, after = texts("HEAD", file), texts(tree, file)
        for key, text in before.items():
            if after.get(key) != text:
                sys.exit(f"上游合并会更改现有译文，已停止同步。{file}  {' / '.join(key)}")
    subprocess.run(["git", "merge", "--no-ff", "--no-edit", upstream], cwd=ROOT, check=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        if error.output:
            print(error.output.decode("utf-8", errors="replace"), file=sys.stderr)
        sys.exit(error.returncode)
