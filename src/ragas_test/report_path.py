"""评测报告输出路径约定（2026-09-19 起）。

背景：此前所有评测脚本都把 `*_report.json` 直接写到 `src/ragas_test/` 根目录，
十几个脚本 + 历史报告混在一起，分不清哪份是当天的、哪份是过期的。
现约定：

    src/ragas_test/reports/<YYYY-MM-DD>/<脚本名>_report.json

- 脚本默认写当天目录（`date.today()`），跑多次会覆盖当天同名文件；
  需要保留多版本时显式传 `--out-dir` 或 `--tag`（后者由脚本自行拼文件名）。
- `reports/` 下只放**生成物**，脚本源码仍在 `src/ragas_test/` 根目录。
- 2026-09-19 之前散落在根目录的报告已归入 `reports/legacy/`，不再原地更新。

用法：
    from ragas_test.report_path import resolve_report_path
    out = resolve_report_path("cache_hitrate_eval_report.json")
"""

from datetime import date
from pathlib import Path

REPORTS_ROOT = Path(__file__).parent / "reports"


def resolve_report_path(filename: str, out_dir: str | None = None, day: str | None = None) -> Path:
    """返回报告落盘路径，并确保目录存在。

    Args:
        filename: 报告文件名（不含目录）
        out_dir: 显式指定输出目录时优先使用
        day: 归档日期（YYYY-MM-DD），默认今天
    """
    if out_dir:
        target_dir = Path(out_dir)
    else:
        target_dir = REPORTS_ROOT / (day or date.today().isoformat())
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / filename
