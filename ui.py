import asyncio
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QThread, QTimer, QSettings, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from items import (
    BM_ENCHANTS,
    BM_TIERS,
    CITIES,
    ENCHANTS,
    QUALITIES,
    RAW_TO_REFINED,
    RESOURCE_ENCHANTS,
    SLOT_GROUPS,
    TIERS,
)
from scanner import scan, scan_black_market, scan_gather, scan_resource_haul

ORG = "AlbionMarket"
APP = "AlbionMarket"
settings = QSettings(ORG, APP)


def _fmt_silver(n: int) -> str:
    return f"{n:,}"


def _fmt_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{seconds / 3600:.1f}h"


def _abs_timestamp(age_seconds: float) -> str:
    ts = datetime.now() - timedelta(seconds=age_seconds)
    return ts.strftime("%Y-%m-%d %H:%M")


def _load_bool(key: str, default: bool) -> bool:
    val = settings.value(key, default)
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("1", "true", "yes")


class NumericItem(QTableWidgetItem):
    """A cell that shows formatted text but sorts by a numeric key.

    QTableWidgetItem collapses Qt.EditRole into Qt.DisplayRole, so the usual
    setData(EditRole, n) trick replaces the formatted string ("1,234") with the
    bare number. Keeping the sort key in UserRole and comparing it in __lt__
    preserves the display text. A None key (no data) sorts to the bottom.
    """

    def __init__(self, text: str, sort_key):
        super().__init__(text)
        self.setData(Qt.UserRole, sort_key)
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):
        a = self.data(Qt.UserRole)
        b = other.data(Qt.UserRole) if isinstance(other, QTableWidgetItem) else None
        a = float("-inf") if a is None else a
        b = float("-inf") if b is None else b
        return a < b


# ---------- Workers ----------


class RefiningWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, cities: list[str]):
        super().__init__()
        self.cities = cities

    def run(self):
        try:
            self.finished_ok.emit(asyncio.run(scan(cities=self.cities)))
        except Exception as e:
            self.failed.emit(str(e))


class BlackMarketWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, source_cities, tiers, enchants, min_margin):
        super().__init__()
        self.source_cities = source_cities
        self.tiers = tiers
        self.enchants = enchants
        self.min_margin = min_margin

    def run(self):
        try:
            self.finished_ok.emit(
                asyncio.run(
                    scan_black_market(
                        source_cities=self.source_cities,
                        tiers=self.tiers,
                        enchants=self.enchants,
                        min_margin=self.min_margin,
                    )
                )
            )
        except Exception as e:
            self.failed.emit(str(e))


class ResourceHaulWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, cities: list[str]):
        super().__init__()
        self.cities = cities

    def run(self):
        try:
            self.finished_ok.emit(asyncio.run(scan_resource_haul(cities=self.cities)))
        except Exception as e:
            self.failed.emit(str(e))


class GatherWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, cities: list[str]):
        super().__init__()
        self.cities = cities

    def run(self):
        try:
            self.finished_ok.emit(asyncio.run(scan_gather(cities=self.cities)))
        except Exception as e:
            self.failed.emit(str(e))


# ---------- Refining detail dialog ----------


class RefiningDetailDialog(QDialog):
    def __init__(self, parent, row: dict):
        super().__init__(parent)
        self.setWindowTitle(row["label"])
        self.resize(520, 420)
        layout = QVBoxLayout(self)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self._build_text(row))
        layout.addWidget(text)

    @staticmethod
    def _build_text(r: dict) -> str:
        if r.get("margin") is None:
            return RefiningDetailDialog._build_incomplete_text(r)
        lines = [f"=== {r['label']} ===", ""]
        lines.append(f"Recipe: {r['raws_per']} raw + {r['prev_per']} prev-tier refined")
        lines.append(f"Refine city: {r['refine_city']} ({r['refine_note']})")
        lines.append(f"Return rate (no focus): {r['return_rate'] * 100:.1f}%")
        lines.append("")
        lines.append("--- Inputs ---")
        lines.append(
            f"Raw: buy {r['raws_per']}x in {r['buy_city']} @ {r['buy_price']:,} = "
            f"{r['raws_per'] * r['buy_price']:,} silver"
        )
        if r["prev_per"] > 0:
            lines.append(
                f"Prev-tier refined: buy {r['prev_per']}x in {r['prev_buy_city']} @ "
                f"{r['prev_buy_price']:,} = {r['prev_per'] * r['prev_buy_price']:,} silver"
            )
        lines.append(f"Gross input cost: {int(round(r['gross_input'])):,} silver")
        lines.append(
            f"Effective input cost (after {r['return_rate'] * 100:.1f}% return): "
            f"{int(round(r['effective_input'])):,} silver"
        )
        lines.append("")
        lines.append("--- Output ---")
        lines.append(f"Sell 1x refined in {r['sell_city']} @ {r['sell_price']:,} silver")
        lines.append("")
        lines.append(f"NET MARGIN per refined unit: {r['margin']:,} silver")
        lines.append("")
        lines.append("--- Route ---")
        route = [f"1. Buy raws in {r['buy_city']}"]
        if r["prev_per"] > 0 and r["prev_buy_city"] != r["buy_city"]:
            route.append(f"2. Buy prev-tier refined in {r['prev_buy_city']}")
        n = len(route) + 1
        route.append(f"{n}. Refine in {r['refine_city']}")
        route.append(f"{n + 1}. Sell refined in {r['sell_city']}")
        lines.extend(route)
        lines.append("")
        lines.append("--- Data freshness ---")
        lines.append(f"Raw: {_fmt_age(r['buy_age'])} old ({_abs_timestamp(r['buy_age'])})")
        if r["prev_buy_age"] is not None:
            lines.append(
                f"Prev refined: {_fmt_age(r['prev_buy_age'])} old "
                f"({_abs_timestamp(r['prev_buy_age'])})"
            )
        lines.append(f"Refined: {_fmt_age(r['sell_age'])} old ({_abs_timestamp(r['sell_age'])})")
        return "\n".join(lines)

    @staticmethod
    def _build_incomplete_text(r: dict) -> str:
        lines = [f"=== {r['label']} ===", ""]
        lines.append("Incomplete data — can't compute an honest margin.")
        lines.append("Missing live price(s): " + ", ".join(r.get("missing") or ["unknown"]))
        lines.append("")
        lines.append("--- What we do have ---")
        if r["buy_price"] is not None:
            lines.append(f"Raw: buy in {r['buy_city']} @ {r['buy_price']:,} silver")
        else:
            lines.append("Raw: no live price")
        if r["prev_per"] > 0:
            if r["prev_buy_price"]:
                lines.append(
                    f"Prev-tier refined: buy in {r['prev_buy_city']} @ "
                    f"{r['prev_buy_price']:,} silver"
                )
            else:
                lines.append("Prev-tier refined: no live price")
        if r["sell_price"] is not None:
            lines.append(f"Refined sell order: {r['sell_city']} @ {r['sell_price']:,} silver")
        else:
            lines.append("Refined sell order: no live buy order to sell into")
        lines.append("")
        lines.append("Tip: the data is player-sourced — try Refresh, or check this item in-game.")
        return "\n".join(lines)


