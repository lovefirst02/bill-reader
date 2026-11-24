"""A site's own reader, for bills that are not Taipower's, such as a landlord's.

A plugin is a .py file named in sites.csv's Plugin column, with one function:

    def read(path, site):
        return None          # not this plugin's file: read it as a Taipower bill
        return []            # this plugin's file, holding no bill
        return [{...}, ...]  # one dict per bill, keyed by the master's column names

Raising fails the file, which is tried again like any file that fails. The exe
runs a plugin with the Python inside it, so a plugin may import the standard
library and what the exe holds (pdfplumber, pandas, numpy, openpyxl, Pillow, cv2).
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

from .master import ID, MASTER_COLUMNS, PAGE, PERIOD, METER, READ_AT, SITE, SOURCE
from .record import FIGURE_COLUMNS, _is_number

# A site with a plugin also has these files listed; one the plugin leaves is not a bill.
PLUGIN_SUFFIXES = (".xlsx", ".xlsm", ".xls", ".csv")
# Filled in by the watcher, whatever a plugin says.
OWN_COLUMNS = (ID, SITE, SOURCE, READ_AT)


class PluginError(Exception):
    """A plugin that cannot be loaded, failed, or answered something that is not rows."""


_loaded: dict[Path, ModuleType] = {}


def load(path: Path) -> ModuleType:
    """The plugin at ``path``, loaded once."""
    path = path.resolve()
    if path in _loaded:
        return _loaded[path]
    name = "bill_reader_plugin_" + hashlib.sha1(str(path).lower().encode()).hexdigest()[:8]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PluginError(f"{path.name} 不是 Python 檔")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:  # noqa: BLE001 - whatever the plugin raises
        del sys.modules[name]
        raise PluginError(f"{path.name} 載入失敗：{_where(error, path)}") from error
    if not callable(getattr(module, "read", None)):
        del sys.modules[name]
        raise PluginError(f"{path.name} 沒有 read(path, site) 函式")
    _loaded[path] = module
    return module


def _where(error: BaseException, path: Path) -> str:
    """The error, with the plugin's line it came from."""
    frames = [frame for frame in traceback.extract_tb(error.__traceback__) if Path(frame.filename).resolve() == path.resolve()]
    line = f"第 {frames[-1].lineno} 行 " if frames else ""
    return f"{line}{type(error).__name__}: {error}"


def read_rows(plugin: Path, path: Path, site: str) -> list[dict[str, Any]] | None:
    """What the plugin read from ``path``: None when it leaves the file to the Taipower reader.

    Each row keeps the plugin's keys, with 頁碼 set: 1, 2, ... in order unless the plugin gave them.
    """
    module = load(plugin)
    try:
        answer = module.read(path, site)
    except Exception as error:  # noqa: BLE001
        raise PluginError(f"{plugin.name}：{_where(error, plugin)}") from error
    if answer is None:
        return None
    if not isinstance(answer, (list, tuple)):
        raise PluginError(f"{plugin.name}：read() 要回傳 list 或 None，不是 {type(answer).__name__}")
    rows: list[dict[str, Any]] = []
    for number, row in enumerate(answer, start=1):
        if not isinstance(row, dict):
            raise PluginError(f"{plugin.name}：第 {number} 列要是 dict，不是 {type(row).__name__}")
        for column in (PERIOD, METER):
            if not str(row.get(column) or "").strip():
                raise PluginError(f"{plugin.name}：第 {number} 列沒有「{column}」")
        for column in FIGURE_COLUMNS:
            value = row.get(column)
            if value is not None and not _is_number(value):
                raise PluginError(f"{plugin.name}：第 {number} 列「{column}」要是數字，不是 {value!r}")
        for key, value in row.items():
            if not isinstance(key, str):
                raise PluginError(f"{plugin.name}：第 {number} 列的欄名要是文字，不是 {key!r}")
            if value is not None and not isinstance(value, (str, int, float)):
                raise PluginError(f"{plugin.name}：第 {number} 列「{key}」要是文字或數字，不是 {type(value).__name__}")
        page = row.get(PAGE, number)
        if not isinstance(page, int) or isinstance(page, bool):
            raise PluginError(f"{plugin.name}：第 {number} 列「{PAGE}」要是整數，不是 {page!r}")
        rows.append({key: value for key, value in row.items() if key not in OWN_COLUMNS} | {PAGE: page})
    pages = [row[PAGE] for row in rows]
    if len(set(pages)) != len(pages):
        raise PluginError(f"{plugin.name}：「{PAGE}」有重複，每一列要不同")
    return rows


def extra_columns(row: dict[str, Any]) -> list[str]:
    """A plugin's keys the master does not have unless someone adds the column by hand."""
    return [key for key in row if key not in MASTER_COLUMNS]
