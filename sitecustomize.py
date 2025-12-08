"""
Lightweight runtime patches for third-party libraries used in the app.

This is loaded automatically by Python when present on sys.path. Keep changes
minimal and side-effect free.
"""

# Allow code that still calls .save() on XlsxWriter objects (older pandas examples)
try:  # pragma: no cover - defensive monkeypatch
    import xlsxwriter.workbook as _wb

    if not hasattr(_wb.Workbook, "save"):
        _wb.Workbook.save = _wb.Workbook.close
except Exception:
    pass

try:  # pragma: no cover
    import pandas.io.excel._xlsxwriter as _pwx

    for _cls_name in ("XlsxWriter", "_XlsxWriter"):
        _cls = getattr(_pwx, _cls_name, None)
        if _cls and not hasattr(_cls, "save") and hasattr(_cls, "close"):
            _cls.save = _cls.close
except Exception:
    pass