# ---------- Refining tab ----------


class RefiningTab(QWidget):
    def __init__(self):
        super().__init__()
        self.all_results: list[dict] = []
        self._filtered_rows: list[dict] = []
        self._worker = None
        self._pending = False

        layout = QVBoxLayout(self)

        # Filters
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Tier:"))
        self.tier_checks = {}
        for t in TIERS:
            cb = QCheckBox(f"T{t}")
            cb.setChecked(_load_bool(f"ref/tier/{t}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.tier_checks[t] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Enchant:"))
        self.enchant_checks = {}
        for e in ENCHANTS:
            cb = QCheckBox(f".{e}")
            cb.setChecked(_load_bool(f"ref/enchant/{e}", e == 0))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.enchant_checks[e] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Material:"))
        self.mat_checks = {}
        for mat in RAW_TO_REFINED:
            cb = QCheckBox(mat.title())
            cb.setChecked(_load_bool(f"ref/mat/{mat}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.mat_checks[mat] = cb

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Cities
        city_row = QHBoxLayout()
        city_row.addWidget(QLabel("Cities:"))
        self.city_checks = {}
        for city in CITIES:
            cb = QCheckBox(city)
            cb.setChecked(_load_bool(f"ref/city/{city}", city != "Caerleon"))
            cb.stateChanged.connect(self._on_filter_changed)
            city_row.addWidget(cb)
            self.city_checks[city] = cb
        city_row.addStretch()
        layout.addLayout(city_row)

        # Actions
        action_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh prices")
        self.refresh_btn.clicked.connect(self._refresh)
        action_row.addWidget(self.refresh_btn)

        action_row.addSpacing(15)
        action_row.addWidget(QLabel("Min margin:"))
        self.min_margin = QSpinBox()
        self.min_margin.setRange(-999_999, 9_999_999)
        self.min_margin.setSingleStep(100)
        self.min_margin.setValue(int(settings.value("ref/min_margin", 0)))
        self.min_margin.valueChanged.connect(self._on_filter_changed)
        action_row.addWidget(self.min_margin)

        action_row.addSpacing(15)
        self.auto_refresh = QCheckBox("Auto-refresh every")
        self.auto_refresh.setChecked(_load_bool("ref/auto_refresh", False))
        self.auto_refresh.stateChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_refresh)
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 120)
        self.auto_interval.setValue(int(settings.value("ref/auto_interval", 10)))
        self.auto_interval.setSuffix(" min")
        self.auto_interval.valueChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_interval)

        self.status = QLabel("Idle.")
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        # Table
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Item", "Buy in", "Buy price", "Refine in", "Sell in", "Sell price", "Net margin", "Data age"]
        )
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._show_detail)
        layout.addWidget(self.table)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self._on_auto_refresh_toggled()
        self._refresh()

    def save_state(self):
        for t, cb in self.tier_checks.items():
            settings.setValue(f"ref/tier/{t}", cb.isChecked())
        for e, cb in self.enchant_checks.items():
            settings.setValue(f"ref/enchant/{e}", cb.isChecked())
        for m, cb in self.mat_checks.items():
            settings.setValue(f"ref/mat/{m}", cb.isChecked())
        for c, cb in self.city_checks.items():
            settings.setValue(f"ref/city/{c}", cb.isChecked())
        settings.setValue("ref/min_margin", self.min_margin.value())
        settings.setValue("ref/auto_refresh", self.auto_refresh.isChecked())
        settings.setValue("ref/auto_interval", self.auto_interval.value())

    def _on_filter_changed(self):
        self.save_state()
        self._apply_filters()

    def _on_auto_refresh_toggled(self):
        self.save_state()
        if self.auto_refresh.isChecked():
            self.timer.start(self.auto_interval.value() * 60 * 1000)
        else:
            self.timer.stop()

    def _refresh(self):
        cities = [c for c, cb in self.city_checks.items() if cb.isChecked()]
        if not cities:
            self.status.setText("Pick at least one city.")
            return
        if self._worker is not None and self._worker.isRunning():
            self._pending = True
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText(f"Fetching prices for {len(cities)} cities…")
        worker = RefiningWorker(cities)
        worker.finished_ok.connect(self._on_results)
        worker.failed.connect(self._on_error)
        worker.finished.connect(self._on_worker_done)
        self._worker = worker
        worker.start()

    def _on_results(self, results: list[dict]):
        self.all_results = results
        ts = datetime.now().strftime("%H:%M:%S")
        self.status.setText(f"Loaded {len(results)} pairs at {ts}.")
        self.refresh_btn.setEnabled(True)
        self._apply_filters()

    def _on_error(self, msg: str):
        self.status.setText(f"Error: {msg}")
        self.refresh_btn.setEnabled(True)

    def _on_worker_done(self):
        # Old thread has fully finished — safe to release and run any queued pass.
        self._worker = None
        if self._pending:
            self._pending = False
            self._refresh()

    def _apply_filters(self):
        tiers = {t for t, cb in self.tier_checks.items() if cb.isChecked()}
        enchants = {e for e, cb in self.enchant_checks.items() if cb.isChecked()}
        mats = {m for m, cb in self.mat_checks.items() if cb.isChecked()}
        min_margin = self.min_margin.value()

        def _passes(r):
            if r["tier"] not in tiers or r["enchant"] not in enchants:
                return False
            if r["material"] not in mats:
                return False
            if r["margin"] is None:
                # Data-incomplete rows have no margin — show them only when the
                # user isn't actively filtering for a positive margin.
                return min_margin <= 0
            return r["margin"] >= min_margin

        filtered = [r for r in self.all_results if _passes(r)]
        self._filtered_rows = filtered

        def _silver_or_dash(v):
            return _fmt_silver(v) if v is not None else "—"

        def _age_or_dash(a):
            return _fmt_age(a) if a is not None else "—"

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(filtered))
        for row_i, r in enumerate(filtered):
            refine_text = r["refine_city"] or "—"
            if r["refine_city"] and r["refine_note"] == "no bonus":
                refine_text += " (no bonus)"
            age_text = f"{_age_or_dash(r['buy_age'])} / {_age_or_dash(r['sell_age'])}"
            buy_ts = _abs_timestamp(r["buy_age"]) if r["buy_age"] is not None else "no data"
            sell_ts = _abs_timestamp(r["sell_age"]) if r["sell_age"] is not None else "no data"
            age_tooltip = f"Raw price: {buy_ts}\nRefined price: {sell_ts}"

            margin_item = NumericItem(_silver_or_dash(r["margin"]), r["margin"])
            if r["margin"] is None:
                margin_item.setForeground(QColor("#888888"))
                margin_item.setToolTip(
                    "No margin — missing live price: "
                    + ", ".join(r.get("missing") or ["unknown"])
                )
            elif r["margin"] > 0:
                margin_item.setForeground(QColor("#2e7d32"))
            elif r["margin"] < 0:
                margin_item.setForeground(QColor("#c62828"))

            age_item = QTableWidgetItem(age_text)
            age_item.setToolTip(age_tooltip)

            self.table.setItem(row_i, 0, QTableWidgetItem(r["label"]))
            self.table.setItem(row_i, 1, QTableWidgetItem(r["buy_city"] or "—"))
            self.table.setItem(row_i, 2, NumericItem(_silver_or_dash(r["buy_price"]), r["buy_price"]))
            self.table.setItem(row_i, 3, QTableWidgetItem(refine_text))
            self.table.setItem(row_i, 4, QTableWidgetItem(r["sell_city"] or "—"))
            self.table.setItem(row_i, 5, NumericItem(_silver_or_dash(r["sell_price"]), r["sell_price"]))
            self.table.setItem(row_i, 6, margin_item)
            self.table.setItem(row_i, 7, age_item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(6, Qt.DescendingOrder)

    def _show_detail(self, row: int, _col: int):
        item = self.table.item(row, 0)
        if not item:
            return
        label = item.text()
        match = next((r for r in self._filtered_rows if r["label"] == label), None)
        if match:
            RefiningDetailDialog(self, match).exec()


# ---------- Black Market tab ----------


class BlackMarketTab(QWidget):
    def __init__(self):
        super().__init__()
        self.all_results: list[dict] = []
        self._filtered_rows: list[dict] = []
        self._worker = None
        self._pending = False
        self._last_scan_ts = "—"

        layout = QVBoxLayout(self)

        info = QLabel(
            "Buy gear cheap in a royal city → sell into Black Market buy orders in Caerleon. "
            "Margins include the 4% BM sales tax."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Filters
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Tier:"))
        self.tier_checks = {}
        for t in BM_TIERS:
            cb = QCheckBox(f"T{t}")
            cb.setChecked(_load_bool(f"bm/tier/{t}", t >= 6))
            cb.stateChanged.connect(self._on_scan_filter_changed)
            filter_row.addWidget(cb)
            self.tier_checks[t] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Enchant:"))
        self.enchant_checks = {}
        for e in BM_ENCHANTS:
            cb = QCheckBox(f".{e}")
            cb.setChecked(_load_bool(f"bm/enchant/{e}", True))
            cb.stateChanged.connect(self._on_scan_filter_changed)
            filter_row.addWidget(cb)
            self.enchant_checks[e] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Category:"))
        self.group_checks = {}
        for group in SLOT_GROUPS:
            cb = QCheckBox(group)
            cb.setChecked(_load_bool(f"bm/group/{group}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.group_checks[group] = cb

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Source cities (Caerleon excluded — BM is there, doesn't make sense as source)
        city_row = QHBoxLayout()
        city_row.addWidget(QLabel("Source cities:"))
        self.city_checks = {}
        for city in CITIES:
            if city == "Caerleon":
                continue
            cb = QCheckBox(city)
            cb.setChecked(_load_bool(f"bm/city/{city}", True))
            cb.stateChanged.connect(self._on_scan_filter_changed)
            city_row.addWidget(cb)
            self.city_checks[city] = cb
        city_row.addStretch()
        layout.addLayout(city_row)

        # Actions
        action_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh prices")
        self.refresh_btn.clicked.connect(self._refresh)
        action_row.addWidget(self.refresh_btn)

        action_row.addSpacing(15)
        action_row.addWidget(QLabel("Min margin:"))
        self.min_margin = QSpinBox()
        self.min_margin.setRange(0, 99_999_999)
        self.min_margin.setSingleStep(10_000)
        self.min_margin.setValue(int(settings.value("bm/min_margin", 100_000)))
        self.min_margin.valueChanged.connect(self._on_min_margin_changed)
        action_row.addWidget(self.min_margin)

        action_row.addSpacing(15)
        self.auto_refresh = QCheckBox("Auto-refresh every")
        self.auto_refresh.setChecked(_load_bool("bm/auto_refresh", False))
        self.auto_refresh.stateChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_refresh)
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 120)
        self.auto_interval.setValue(int(settings.value("bm/auto_interval", 10)))
        self.auto_interval.setSuffix(" min")
        self.auto_interval.valueChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_interval)

        self.status = QLabel("Idle.")
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        # Table
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["Item", "Quality", "Buy in", "Buy price", "BM price", "Net (after 4% tax)", "Margin", "Vol/day", "Data age"]
        )
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self._on_auto_refresh_toggled()
        self._refresh()

    def save_state(self):
        for t, cb in self.tier_checks.items():
            settings.setValue(f"bm/tier/{t}", cb.isChecked())
        for e, cb in self.enchant_checks.items():
            settings.setValue(f"bm/enchant/{e}", cb.isChecked())
        for g, cb in self.group_checks.items():
            settings.setValue(f"bm/group/{g}", cb.isChecked())
        for c, cb in self.city_checks.items():
            settings.setValue(f"bm/city/{c}", cb.isChecked())
        settings.setValue("bm/min_margin", self.min_margin.value())
        settings.setValue("bm/auto_refresh", self.auto_refresh.isChecked())
        settings.setValue("bm/auto_interval", self.auto_interval.value())

    def _on_filter_changed(self):
        # Client-side filters (slot group) — no rescan needed.
        self.save_state()
        self._apply_filters()

    def _on_scan_filter_changed(self):
        # Tier/enchant/city affect what we fetch — rescan.
        self.save_state()
        self._refresh()

    def _on_min_margin_changed(self):
        # Min margin is a client-side filter — no network rescan needed (and
        # rescanning on every spinbox tick was spawning overlapping threads).
        self.save_state()
        self._apply_filters()

    def _on_auto_refresh_toggled(self):
        self.save_state()
        if self.auto_refresh.isChecked():
            self.timer.start(self.auto_interval.value() * 60 * 1000)
        else:
            self.timer.stop()

    def _refresh(self):
        cities = [c for c, cb in self.city_checks.items() if cb.isChecked()]
        if not cities:
            self.status.setText("Pick at least one source city.")
            return
        tiers = [t for t, cb in self.tier_checks.items() if cb.isChecked()]
        enchants = [e for e, cb in self.enchant_checks.items() if cb.isChecked()]
        if not tiers or not enchants:
            self.status.setText("Pick at least one tier and enchant.")
            return
        # A scan is already in flight — don't spawn an overlapping QThread (that
        # would destroy the running one and crash the app). Queue one more pass.
        if self._worker is not None and self._worker.isRunning():
            self._pending = True
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText(
            f"Scanning tiers {tiers} enchants {enchants} across {len(cities)} cities + Black Market…"
        )
        # Always fetch every profitable flip (min_margin=0). The user's Min margin
        # is applied client-side in _apply_filters, so changing it never rescans.
        worker = BlackMarketWorker(cities, tiers, enchants, 0)
        worker.finished_ok.connect(self._on_results)
        worker.failed.connect(self._on_error)
        worker.finished.connect(self._on_worker_done)
        self._worker = worker
        worker.start()

    def _on_results(self, results: list[dict]):
        self.all_results = results
        self._last_scan_ts = datetime.now().strftime("%H:%M:%S")
        self.refresh_btn.setEnabled(True)
        self._apply_filters()

    def _on_error(self, msg: str):
        self.status.setText(f"Error: {msg}")
        self.refresh_btn.setEnabled(True)

    def _on_worker_done(self):
        # Old thread has fully finished — safe to release and run any queued pass.
        self._worker = None
        if self._pending:
            self._pending = False
            self._refresh()

    def _apply_filters(self):
        groups = {g for g, cb in self.group_checks.items() if cb.isChecked()}
        min_margin = self.min_margin.value()
        filtered = [
            r
            for r in self.all_results
            if r["group"] in groups and r["margin"] >= min_margin
        ]
        self._filtered_rows = filtered
        self.status.setText(
            f"Showing {len(filtered)} of {len(self.all_results)} flips "
            f"(margin ≥ {_fmt_silver(min_margin)}) — scanned {self._last_scan_ts}."
        )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(filtered))
        for row_i, r in enumerate(filtered):
            age_text = f"{_fmt_age(r['buy_age'])} / {_fmt_age(r['bm_age'])}"
            age_tooltip = (
                f"City price: {_abs_timestamp(r['buy_age'])}\n"
                f"BM price: {_abs_timestamp(r['bm_age'])}"
            )
            label = f"T{r['tier']}.{r['enchant']} {r['name']}"
            quality_text = QUALITIES.get(r["quality"], str(r["quality"]))
            vol = r.get("daily_volume")  # None = not fetched (outside top-N by margin)
            vol_text = "—" if vol is None else str(vol)

            margin_item = NumericItem(_fmt_silver(r["margin"]), r["margin"])
            if r["margin"] > 0:
                margin_item.setForeground(QColor("#2e7d32"))
            elif r["margin"] < 0:
                margin_item.setForeground(QColor("#c62828"))

            # Unfetched volume (None) sorts to the bottom via key -1.
            vol_item = NumericItem(vol_text, -1 if vol is None else vol)
            if vol is None:
                vol_item.setForeground(QColor("#888888"))
                vol_item.setToolTip(
                    "Volume only fetched for the top flips by margin — "
                    "raise Min margin or check this item in-game"
                )
            else:
                if vol >= 20:
                    vol_item.setForeground(QColor("#2e7d32"))
                elif vol >= 5:
                    vol_item.setForeground(QColor("#b08800"))
                else:
                    vol_item.setForeground(QColor("#c62828"))
                vol_item.setToolTip("Avg units sold to BM per day (last 7 days)")

            age_item = QTableWidgetItem(age_text)
            age_item.setToolTip(age_tooltip)

            self.table.setItem(row_i, 0, QTableWidgetItem(label))
            self.table.setItem(row_i, 1, NumericItem(quality_text, r["quality"]))
            self.table.setItem(row_i, 2, QTableWidgetItem(r["buy_city"]))
            self.table.setItem(row_i, 3, NumericItem(_fmt_silver(r["buy_price"]), r["buy_price"]))
            self.table.setItem(row_i, 4, NumericItem(_fmt_silver(r["bm_price"]), r["bm_price"]))
            self.table.setItem(row_i, 5, NumericItem(_fmt_silver(r["net_sell"]), r["net_sell"]))
            self.table.setItem(row_i, 6, margin_item)
            self.table.setItem(row_i, 7, vol_item)
            self.table.setItem(row_i, 8, age_item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(6, Qt.DescendingOrder)


# ---------- Resource haul tab ----------


class ResourceHaulTab(QWidget):
    """Buy a gathering product cheap in one city → list a sell order in another.

    City selection drives the fetch; every other filter (tier, enchant, type,
    material, min margin) is applied client-side, so toggling them is instant
    and never spawns an overlapping scan thread.
    """

    KINDS = ["Raw", "Refined"]

    def __init__(self):
        super().__init__()
        self.all_results: list[dict] = []
        self._filtered_rows: list[dict] = []
        self._worker = None
        self._pending = False
        self._last_scan_ts = "—"

        layout = QVBoxLayout(self)

        info = QLabel(
            "Buy a resource at its cheapest city → haul it → list your own sell order "
            "in the priciest city. Margin = destination price × (1 − 6.5% tax/fee) − buy price."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Filters (all client-side)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Tier:"))
        self.tier_checks = {}
        for t in TIERS:
            cb = QCheckBox(f"T{t}")
            cb.setChecked(_load_bool(f"haul/tier/{t}", t >= 4))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.tier_checks[t] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Enchant:"))
        self.enchant_checks = {}
        for e in RESOURCE_ENCHANTS:
            cb = QCheckBox(f".{e}")
            cb.setChecked(_load_bool(f"haul/enchant/{e}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.enchant_checks[e] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Type:"))
        self.kind_checks = {}
        for k in self.KINDS:
            cb = QCheckBox(k)
            cb.setChecked(_load_bool(f"haul/kind/{k}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.kind_checks[k] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Material:"))
        self.mat_checks = {}
        for mat in RAW_TO_REFINED:
            cb = QCheckBox(mat.title())
            cb.setChecked(_load_bool(f"haul/mat/{mat}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.mat_checks[mat] = cb

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Cities (drive the fetch — changing these rescans)
        city_row = QHBoxLayout()
        city_row.addWidget(QLabel("Cities:"))
        self.city_checks = {}
        for city in CITIES:
            cb = QCheckBox(city)
            cb.setChecked(_load_bool(f"haul/city/{city}", city != "Caerleon"))
            cb.stateChanged.connect(self._on_city_changed)
            city_row.addWidget(cb)
            self.city_checks[city] = cb
        city_row.addStretch()
        layout.addLayout(city_row)

        # Actions
        action_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh prices")
        self.refresh_btn.clicked.connect(self._refresh)
        action_row.addWidget(self.refresh_btn)

        action_row.addSpacing(15)
        action_row.addWidget(QLabel("Min margin:"))
        self.min_margin = QSpinBox()
        self.min_margin.setRange(-999_999, 9_999_999)
        self.min_margin.setSingleStep(50)
        self.min_margin.setValue(int(settings.value("haul/min_margin", 0)))
        self.min_margin.valueChanged.connect(self._on_filter_changed)
        action_row.addWidget(self.min_margin)

        action_row.addSpacing(15)
        self.auto_refresh = QCheckBox("Auto-refresh every")
        self.auto_refresh.setChecked(_load_bool("haul/auto_refresh", False))
        self.auto_refresh.stateChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_refresh)
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 120)
        self.auto_interval.setValue(int(settings.value("haul/auto_interval", 10)))
        self.auto_interval.setSuffix(" min")
        self.auto_interval.valueChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_interval)

        self.status = QLabel("Idle.")
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        # Table
        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            [
                "Item",
                "Type",
                "Buy in",
                "Buy price",
                "Sell in",
                "Sell price",
                "Net (after 6.5%)",
                "Margin",
                "ROI",
                "Vol/day",
                "Data age",
            ]
        )
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self._on_auto_refresh_toggled()
        self._refresh()

    def save_state(self):
        for t, cb in self.tier_checks.items():
            settings.setValue(f"haul/tier/{t}", cb.isChecked())
        for e, cb in self.enchant_checks.items():
            settings.setValue(f"haul/enchant/{e}", cb.isChecked())
        for k, cb in self.kind_checks.items():
            settings.setValue(f"haul/kind/{k}", cb.isChecked())
        for m, cb in self.mat_checks.items():
            settings.setValue(f"haul/mat/{m}", cb.isChecked())
        for c, cb in self.city_checks.items():
            settings.setValue(f"haul/city/{c}", cb.isChecked())
        settings.setValue("haul/min_margin", self.min_margin.value())
        settings.setValue("haul/auto_refresh", self.auto_refresh.isChecked())
        settings.setValue("haul/auto_interval", self.auto_interval.value())

    def _on_filter_changed(self):
        # Client-side filters only — no network rescan.
        self.save_state()
        self._apply_filters()

    def _on_city_changed(self):
        # Cities drive the fetch — rescan.
        self.save_state()
        self._refresh()

    def _on_auto_refresh_toggled(self):
        self.save_state()
        if self.auto_refresh.isChecked():
            self.timer.start(self.auto_interval.value() * 60 * 1000)
        else:
            self.timer.stop()

    def _refresh(self):
        cities = [c for c, cb in self.city_checks.items() if cb.isChecked()]
        if len(cities) < 2:
            self.status.setText("Pick at least two cities (a haul needs a source and a destination).")
            return
        if self._worker is not None and self._worker.isRunning():
            self._pending = True
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText(f"Fetching resource prices across {len(cities)} cities…")
        worker = ResourceHaulWorker(cities)
        worker.finished_ok.connect(self._on_results)
        worker.failed.connect(self._on_error)
        worker.finished.connect(self._on_worker_done)
        self._worker = worker
        worker.start()

    def _on_results(self, results: list[dict]):
        self.all_results = results
        self._last_scan_ts = datetime.now().strftime("%H:%M:%S")
        self.refresh_btn.setEnabled(True)
        self._apply_filters()

    def _on_error(self, msg: str):
        self.status.setText(f"Error: {msg}")
        self.refresh_btn.setEnabled(True)

    def _on_worker_done(self):
        self._worker = None
        if self._pending:
            self._pending = False
            self._refresh()

    def _apply_filters(self):
        tiers = {t for t, cb in self.tier_checks.items() if cb.isChecked()}
        enchants = {e for e, cb in self.enchant_checks.items() if cb.isChecked()}
        kinds = {k for k, cb in self.kind_checks.items() if cb.isChecked()}
        mats = {m for m, cb in self.mat_checks.items() if cb.isChecked()}
        min_margin = self.min_margin.value()

        filtered = [
            r
            for r in self.all_results
            if r["tier"] in tiers
            and r["enchant"] in enchants
            and r["kind"] in kinds
            and r["family"] in mats
            and r["margin"] >= min_margin
        ]
        self._filtered_rows = filtered
        self.status.setText(
            f"Showing {len(filtered)} of {len(self.all_results)} hauls "
            f"(margin ≥ {_fmt_silver(min_margin)}) — scanned {self._last_scan_ts}."
        )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(filtered))
        for row_i, r in enumerate(filtered):
            age_text = f"{_fmt_age(r['buy_age'])} / {_fmt_age(r['sell_age'])}"
            age_tooltip = (
                f"Buy price: {_abs_timestamp(r['buy_age'])}\n"
                f"Sell price: {_abs_timestamp(r['sell_age'])}"
            )
            roi_pct = r["roi"] * 100
            vol = r.get("daily_volume")  # None = not fetched (outside top-N by margin)
            vol_text = "—" if vol is None else str(vol)

            margin_item = NumericItem(_fmt_silver(r["margin"]), r["margin"])
            if r["margin"] > 0:
                margin_item.setForeground(QColor("#2e7d32"))
            elif r["margin"] < 0:
                margin_item.setForeground(QColor("#c62828"))

            # Unfetched volume (None) sorts to the bottom via key -1.
            vol_item = NumericItem(vol_text, -1 if vol is None else vol)
            if vol is None:
                vol_item.setForeground(QColor("#888888"))
                vol_item.setToolTip(
                    "Volume only fetched for the top hauls by margin — "
                    "raise Min margin or check this item in-game"
                )
            else:
                if vol >= 20:
                    vol_item.setForeground(QColor("#2e7d32"))
                elif vol >= 5:
                    vol_item.setForeground(QColor("#b08800"))
                else:
                    vol_item.setForeground(QColor("#c62828"))
                vol_item.setToolTip(
                    f"Avg units sold per day in {r['sell_city']} (last 7 days)"
                )

            age_item = QTableWidgetItem(age_text)
            age_item.setToolTip(age_tooltip)

            self.table.setItem(row_i, 0, QTableWidgetItem(r["label"]))
            self.table.setItem(row_i, 1, QTableWidgetItem(r["kind"]))
            self.table.setItem(row_i, 2, QTableWidgetItem(r["buy_city"]))
            self.table.setItem(row_i, 3, NumericItem(_fmt_silver(r["buy_price"]), r["buy_price"]))
            self.table.setItem(row_i, 4, QTableWidgetItem(r["sell_city"]))
            self.table.setItem(row_i, 5, NumericItem(_fmt_silver(r["sell_price"]), r["sell_price"]))
            self.table.setItem(row_i, 6, NumericItem(_fmt_silver(r["net_sell"]), r["net_sell"]))
            self.table.setItem(row_i, 7, margin_item)
            self.table.setItem(row_i, 8, NumericItem(f"{roi_pct:.1f}%", roi_pct))
            self.table.setItem(row_i, 9, vol_item)
            self.table.setItem(row_i, 10, age_item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(7, Qt.DescendingOrder)


# ---------- Gather advisor tab ----------


class GatherDetailDialog(QDialog):
    def __init__(self, parent, row: dict):
        super().__init__(parent)
        self.setWindowTitle(row["label"])
        self.resize(540, 460)
        layout = QVBoxLayout(self)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self._build_text(row))
        layout.addWidget(text)

    @staticmethod
    def _build_text(r: dict) -> str:
        lines = [f"=== {r['label']} (gathered) ===", ""]
        lines.append(f"Recommended: {r['best_action']} — {r['best_value']:,} silver per gathered unit")
        lines.append("(Net of 6.5% sell-order tax/fee. Raws are free; refine path buys the lesser material.)")
        lines.append("")

        lines.append("--- Option A: Sell raw ---")
        if r["raw_value"] is not None:
            lines.append(
                f"List raw in {r['raw_sell_city']} @ {r['raw_sell_price']:,}  ->  "
                f"x0.935 = {int(round(r['raw_value'])):,} /unit"
            )
        else:
            lines.append("No live raw sell price.")
        lines.append("")

        lines.append("--- Option B: Refine -> sell ---")
        if r["refine_value"] is not None:
            rr = r["return_rate"]
            eff_raws = r["raws_per"] * (1 - rr)
            eff_prev = r["prev_per"] * (1 - rr)
            where = (
                f"{r['refine_city']} (bonus, {rr * 100:.1f}% return)"
                if r["refine_city"]
                else f"any royal city ({rr * 100:.1f}% return)"
            )
            lines.append(f"Refine in {where}")
            recipe = f"{r['raws_per']} raws (gathered)"
            if r["prev_per"] > 0:
                recipe += f" + {r['prev_per']} T{r['tier'] - 1} {r['refined_name']}"
            lines.append(f"Recipe per refined unit: {recipe}")
            if r["prev_per"] > 0:
                if r["prev_buy_price"]:
                    lines.append(
                        f"Buy T{r['tier'] - 1} {r['refined_name']} in {r['prev_buy_city']} @ "
                        f"{r['prev_buy_price']:,}  (effective {eff_prev:.2f}/refined after return)"
                    )
                else:
                    lines.append(f"T{r['tier'] - 1} {r['refined_name']}: no live price")
            refined_net = r["refine_sell_price"] * (1 - 0.065)
            lines.append(
                f"Sell refined in {r['refine_sell_city']} @ {r['refine_sell_price']:,}  ->  "
                f"x0.935 = {int(round(refined_net)):,}"
            )
            prev_cost = eff_prev * (r["prev_buy_price"] or 0)
            lines.append(
                f"Net per refined unit = {int(round(refined_net)):,} - "
                f"{int(round(prev_cost)):,} input = {int(round(refined_net - prev_cost)):,}"
            )
            lines.append(
                f"Per gathered raw unit (/ {eff_raws:.2f} raws) = "
                f"{int(round(r['refine_value'])):,} /unit"
            )
        else:
            lines.append("Refine path unavailable (missing refined or lesser-material price).")
        lines.append("")

        lines.append("--- Data freshness ---")
        if r["raw_age"] is not None:
            lines.append(f"Raw price: {_fmt_age(r['raw_age'])} old ({_abs_timestamp(r['raw_age'])})")
        if r["refine_age"] is not None:
            lines.append(
                f"Refined price: {_fmt_age(r['refine_age'])} old ({_abs_timestamp(r['refine_age'])})"
            )
        return "\n".join(lines)


class GatherTab(QWidget):
    """Rank manually-gathered raws by net silver per gathered unit, choosing the
    better of sell-raw vs refine-then-sell. City selection drives the fetch;
    everything else is a client-side filter.
    """

    def __init__(self):
        super().__init__()
        self.all_results: list[dict] = []
        self._filtered_rows: list[dict] = []
        self._worker = None
        self._pending = False
        self._last_scan_ts = "—"

        layout = QVBoxLayout(self)

        info = QLabel(
            "You gather the raws for free — this ranks each resource by net silver per "
            "gathered unit, picking the better of selling it raw or refining it and selling "
            "the bar/cloth/etc. The refine path buys the lesser material at market and includes "
            "the 6.5% sell-order tax. Double-click a row for the full breakdown."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Filters (all client-side)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Tier:"))
        self.tier_checks = {}
        for t in TIERS:
            cb = QCheckBox(f"T{t}")
            cb.setChecked(_load_bool(f"gather/tier/{t}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.tier_checks[t] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Enchant:"))
        self.enchant_checks = {}
        for e in ENCHANTS:
            cb = QCheckBox(f".{e}")
            cb.setChecked(_load_bool(f"gather/enchant/{e}", e <= 1))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.enchant_checks[e] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Material:"))
        self.mat_checks = {}
        for mat in RAW_TO_REFINED:
            cb = QCheckBox(mat.title())
            cb.setChecked(_load_bool(f"gather/mat/{mat}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.mat_checks[mat] = cb

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Cities (drive the fetch)
        city_row = QHBoxLayout()
        city_row.addWidget(QLabel("Cities:"))
        self.city_checks = {}
        for city in CITIES:
            cb = QCheckBox(city)
            cb.setChecked(_load_bool(f"gather/city/{city}", city != "Caerleon"))
            cb.stateChanged.connect(self._on_city_changed)
            city_row.addWidget(cb)
            self.city_checks[city] = cb
        city_row.addStretch()
        layout.addLayout(city_row)

        # Actions
        action_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh prices")
        self.refresh_btn.clicked.connect(self._refresh)
        action_row.addWidget(self.refresh_btn)

        action_row.addSpacing(15)
        action_row.addWidget(QLabel("Min silver/unit:"))
        self.min_value = QSpinBox()
        self.min_value.setRange(0, 9_999_999)
        self.min_value.setSingleStep(100)
        self.min_value.setValue(int(settings.value("gather/min_value", 0)))
        self.min_value.valueChanged.connect(self._on_filter_changed)
        action_row.addWidget(self.min_value)

        action_row.addSpacing(15)
        self.auto_refresh = QCheckBox("Auto-refresh every")
        self.auto_refresh.setChecked(_load_bool("gather/auto_refresh", False))
        self.auto_refresh.stateChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_refresh)
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 120)
        self.auto_interval.setValue(int(settings.value("gather/auto_interval", 10)))
        self.auto_interval.setSuffix(" min")
        self.auto_interval.valueChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_interval)

        self.status = QLabel("Idle.")
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        # Table
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Item",
                "Best action",
                "Net silver/unit",
                "Sell raw /unit",
                "Refine /unit",
                "Sell in",
                "Inputs to buy",
                "Vol/day",
                "Data age",
            ]
        )
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._show_detail)
        layout.addWidget(self.table)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self._on_auto_refresh_toggled()
        self._refresh()

    def save_state(self):
        for t, cb in self.tier_checks.items():
            settings.setValue(f"gather/tier/{t}", cb.isChecked())
        for e, cb in self.enchant_checks.items():
            settings.setValue(f"gather/enchant/{e}", cb.isChecked())
        for m, cb in self.mat_checks.items():
            settings.setValue(f"gather/mat/{m}", cb.isChecked())
        for c, cb in self.city_checks.items():
            settings.setValue(f"gather/city/{c}", cb.isChecked())
        settings.setValue("gather/min_value", self.min_value.value())
        settings.setValue("gather/auto_refresh", self.auto_refresh.isChecked())
        settings.setValue("gather/auto_interval", self.auto_interval.value())

    def _on_filter_changed(self):
        self.save_state()
        self._apply_filters()

    def _on_city_changed(self):
        self.save_state()
        self._refresh()

    def _on_auto_refresh_toggled(self):
        self.save_state()
        if self.auto_refresh.isChecked():
            self.timer.start(self.auto_interval.value() * 60 * 1000)
        else:
            self.timer.stop()

    def _refresh(self):
        cities = [c for c, cb in self.city_checks.items() if cb.isChecked()]
        if not cities:
            self.status.setText("Pick at least one city.")
            return
        if self._worker is not None and self._worker.isRunning():
            self._pending = True
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText(f"Scanning gather options across {len(cities)} cities…")
        worker = GatherWorker(cities)
        worker.finished_ok.connect(self._on_results)
        worker.failed.connect(self._on_error)
        worker.finished.connect(self._on_worker_done)
        self._worker = worker
        worker.start()

    def _on_results(self, results: list[dict]):
        self.all_results = results
        self._last_scan_ts = datetime.now().strftime("%H:%M:%S")
        self.refresh_btn.setEnabled(True)
        self._apply_filters()

    def _on_error(self, msg: str):
        self.status.setText(f"Error: {msg}")
        self.refresh_btn.setEnabled(True)

    def _on_worker_done(self):
        self._worker = None
        if self._pending:
            self._pending = False
            self._refresh()

    def _apply_filters(self):
        tiers = {t for t, cb in self.tier_checks.items() if cb.isChecked()}
        enchants = {e for e, cb in self.enchant_checks.items() if cb.isChecked()}
        mats = {m for m, cb in self.mat_checks.items() if cb.isChecked()}
        min_value = self.min_value.value()

        filtered = [
            r
            for r in self.all_results
            if r["tier"] in tiers
            and r["enchant"] in enchants
            and r["material"] in mats
            and r["best_value"] >= min_value
        ]
        self._filtered_rows = filtered
        self.status.setText(
            f"Showing {len(filtered)} of {len(self.all_results)} resources "
            f"(≥ {_fmt_silver(min_value)}/unit) — scanned {self._last_scan_ts}."
        )

        def _silver_or_dash(v):
            return _fmt_silver(int(round(v))) if v is not None else "—"

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(filtered))
        for row_i, r in enumerate(filtered):
            sell_city = r["raw_sell_city"] if r["best"] == "raw" else r["refine_sell_city"]
            if r["best"] == "refine" and r["prev_per"] > 0 and r["prev_buy_city"]:
                inputs = f"{r['prev_per']}× T{r['tier'] - 1} {r['refined_name']} @ {_fmt_silver(r['prev_buy_price'])}"
            elif r["best"] == "refine":
                inputs = "none"
            else:
                inputs = "—"
            age = r["raw_age"] if r["best"] == "raw" else r["refine_age"]
            age_text = _fmt_age(age) if age is not None else "—"

            best_item = NumericItem(_silver_or_dash(r["best_value"]), r["best_value"])
            if r["best_value"] > 0:
                best_item.setForeground(QColor("#2e7d32"))

            vol = r.get("daily_volume")  # None = not fetched (outside top-N)
            vol_text = "—" if vol is None else str(vol)
            vol_item = NumericItem(vol_text, -1 if vol is None else vol)
            if vol is None:
                vol_item.setForeground(QColor("#888888"))
                vol_item.setToolTip(
                    "Volume only fetched for the top options — "
                    "raise Min silver/unit or check this item in-game"
                )
            else:
                if vol >= 20:
                    vol_item.setForeground(QColor("#2e7d32"))
                elif vol >= 5:
                    vol_item.setForeground(QColor("#b08800"))
                else:
                    vol_item.setForeground(QColor("#c62828"))
                vol_item.setToolTip(
                    f"Avg units of the listed item sold per day in {sell_city} (last 7 days)"
                )

            self.table.setItem(row_i, 0, QTableWidgetItem(r["label"]))
            self.table.setItem(row_i, 1, QTableWidgetItem(r["best_action"]))
            self.table.setItem(row_i, 2, best_item)
            self.table.setItem(row_i, 3, NumericItem(_silver_or_dash(r["raw_value"]), r["raw_value"]))
            self.table.setItem(row_i, 4, NumericItem(_silver_or_dash(r["refine_value"]), r["refine_value"]))
            self.table.setItem(row_i, 5, QTableWidgetItem(sell_city or "—"))
            self.table.setItem(row_i, 6, QTableWidgetItem(inputs))
            self.table.setItem(row_i, 7, vol_item)
            self.table.setItem(row_i, 8, QTableWidgetItem(age_text))
        self.table.setSortingEnabled(True)
        self.table.sortItems(2, Qt.DescendingOrder)

    def _show_detail(self, row: int, _col: int):
        item = self.table.item(row, 0)
        if not item:
            return
        label = item.text()
        match = next((r for r in self._filtered_rows if r["label"] == label), None)
        if match:
            GatherDetailDialog(self, match).exec()


# ---------- Main window ----------


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Albion Market Scanner — NA (Americas)")
        self.resize(1150, 740)

        tabs = QTabWidget()
        self.refining = RefiningTab()
        self.bm = BlackMarketTab()
        self.haul = ResourceHaulTab()
        self.gather = GatherTab()
        tabs.addTab(self.refining, "Refining")
        tabs.addTab(self.bm, "Black Market flip")
        tabs.addTab(self.haul, "Resource haul")
        tabs.addTab(self.gather, "Gather advisor")
        self.setCentralWidget(tabs)

    def closeEvent(self, event):
        self.refining.save_state()
        self.bm.save_state()
        self.haul.save_state()
        self.gather.save_state()
        super().closeEvent(event)


def main():
    app = QApplication([])
    win = MainWindow()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
