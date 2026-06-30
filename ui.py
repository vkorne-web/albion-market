import asyncio
from datetime import datetime, timedelta
from math import ceil

from PySide6.QtCore import Qt, QThread, QTimer, QSettings, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
    CRAFT_BONUS_DAY,
    ENCHANTS,
    QUALITIES,
    RAW_TO_REFINED,
    RESOURCE_ENCHANTS,
    SELL_ORDER_TAX,
    SLOT_GROUPS,
    TIERS,
    craft_return_rate,
)
from scanner import (
    BM_TAX,
    check_items,
    fetch_craft_prices,
    scan,
    scan_black_market,
    scan_craft,
    scan_gather,
    scan_manipulation,
    scan_resource_haul,
)
import names
import recipes
from i18n import tr, set_lang, get_lang, load_lang

ORG = "AlbionMarket"
APP = "AlbionMarket"
settings = QSettings(ORG, APP)


def _fmt_silver(n: int) -> str:
    return f"{n:,}"


def _silver_or_dash(v) -> str:
    """Format a silver amount (rounding floats), or an em dash for None."""
    return f"{int(round(v)):,}" if v is not None else "—"


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


# ---------- i18n-aware widgets ----------
# Each remembers its English source string so a tab's retranslate() can re-apply
# the current language by walking its children — no per-widget ref bookkeeping.


class TransLabel(QLabel):
    def __init__(self, key: str):
        super().__init__(tr(key))
        self._key = key

    def retranslate(self):
        self.setText(tr(self._key))


class TransButton(QPushButton):
    def __init__(self, key: str):
        super().__init__(tr(key))
        self._key = key

    def retranslate(self):
        self.setText(tr(self._key))


class TransCheck(QCheckBox):
    def __init__(self, key: str):
        super().__init__(tr(key))
        self._key = key

    def retranslate(self):
        self.setText(tr(self._key))


def retranslate_children(widget):
    """Re-apply the current language to every Trans* widget under `widget`."""
    for cls in (TransLabel, TransButton, TransCheck):
        for w in widget.findChildren(cls):
            w.retranslate()


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


class ScamWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, cities, tiers, enchants):
        super().__init__()
        self.cities = cities
        self.tiers = tiers
        self.enchants = enchants

    def run(self):
        try:
            self.finished_ok.emit(
                asyncio.run(
                    scan_manipulation(
                        cities=self.cities, tiers=self.tiers, enchants=self.enchants
                    )
                )
            )
        except Exception as e:
            self.failed.emit(str(e))


class BundleCheckWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, item_ids, cities):
        super().__init__()
        self.item_ids = item_ids
        self.cities = cities

    def run(self):
        try:
            self.finished_ok.emit(
                asyncio.run(check_items(self.item_ids, cities=self.cities))
            )
        except Exception as e:
            self.failed.emit(str(e))


class CraftWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, item_ids, cities, return_rate):
        super().__init__()
        self.item_ids = item_ids
        self.cities = cities
        self.return_rate = return_rate

    def run(self):
        try:
            self.finished_ok.emit(
                asyncio.run(scan_craft(self.item_ids, self.cities, self.return_rate))
            )
        except Exception as e:
            self.failed.emit(str(e))


class CraftCalcFetchWorker(QThread):
    """Pulls live material/sell prices for every enchant tab in one batch."""

    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, items, cities):
        super().__init__()
        self.items = items  # list of (crafted_id, [material_ids])
        self.cities = cities

    def run(self):
        try:
            self.finished_ok.emit(
                asyncio.run(fetch_craft_prices(self.items, self.cities))
            )
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
        lines.append(tr("Recipe: {raws} raw + {prev} prev-tier refined").format(
            raws=r['raws_per'], prev=r['prev_per']))
        lines.append(tr("Refine city: {city} ({note})").format(
            city=r['refine_city'], note=r['refine_note']))
        lines.append(tr("Return rate (no focus): {pct}%").format(pct=f"{r['return_rate'] * 100:.1f}"))
        lines.append("")
        lines.append(tr("--- Inputs ---"))
        lines.append(
            tr("Raw: buy {qty}x in {city} @ {price} = {total} silver").format(
                qty=r['raws_per'], city=r['buy_city'], price=f"{r['buy_price']:,}",
                total=f"{r['raws_per'] * r['buy_price']:,}")
        )
        if r["prev_per"] > 0:
            lines.append(
                tr("Prev-tier refined: buy {qty}x in {city} @ {price} = {total} silver").format(
                    qty=r['prev_per'], city=r['prev_buy_city'], price=f"{r['prev_buy_price']:,}",
                    total=f"{r['prev_per'] * r['prev_buy_price']:,}")
            )
        lines.append(tr("Gross input cost: {v} silver").format(v=f"{int(round(r['gross_input'])):,}"))
        lines.append(
            tr("Effective input cost (after {pct}% return): {v} silver").format(
                pct=f"{r['return_rate'] * 100:.1f}", v=f"{int(round(r['effective_input'])):,}")
        )
        lines.append("")
        lines.append(tr("--- Output ---"))
        lines.append(tr("Sell 1x refined in {city} @ {price} silver").format(
            city=r['sell_city'], price=f"{r['sell_price']:,}"))
        lines.append("")
        lines.append(tr("NET MARGIN per refined unit: {v} silver").format(v=f"{r['margin']:,}"))
        lines.append("")
        lines.append(tr("--- Route ---"))
        route = [tr("1. Buy raws in {city}").format(city=r['buy_city'])]
        if r["prev_per"] > 0 and r["prev_buy_city"] != r["buy_city"]:
            route.append(tr("2. Buy prev-tier refined in {city}").format(city=r['prev_buy_city']))
        n = len(route) + 1
        route.append(tr("{n}. Refine in {city}").format(n=n, city=r['refine_city']))
        route.append(tr("{n}. Sell refined in {city}").format(n=n + 1, city=r['sell_city']))
        lines.extend(route)
        lines.append("")
        lines.append(tr("--- Data freshness ---"))
        lines.append(tr("Raw: {age} old ({ts})").format(
            age=_fmt_age(r['buy_age']), ts=_abs_timestamp(r['buy_age'])))
        if r["prev_buy_age"] is not None:
            lines.append(
                tr("Prev refined: {age} old ({ts})").format(
                    age=_fmt_age(r['prev_buy_age']), ts=_abs_timestamp(r['prev_buy_age']))
            )
        lines.append(tr("Refined: {age} old ({ts})").format(
            age=_fmt_age(r['sell_age']), ts=_abs_timestamp(r['sell_age'])))
        return "\n".join(lines)

    @staticmethod
    def _build_incomplete_text(r: dict) -> str:
        lines = [f"=== {r['label']} ===", ""]
        lines.append(tr("Incomplete data — can't compute an honest margin."))
        lines.append(tr("Missing live price(s): ") + ", ".join(r.get("missing") or [tr("unknown")]))
        lines.append("")
        lines.append(tr("--- What we do have ---"))
        if r["buy_price"] is not None:
            lines.append(tr("Raw: buy in {city} @ {price} silver").format(
                city=r['buy_city'], price=f"{r['buy_price']:,}"))
        else:
            lines.append(tr("Raw: no live price"))
        if r["prev_per"] > 0:
            if r["prev_buy_price"]:
                lines.append(
                    tr("Prev-tier refined: buy in {city} @ {price} silver").format(
                        city=r['prev_buy_city'], price=f"{r['prev_buy_price']:,}")
                )
            else:
                lines.append(tr("Prev-tier refined: no live price"))
        if r["sell_price"] is not None:
            lines.append(tr("Refined sell order: {city} @ {price} silver").format(
                city=r['sell_city'], price=f"{r['sell_price']:,}"))
        else:
            lines.append(tr("Refined sell order: no live buy order to sell into"))
        lines.append("")
        lines.append(tr("Tip: the data is player-sourced — try Refresh, or check this item in-game."))
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
        filter_row.addWidget(TransLabel("Tier:"))
        self.tier_checks = {}
        for t in TIERS:
            cb = QCheckBox(f"T{t}")
            cb.setChecked(_load_bool(f"ref/tier/{t}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.tier_checks[t] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(TransLabel("Enchant:"))
        self.enchant_checks = {}
        for e in ENCHANTS:
            cb = QCheckBox(f".{e}")
            cb.setChecked(_load_bool(f"ref/enchant/{e}", e == 0))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.enchant_checks[e] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(TransLabel("Material:"))
        self.mat_checks = {}
        for mat in RAW_TO_REFINED:
            cb = QCheckBox(tr(mat.title()))
            cb.setChecked(_load_bool(f"ref/mat/{mat}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.mat_checks[mat] = cb

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Cities
        city_row = QHBoxLayout()
        city_row.addWidget(TransLabel("Cities:"))
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
        self.refresh_btn = TransButton("Refresh prices")
        self.refresh_btn.clicked.connect(self._refresh)
        action_row.addWidget(self.refresh_btn)

        action_row.addSpacing(15)
        action_row.addWidget(TransLabel("Min margin:"))
        self.min_margin = QSpinBox()
        self.min_margin.setRange(-999_999, 9_999_999)
        self.min_margin.setSingleStep(100)
        self.min_margin.setValue(int(settings.value("ref/min_margin", 0)))
        self.min_margin.valueChanged.connect(self._on_filter_changed)
        action_row.addWidget(self.min_margin)

        action_row.addSpacing(15)
        self.auto_refresh = TransCheck("Auto-refresh every")
        self.auto_refresh.setChecked(_load_bool("ref/auto_refresh", False))
        self.auto_refresh.stateChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_refresh)
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 120)
        self.auto_interval.setValue(int(settings.value("ref/auto_interval", 10)))
        self.auto_interval.setSuffix(" min")
        self.auto_interval.valueChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_interval)

        self.status = QLabel(tr("Idle."))
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        # Table
        self.table = QTableWidget(0, 8)
        self._set_headers()
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

    def _set_headers(self):
        self.table.setHorizontalHeaderLabels(
            [tr("Item"), tr("Buy in"), tr("Buy price"), tr("Refine in"), tr("Sell in"),
             tr("Sell price"), tr("Net margin"), tr("Data age")]
        )

    def retranslate(self):
        retranslate_children(self)
        self._set_headers()
        for mat, cb in self.mat_checks.items():
            cb.setText(tr(mat.title()))
        self._apply_filters()

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
            self.status.setText(tr("Pick at least one city."))
            return
        if self._worker is not None and self._worker.isRunning():
            self._pending = True
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText(tr("Fetching prices for {n} cities…").format(n=len(cities)))
        worker = RefiningWorker(cities)
        worker.finished_ok.connect(self._on_results)
        worker.failed.connect(self._on_error)
        worker.finished.connect(self._on_worker_done)
        self._worker = worker
        worker.start()

    def _on_results(self, results: list[dict]):
        self.all_results = results
        ts = datetime.now().strftime("%H:%M:%S")
        self.status.setText(tr("Loaded {n} pairs at {ts}.").format(n=len(results), ts=ts))
        self.refresh_btn.setEnabled(True)
        self._apply_filters()

    def _on_error(self, msg: str):
        self.status.setText(tr("Error: {msg}").format(msg=msg))
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
                refine_text += tr(" (no bonus)")
            age_text = f"{_age_or_dash(r['buy_age'])} / {_age_or_dash(r['sell_age'])}"
            buy_ts = _abs_timestamp(r["buy_age"]) if r["buy_age"] is not None else tr("no data")
            sell_ts = _abs_timestamp(r["sell_age"]) if r["sell_age"] is not None else tr("no data")
            age_tooltip = tr("Raw price: {buy}\nRefined price: {sell}").format(buy=buy_ts, sell=sell_ts)

            margin_item = NumericItem(_silver_or_dash(r["margin"]), r["margin"])
            if r["margin"] is None:
                margin_item.setForeground(QColor("#888888"))
                margin_item.setToolTip(
                    tr("No margin — missing live price: ")
                    + ", ".join(r.get("missing") or [tr("unknown")])
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

        info = TransLabel(
            "Buy gear cheap in a royal city → sell into Black Market buy orders in Caerleon. "
            "Margins include the 4% BM sales tax."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Filters
        filter_row = QHBoxLayout()
        filter_row.addWidget(TransLabel("Tier:"))
        self.tier_checks = {}
        for t in BM_TIERS:
            cb = QCheckBox(f"T{t}")
            cb.setChecked(_load_bool(f"bm/tier/{t}", t >= 6))
            cb.stateChanged.connect(self._on_scan_filter_changed)
            filter_row.addWidget(cb)
            self.tier_checks[t] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(TransLabel("Enchant:"))
        self.enchant_checks = {}
        for e in BM_ENCHANTS:
            cb = QCheckBox(f".{e}")
            cb.setChecked(_load_bool(f"bm/enchant/{e}", True))
            cb.stateChanged.connect(self._on_scan_filter_changed)
            filter_row.addWidget(cb)
            self.enchant_checks[e] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(TransLabel("Category:"))
        self.group_checks = {}
        for group in SLOT_GROUPS:
            cb = QCheckBox(tr(group))
            cb.setChecked(_load_bool(f"bm/group/{group}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.group_checks[group] = cb

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Source cities (Caerleon excluded — BM is there, doesn't make sense as source)
        city_row = QHBoxLayout()
        city_row.addWidget(TransLabel("Source cities:"))
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
        self.refresh_btn = TransButton("Refresh prices")
        self.refresh_btn.clicked.connect(self._refresh)
        action_row.addWidget(self.refresh_btn)

        action_row.addSpacing(15)
        action_row.addWidget(TransLabel("Min margin:"))
        self.min_margin = QSpinBox()
        self.min_margin.setRange(0, 99_999_999)
        self.min_margin.setSingleStep(10_000)
        self.min_margin.setValue(int(settings.value("bm/min_margin", 100_000)))
        self.min_margin.valueChanged.connect(self._on_min_margin_changed)
        action_row.addWidget(self.min_margin)

        action_row.addSpacing(15)
        self.auto_refresh = TransCheck("Auto-refresh every")
        self.auto_refresh.setChecked(_load_bool("bm/auto_refresh", False))
        self.auto_refresh.stateChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_refresh)
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 120)
        self.auto_interval.setValue(int(settings.value("bm/auto_interval", 10)))
        self.auto_interval.setSuffix(" min")
        self.auto_interval.valueChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_interval)

        self.status = QLabel(tr("Idle."))
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        # Table
        self.table = QTableWidget(0, 9)
        self._set_headers()
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

    def _set_headers(self):
        self.table.setHorizontalHeaderLabels(
            [tr("Item"), tr("Quality"), tr("Buy in"), tr("Buy price"), tr("BM price"),
             tr("Net (after 4% tax)"), tr("Margin"), tr("Vol/day"), tr("Data age")]
        )

    def retranslate(self):
        retranslate_children(self)
        self._set_headers()
        for group, cb in self.group_checks.items():
            cb.setText(tr(group))
        self._apply_filters()

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
            self.status.setText(tr("Pick at least one source city."))
            return
        tiers = [t for t, cb in self.tier_checks.items() if cb.isChecked()]
        enchants = [e for e, cb in self.enchant_checks.items() if cb.isChecked()]
        if not tiers or not enchants:
            self.status.setText(tr("Pick at least one tier and enchant."))
            return
        # A scan is already in flight — don't spawn an overlapping QThread (that
        # would destroy the running one and crash the app). Queue one more pass.
        if self._worker is not None and self._worker.isRunning():
            self._pending = True
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText(
            tr("Scanning tiers {tiers} enchants {enchants} across {n} cities + Black Market…").format(
                tiers=tiers, enchants=enchants, n=len(cities))
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
        self.status.setText(tr("Error: {msg}").format(msg=msg))
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
            tr("Showing {shown} of {total} flips (margin ≥ {margin}) — scanned {ts}.").format(
                shown=len(filtered), total=len(self.all_results),
                margin=_fmt_silver(min_margin), ts=self._last_scan_ts)
        )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(filtered))
        for row_i, r in enumerate(filtered):
            age_text = f"{_fmt_age(r['buy_age'])} / {_fmt_age(r['bm_age'])}"
            age_tooltip = tr("City price: {city}\nBM price: {bm}").format(
                city=_abs_timestamp(r['buy_age']), bm=_abs_timestamp(r['bm_age']))
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
                    tr("Volume only fetched for the top flips by margin — "
                       "raise Min margin or check this item in-game")
                )
            else:
                if vol >= 20:
                    vol_item.setForeground(QColor("#2e7d32"))
                elif vol >= 5:
                    vol_item.setForeground(QColor("#b08800"))
                else:
                    vol_item.setForeground(QColor("#c62828"))
                vol_item.setToolTip(tr("Avg units sold to BM per day (last 7 days)"))

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

        info = TransLabel(
            "Buy a resource at its cheapest city → haul it → list your own sell order "
            "in the priciest city. Margin = destination price × (1 − 6.5% tax/fee) − buy price."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Filters (all client-side)
        filter_row = QHBoxLayout()
        filter_row.addWidget(TransLabel("Tier:"))
        self.tier_checks = {}
        for t in TIERS:
            cb = QCheckBox(f"T{t}")
            cb.setChecked(_load_bool(f"haul/tier/{t}", t >= 4))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.tier_checks[t] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(TransLabel("Enchant:"))
        self.enchant_checks = {}
        for e in RESOURCE_ENCHANTS:
            cb = QCheckBox(f".{e}")
            cb.setChecked(_load_bool(f"haul/enchant/{e}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.enchant_checks[e] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(TransLabel("Type:"))
        self.kind_checks = {}
        for k in self.KINDS:
            cb = QCheckBox(tr(k))
            cb.setChecked(_load_bool(f"haul/kind/{k}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.kind_checks[k] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(TransLabel("Material:"))
        self.mat_checks = {}
        for mat in RAW_TO_REFINED:
            cb = QCheckBox(tr(mat.title()))
            cb.setChecked(_load_bool(f"haul/mat/{mat}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.mat_checks[mat] = cb

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Cities (drive the fetch — changing these rescans)
        city_row = QHBoxLayout()
        city_row.addWidget(TransLabel("Cities:"))
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
        self.refresh_btn = TransButton("Refresh prices")
        self.refresh_btn.clicked.connect(self._refresh)
        action_row.addWidget(self.refresh_btn)

        action_row.addSpacing(15)
        action_row.addWidget(TransLabel("Min margin:"))
        self.min_margin = QSpinBox()
        self.min_margin.setRange(-999_999, 9_999_999)
        self.min_margin.setSingleStep(50)
        self.min_margin.setValue(int(settings.value("haul/min_margin", 0)))
        self.min_margin.valueChanged.connect(self._on_filter_changed)
        action_row.addWidget(self.min_margin)

        action_row.addSpacing(15)
        self.auto_refresh = TransCheck("Auto-refresh every")
        self.auto_refresh.setChecked(_load_bool("haul/auto_refresh", False))
        self.auto_refresh.stateChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_refresh)
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 120)
        self.auto_interval.setValue(int(settings.value("haul/auto_interval", 10)))
        self.auto_interval.setSuffix(" min")
        self.auto_interval.valueChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_interval)

        self.status = QLabel(tr("Idle."))
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        # Table
        self.table = QTableWidget(0, 11)
        self._set_headers()
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

    def _set_headers(self):
        self.table.setHorizontalHeaderLabels(
            [
                tr("Item"),
                tr("Type"),
                tr("Buy in"),
                tr("Buy price"),
                tr("Sell in"),
                tr("Sell price"),
                tr("Net (after 6.5%)"),
                tr("Margin"),
                tr("ROI"),
                tr("Vol/day"),
                tr("Data age"),
            ]
        )

    def retranslate(self):
        retranslate_children(self)
        self._set_headers()
        for k, cb in self.kind_checks.items():
            cb.setText(tr(k))
        for mat, cb in self.mat_checks.items():
            cb.setText(tr(mat.title()))
        self._apply_filters()

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
            self.status.setText(tr("Pick at least two cities (a haul needs a source and a destination)."))
            return
        if self._worker is not None and self._worker.isRunning():
            self._pending = True
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText(tr("Fetching resource prices across {n} cities…").format(n=len(cities)))
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
        self.status.setText(tr("Error: {msg}").format(msg=msg))
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
            tr("Showing {shown} of {total} hauls (margin ≥ {margin}) — scanned {ts}.").format(
                shown=len(filtered), total=len(self.all_results),
                margin=_fmt_silver(min_margin), ts=self._last_scan_ts)
        )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(filtered))
        for row_i, r in enumerate(filtered):
            age_text = f"{_fmt_age(r['buy_age'])} / {_fmt_age(r['sell_age'])}"
            age_tooltip = tr("Buy price: {buy}\nSell price: {sell}").format(
                buy=_abs_timestamp(r['buy_age']), sell=_abs_timestamp(r['sell_age']))
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
                    tr("Volume only fetched for the top hauls by margin — "
                       "raise Min margin or check this item in-game")
                )
            else:
                if vol >= 20:
                    vol_item.setForeground(QColor("#2e7d32"))
                elif vol >= 5:
                    vol_item.setForeground(QColor("#b08800"))
                else:
                    vol_item.setForeground(QColor("#c62828"))
                vol_item.setToolTip(
                    tr("Avg units sold per day in {city} (last 7 days)").format(city=r['sell_city'])
                )

            age_item = QTableWidgetItem(age_text)
            age_item.setToolTip(age_tooltip)

            self.table.setItem(row_i, 0, QTableWidgetItem(r["label"]))
            self.table.setItem(row_i, 1, QTableWidgetItem(tr(r["kind"])))
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
        lines = [tr("=== {label} (gathered) ===").format(label=r['label']), ""]
        lines.append(tr("Recommended: {action} — {v} silver per gathered unit").format(
            action=tr(r['best_action']), v=f"{r['best_value']:,}"))
        lines.append(tr("(Net of 6.5% sell-order tax/fee. Raws are free; refine path buys the lesser material.)"))
        lines.append("")

        lines.append(tr("--- Option A: Sell raw ---"))
        if r["raw_value"] is not None:
            lines.append(
                tr("List raw in {city} @ {price}  ->  x0.935 = {v} /unit").format(
                    city=r['raw_sell_city'], price=f"{r['raw_sell_price']:,}",
                    v=f"{int(round(r['raw_value'])):,}")
            )
        else:
            lines.append(tr("No live raw sell price."))
        lines.append("")

        lines.append(tr("--- Option B: Refine -> sell ---"))
        if r["refine_value"] is not None:
            rr = r["return_rate"]
            eff_raws = r["raws_per"] * (1 - rr)
            eff_prev = r["prev_per"] * (1 - rr)
            where = (
                tr("{city} (bonus, {pct}% return)").format(
                    city=r['refine_city'], pct=f"{rr * 100:.1f}")
                if r["refine_city"]
                else tr("any royal city ({pct}% return)").format(pct=f"{rr * 100:.1f}")
            )
            lines.append(tr("Refine in {where}").format(where=where))
            recipe = tr("{raws} raws (gathered)").format(raws=r['raws_per'])
            if r["prev_per"] > 0:
                recipe += f" + {r['prev_per']} T{r['tier'] - 1} {r['refined_name']}"
            lines.append(tr("Recipe per refined unit: {recipe}").format(recipe=recipe))
            if r["prev_per"] > 0:
                if r["prev_buy_price"]:
                    lines.append(
                        tr("Buy T{tier} {name} in {city} @ {price}  (effective {eff}/refined after return)").format(
                            tier=r['tier'] - 1, name=r['refined_name'], city=r['prev_buy_city'],
                            price=f"{r['prev_buy_price']:,}", eff=f"{eff_prev:.2f}")
                    )
                else:
                    lines.append(tr("T{tier} {name}: no live price").format(
                        tier=r['tier'] - 1, name=r['refined_name']))
            refined_net = r["refine_sell_price"] * (1 - 0.065)
            lines.append(
                tr("Sell refined in {city} @ {price}  ->  x0.935 = {v}").format(
                    city=r['refine_sell_city'], price=f"{r['refine_sell_price']:,}",
                    v=f"{int(round(refined_net)):,}")
            )
            prev_cost = eff_prev * (r["prev_buy_price"] or 0)
            lines.append(
                tr("Net per refined unit = {net} - {cost} input = {result}").format(
                    net=f"{int(round(refined_net)):,}", cost=f"{int(round(prev_cost)):,}",
                    result=f"{int(round(refined_net - prev_cost)):,}")
            )
            lines.append(
                tr("Per gathered raw unit (/ {raws} raws) = {v} /unit").format(
                    raws=f"{eff_raws:.2f}", v=f"{int(round(r['refine_value'])):,}")
            )
        else:
            lines.append(tr("Refine path unavailable (missing refined or lesser-material price)."))
        lines.append("")

        lines.append(tr("--- Data freshness ---"))
        if r["raw_age"] is not None:
            lines.append(tr("Raw price: {age} old ({ts})").format(
                age=_fmt_age(r['raw_age']), ts=_abs_timestamp(r['raw_age'])))
        if r["refine_age"] is not None:
            lines.append(
                tr("Refined price: {age} old ({ts})").format(
                    age=_fmt_age(r['refine_age']), ts=_abs_timestamp(r['refine_age']))
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

        info = TransLabel(
            "You gather the raws for free — this ranks each resource by net silver per "
            "gathered unit, picking the better of selling it raw or refining it and selling "
            "the bar/cloth/etc. The refine path buys the lesser material at market and includes "
            "the 6.5% sell-order tax. Double-click a row for the full breakdown."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Filters (all client-side)
        filter_row = QHBoxLayout()
        filter_row.addWidget(TransLabel("Tier:"))
        self.tier_checks = {}
        for t in TIERS:
            cb = QCheckBox(f"T{t}")
            cb.setChecked(_load_bool(f"gather/tier/{t}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.tier_checks[t] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(TransLabel("Enchant:"))
        self.enchant_checks = {}
        for e in ENCHANTS:
            cb = QCheckBox(f".{e}")
            cb.setChecked(_load_bool(f"gather/enchant/{e}", e <= 1))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.enchant_checks[e] = cb

        filter_row.addSpacing(20)
        filter_row.addWidget(TransLabel("Material:"))
        self.mat_checks = {}
        for mat in RAW_TO_REFINED:
            cb = QCheckBox(tr(mat.title()))
            cb.setChecked(_load_bool(f"gather/mat/{mat}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            filter_row.addWidget(cb)
            self.mat_checks[mat] = cb

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Cities (drive the fetch)
        city_row = QHBoxLayout()
        city_row.addWidget(TransLabel("Cities:"))
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
        self.refresh_btn = TransButton("Refresh prices")
        self.refresh_btn.clicked.connect(self._refresh)
        action_row.addWidget(self.refresh_btn)

        action_row.addSpacing(15)
        action_row.addWidget(TransLabel("Min silver/unit:"))
        self.min_value = QSpinBox()
        self.min_value.setRange(0, 9_999_999)
        self.min_value.setSingleStep(100)
        self.min_value.setValue(int(settings.value("gather/min_value", 0)))
        self.min_value.valueChanged.connect(self._on_filter_changed)
        action_row.addWidget(self.min_value)

        action_row.addSpacing(15)
        self.auto_refresh = TransCheck("Auto-refresh every")
        self.auto_refresh.setChecked(_load_bool("gather/auto_refresh", False))
        self.auto_refresh.stateChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_refresh)
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 120)
        self.auto_interval.setValue(int(settings.value("gather/auto_interval", 10)))
        self.auto_interval.setSuffix(" min")
        self.auto_interval.valueChanged.connect(self._on_auto_refresh_toggled)
        action_row.addWidget(self.auto_interval)

        self.status = QLabel(tr("Idle."))
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        # Table
        self.table = QTableWidget(0, 9)
        self._set_headers()
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

    def _set_headers(self):
        self.table.setHorizontalHeaderLabels(
            [
                tr("Item"),
                tr("Best action"),
                tr("Net silver/unit"),
                tr("Sell raw /unit"),
                tr("Refine /unit"),
                tr("Sell in"),
                tr("Inputs to buy"),
                tr("Vol/day"),
                tr("Data age"),
            ]
        )

    def retranslate(self):
        retranslate_children(self)
        self._set_headers()
        for mat, cb in self.mat_checks.items():
            cb.setText(tr(mat.title()))
        self._apply_filters()

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
            self.status.setText(tr("Pick at least one city."))
            return
        if self._worker is not None and self._worker.isRunning():
            self._pending = True
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText(tr("Scanning gather options across {n} cities…").format(n=len(cities)))
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
        self.status.setText(tr("Error: {msg}").format(msg=msg))
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
            tr("Showing {shown} of {total} resources (≥ {v}/unit) — scanned {ts}.").format(
                shown=len(filtered), total=len(self.all_results),
                v=_fmt_silver(min_value), ts=self._last_scan_ts)
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
                inputs = tr("none")
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
                    tr("Volume only fetched for the top options — "
                       "raise Min silver/unit or check this item in-game")
                )
            else:
                if vol >= 20:
                    vol_item.setForeground(QColor("#2e7d32"))
                elif vol >= 5:
                    vol_item.setForeground(QColor("#b08800"))
                else:
                    vol_item.setForeground(QColor("#c62828"))
                vol_item.setToolTip(
                    tr("Avg units of the listed item sold per day in {city} (last 7 days)").format(city=sell_city)
                )

            self.table.setItem(row_i, 0, QTableWidgetItem(r["label"]))
            self.table.setItem(row_i, 1, QTableWidgetItem(tr(r["best_action"])))
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


class BundleCheckDialog(QDialog):
    """Verify the specific items in a bundle/trade someone is offering you.

    Search items by name, build a list, and get a per-item verdict (listed price
    vs real traded value) so a planted overpriced item in the bundle stands out.
    """

    def __init__(self, cities, parent=None):
        super().__init__(parent)
        self.cities = cities
        self._worker = None
        self.setWindowTitle(tr("Check a bundle / trade"))
        self.resize(900, 640)

        layout = QVBoxLayout(self)
        intro = QLabel(
            tr("Search the items someone is offering you, add them to the list, and check "
               "them against their real traded value. Uses your selected cities: {cities}.").format(
                cities=', '.join(cities))
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Search (left) + bundle list (right).
        picker = QHBoxLayout()

        left = QVBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Type an item name, e.g. Rotcaller, Royal Cowl…"))
        self.search_box.textChanged.connect(self._on_search)
        left.addWidget(self.search_box)
        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(self._add_item)
        left.addWidget(self.results_list)
        picker.addLayout(left)

        mid = QVBoxLayout()
        mid.addStretch()
        add_btn = QPushButton(tr("Add →"))
        add_btn.clicked.connect(lambda: self._add_item(self.results_list.currentItem()))
        mid.addWidget(add_btn)
        rm_btn = QPushButton(tr("← Remove"))
        rm_btn.clicked.connect(self._remove_item)
        mid.addWidget(rm_btn)
        mid.addStretch()
        picker.addLayout(mid)

        right = QVBoxLayout()
        right.addWidget(QLabel(tr("Items to check:")))
        self.bundle_list = QListWidget()
        self.bundle_list.itemDoubleClicked.connect(lambda _: self._remove_item())
        right.addWidget(self.bundle_list)
        picker.addLayout(right)

        layout.addLayout(picker)

        action_row = QHBoxLayout()
        self.check_btn = QPushButton(tr("Check bundle"))
        self.check_btn.clicked.connect(self._check)
        action_row.addWidget(self.check_btn)
        clear_btn = QPushButton(tr("Clear list"))
        clear_btn.clicked.connect(self.bundle_list.clear)
        action_row.addWidget(clear_btn)
        self.status = QLabel(tr("Add items, then Check bundle."))
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                tr("Item"),
                tr("Tier"),
                tr("Quality"),
                tr("City"),
                tr("Listed price"),
                tr("Fair value"),
                tr("Spike ×"),
                tr("Vol/day"),
                tr("Verdict"),
                tr("Age"),
            ]
        )
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

    def _on_search(self, text):
        self.results_list.clear()
        for iid in names.search_ids(text):
            meta = names.parse_id(iid)
            label = f"{names.get_name(iid)}  (T{meta['tier']}.{meta['enchant']})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, iid)
            self.results_list.addItem(item)

    def _add_item(self, item):
        if item is None:
            return
        iid = item.data(Qt.UserRole)
        # Skip if already in the bundle.
        for i in range(self.bundle_list.count()):
            if self.bundle_list.item(i).data(Qt.UserRole) == iid:
                return
        new = QListWidgetItem(item.text())
        new.setData(Qt.UserRole, iid)
        self.bundle_list.addItem(new)

    def _remove_item(self):
        row = self.bundle_list.currentRow()
        if row >= 0:
            self.bundle_list.takeItem(row)

    def _check(self):
        ids = [
            self.bundle_list.item(i).data(Qt.UserRole)
            for i in range(self.bundle_list.count())
        ]
        if not ids:
            self.status.setText(tr("Add at least one item first."))
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.check_btn.setEnabled(False)
        self.status.setText(tr("Checking {n} item(s)…").format(n=len(ids)))
        worker = BundleCheckWorker(ids, self.cities)
        worker.finished_ok.connect(self._on_results)
        worker.failed.connect(self._on_error)
        worker.finished.connect(self._on_worker_done)
        self._worker = worker
        worker.start()

    def _on_error(self, msg):
        self.status.setText(tr("Error: {msg}").format(msg=msg))
        self.check_btn.setEnabled(True)

    def _on_worker_done(self):
        self._worker = None

    def _on_results(self, results):
        self.check_btn.setEnabled(True)
        self.status.setText(
            tr("{n} listing(s) found. Most suspicious first.").format(n=len(results))
            if results
            else tr("No live listings found for those items in the selected cities.")
        )
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(results))
        for row_i, r in enumerate(results):
            tier_label = f"T{r['tier']}.{r['enchant']}" if r["tier"] else "—"
            spike = r["spike"]
            spike_text = "—" if spike is None else f"{spike:.1f}×"
            spike_item = NumericItem(spike_text, spike)
            if spike is not None:
                if spike >= 5:
                    spike_item.setForeground(QColor("#b71c1c"))
                elif spike >= 3:
                    spike_item.setForeground(QColor("#c62828"))
                elif spike >= 1.5:
                    spike_item.setForeground(QColor("#b08800"))
                else:
                    spike_item.setForeground(QColor("#2e7d32"))

            baseline = r["baseline"]
            base_text = "—" if baseline is None else _fmt_silver(baseline)
            vol = r["vol_per_day"]
            vol_text = "—" if vol is None else str(vol)

            verdict_item = QTableWidgetItem(r["verdict"])
            v = r["verdict"]
            if v.startswith("🚩"):
                verdict_item.setForeground(QColor("#b71c1c"))
            elif v.startswith("⚠"):
                verdict_item.setForeground(QColor("#c62828"))
            elif v.startswith("Slightly"):
                verdict_item.setForeground(QColor("#b08800"))
            elif v.startswith("✓") or v.startswith("Below"):
                verdict_item.setForeground(QColor("#2e7d32"))
            else:
                verdict_item.setForeground(QColor("#888888"))

            name_item = QTableWidgetItem(r["name"])
            name_item.setToolTip(r["id"])

            self.table.setItem(row_i, 0, name_item)
            self.table.setItem(row_i, 1, QTableWidgetItem(tier_label))
            self.table.setItem(row_i, 2, QTableWidgetItem(QUALITIES.get(r["quality"], "?")))
            self.table.setItem(row_i, 3, QTableWidgetItem(r["city"]))
            self.table.setItem(row_i, 4, NumericItem(_fmt_silver(r["current"]), r["current"]))
            self.table.setItem(row_i, 5, NumericItem(base_text, baseline))
            self.table.setItem(row_i, 6, spike_item)
            self.table.setItem(row_i, 7, NumericItem(vol_text, -1 if vol is None else vol))
            self.table.setItem(row_i, 8, verdict_item)
            self.table.setItem(row_i, 9, QTableWidgetItem(_fmt_age(r["age"])))
        self.table.setSortingEnabled(True)


class ScamTab(QWidget):
    """Flag gear listings priced far above their real recent traded value.

    Catches the bundle/trade scam where one wildly overpriced item inflates a
    deal's apparent worth. Spike x = current listing ÷ history-based fair value.
    Tiers/enchants/cities drive the fetch; spike threshold, min inflation, min
    volume and slot group are instant client-side filters (no rescan).
    """

    def __init__(self):
        super().__init__()
        self.all_results: list[dict] = []
        self._worker = None
        self._pending = False
        self._last_scan_ts = "—"

        layout = QVBoxLayout(self)

        info = TransLabel(
            "Scans the market for items listed FAR above their real recent traded price — "
            "the planted/overpriced listings used in bundle & trade scams. Spike × = current "
            "listing ÷ fair value (volume-weighted 30-day traded average). A high spike on a "
            "low-volume item is almost certainly fake."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Fetch-driving filters: tier + enchant (gear universe).
        gear_row = QHBoxLayout()
        gear_row.addWidget(TransLabel("Tier:"))
        self.tier_checks = {}
        for t in BM_TIERS:
            cb = QCheckBox(f"T{t}")
            cb.setChecked(_load_bool(f"scam/tier/{t}", True))
            cb.stateChanged.connect(self._on_city_changed)
            gear_row.addWidget(cb)
            self.tier_checks[t] = cb

        gear_row.addSpacing(20)
        gear_row.addWidget(TransLabel("Enchant:"))
        self.enchant_checks = {}
        for e in BM_ENCHANTS:
            cb = QCheckBox(f".{e}")
            cb.setChecked(_load_bool(f"scam/enchant/{e}", e <= 1))
            cb.stateChanged.connect(self._on_city_changed)
            gear_row.addWidget(cb)
            self.enchant_checks[e] = cb

        gear_row.addSpacing(20)
        gear_row.addWidget(TransLabel("Slot:"))
        self.group_checks = {}
        for g in SLOT_GROUPS:
            cb = QCheckBox(tr(g))
            cb.setChecked(_load_bool(f"scam/group/{g}", True))
            cb.stateChanged.connect(self._on_filter_changed)
            gear_row.addWidget(cb)
            self.group_checks[g] = cb
        gear_row.addStretch()
        layout.addLayout(gear_row)

        # Cities (drive the fetch).
        city_row = QHBoxLayout()
        city_row.addWidget(TransLabel("Cities:"))
        self.city_checks = {}
        for city in CITIES:
            cb = QCheckBox(city)
            cb.setChecked(_load_bool(f"scam/city/{city}", city != "Caerleon"))
            cb.stateChanged.connect(self._on_city_changed)
            city_row.addWidget(cb)
            self.city_checks[city] = cb
        city_row.addStretch()
        layout.addLayout(city_row)

        # Actions + client-side thresholds.
        action_row = QHBoxLayout()
        self.refresh_btn = TransButton("Scan market")
        self.refresh_btn.clicked.connect(self._refresh)
        action_row.addWidget(self.refresh_btn)

        self.bundle_btn = TransButton("Check a bundle…")
        self.bundle_btn.setToolTip(
            tr("Verify the specific items in a trade someone is offering you, item by item.")
        )
        self.bundle_btn.clicked.connect(self._open_bundle_check)
        action_row.addWidget(self.bundle_btn)

        action_row.addSpacing(15)
        action_row.addWidget(TransLabel("Min spike ×:"))
        self.min_spike = QDoubleSpinBox()
        self.min_spike.setRange(1.0, 1000.0)
        self.min_spike.setSingleStep(0.5)
        self.min_spike.setDecimals(1)
        self.min_spike.setValue(float(settings.value("scam/min_spike", 3.0)))
        self.min_spike.valueChanged.connect(self._on_filter_changed)
        action_row.addWidget(self.min_spike)

        action_row.addSpacing(10)
        action_row.addWidget(TransLabel("Min inflation:"))
        self.min_inflation = QSpinBox()
        self.min_inflation.setRange(0, 999_999_999)
        self.min_inflation.setSingleStep(5_000)
        self.min_inflation.setValue(int(settings.value("scam/min_inflation", 10_000)))
        action_row.addWidget(self.min_inflation)
        self.min_inflation.valueChanged.connect(self._on_filter_changed)

        action_row.addSpacing(10)
        action_row.addWidget(TransLabel("Max vol/day:"))
        self.max_vol = QSpinBox()
        self.max_vol.setRange(0, 999_999)
        self.max_vol.setSingleStep(5)
        self.max_vol.setValue(int(settings.value("scam/max_vol", 0)))
        self.max_vol.setSpecialValueText(tr("any"))  # 0 = no volume cap
        self.max_vol.setToolTip(
            tr("Only show items trading at or below this many units/day.\n"
               "Fake prices live in thin markets — set e.g. 5 to focus on them. 'any' = no cap.")
        )
        self.max_vol.valueChanged.connect(self._on_filter_changed)
        action_row.addWidget(self.max_vol)

        self.status = QLabel(tr("Idle."))
        action_row.addSpacing(10)
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.table = QTableWidget(0, 10)
        self._set_headers()
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        self._refresh()

    def _set_headers(self):
        self.table.setHorizontalHeaderLabels(
            [
                tr("Item"),
                tr("Tier"),
                tr("Quality"),
                tr("City"),
                tr("Listed price"),
                tr("Fair value"),
                tr("Inflation"),
                tr("Spike ×"),
                tr("Vol/day"),
                tr("Data age"),
            ]
        )

    def retranslate(self):
        retranslate_children(self)
        self._set_headers()
        for g, cb in self.group_checks.items():
            cb.setText(tr(g))
        self.bundle_btn.setToolTip(
            tr("Verify the specific items in a trade someone is offering you, item by item.")
        )
        self.max_vol.setSpecialValueText(tr("any"))
        self.max_vol.setToolTip(
            tr("Only show items trading at or below this many units/day.\n"
               "Fake prices live in thin markets — set e.g. 5 to focus on them. 'any' = no cap.")
        )
        self._apply_filters()

    def save_state(self):
        for t, cb in self.tier_checks.items():
            settings.setValue(f"scam/tier/{t}", cb.isChecked())
        for e, cb in self.enchant_checks.items():
            settings.setValue(f"scam/enchant/{e}", cb.isChecked())
        for g, cb in self.group_checks.items():
            settings.setValue(f"scam/group/{g}", cb.isChecked())
        for c, cb in self.city_checks.items():
            settings.setValue(f"scam/city/{c}", cb.isChecked())
        settings.setValue("scam/min_spike", self.min_spike.value())
        settings.setValue("scam/min_inflation", self.min_inflation.value())
        settings.setValue("scam/max_vol", self.max_vol.value())

    def _open_bundle_check(self):
        cities = [c for c, cb in self.city_checks.items() if cb.isChecked()]
        if not cities:
            self.status.setText(tr("Pick at least one city first (bundle check uses them)."))
            return
        dlg = BundleCheckDialog(cities, parent=self)
        dlg.exec()

    def _on_filter_changed(self):
        # Client-side only — no network rescan.
        self.save_state()
        self._apply_filters()

    def _on_city_changed(self):
        # Tier / enchant / city drive the fetch.
        self.save_state()
        self._refresh()

    def _refresh(self):
        cities = [c for c, cb in self.city_checks.items() if cb.isChecked()]
        tiers = [t for t, cb in self.tier_checks.items() if cb.isChecked()]
        enchants = [e for e, cb in self.enchant_checks.items() if cb.isChecked()]
        if not cities or not tiers or not enchants:
            self.status.setText(tr("Pick at least one city, tier and enchant."))
            return
        if self._worker is not None and self._worker.isRunning():
            self._pending = True
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText(tr("Scanning {tn} tier(s) across {cn} cities…").format(
            tn=len(tiers), cn=len(cities)))
        worker = ScamWorker(cities, tiers, enchants)
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
        self.status.setText(tr("Error: {msg}").format(msg=msg))
        self.refresh_btn.setEnabled(True)

    def _on_worker_done(self):
        self._worker = None
        if self._pending:
            self._pending = False
            self._refresh()

    def _apply_filters(self):
        min_spike = self.min_spike.value()
        min_inflation = self.min_inflation.value()
        max_vol = self.max_vol.value()  # 0 = no cap
        groups = {g for g, cb in self.group_checks.items() if cb.isChecked()}

        filtered = [
            r
            for r in self.all_results
            if r["spike"] >= min_spike
            and r["inflation"] >= min_inflation
            and r["group"] in groups
            and (max_vol == 0 or r["vol_per_day"] <= max_vol)
        ]
        self._filtered_rows = filtered
        self.status.setText(
            tr("Showing {shown} of {total} flagged listings (spike ≥ {spike}×) — scanned {ts}.").format(
                shown=len(filtered), total=len(self.all_results),
                spike=f"{min_spike:.1f}", ts=self._last_scan_ts)
        )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(filtered))
        for row_i, r in enumerate(filtered):
            tier_label = f"T{r['tier']}.{r['enchant']}"
            spike = r["spike"]
            spike_item = NumericItem(f"{spike:.1f}×", spike)
            if spike >= 10:
                spike_item.setForeground(QColor("#b71c1c"))  # deep red
            elif spike >= 5:
                spike_item.setForeground(QColor("#c62828"))  # red
            else:
                spike_item.setForeground(QColor("#b08800"))  # amber
            spike_item.setToolTip(
                tr("Listed at {current}, but the 30-day traded average is {baseline} — "
                   "{spike}× over fair value.").format(
                    current=_fmt_silver(r['current']), baseline=_fmt_silver(r['baseline']),
                    spike=f"{spike:.1f}")
            )

            vol = r["vol_per_day"]
            vol_item = NumericItem(str(vol), vol)
            # Low volume + high spike = almost certainly a planted price.
            if vol < 5:
                vol_item.setForeground(QColor("#c62828"))
                vol_item.setToolTip(
                    tr("Very thin market — a fake price here can sit unchallenged. "
                       "Strong corroboration that this listing is bogus.")
                )
            elif vol < 20:
                vol_item.setForeground(QColor("#b08800"))
            else:
                vol_item.setForeground(QColor("#2e7d32"))
                vol_item.setToolTip(
                    tr("Actively traded — a real (if temporary) price spike is more "
                       "plausible here than in a thin market.")
                )

            age_item = QTableWidgetItem(_fmt_age(r["age"]))
            age_item.setToolTip(tr("Listing seen: {ts}").format(ts=_abs_timestamp(r['age'])))

            name_item = QTableWidgetItem(r["name"])
            name_item.setToolTip(r["id"])

            self.table.setItem(row_i, 0, name_item)
            self.table.setItem(row_i, 1, QTableWidgetItem(tier_label))
            self.table.setItem(row_i, 2, QTableWidgetItem(QUALITIES.get(r["quality"], "?")))
            self.table.setItem(row_i, 3, QTableWidgetItem(r["city"]))
            self.table.setItem(row_i, 4, NumericItem(_fmt_silver(r["current"]), r["current"]))
            self.table.setItem(row_i, 5, NumericItem(_fmt_silver(r["baseline"]), r["baseline"]))
            self.table.setItem(row_i, 6, NumericItem(_fmt_silver(r["inflation"]), r["inflation"]))
            self.table.setItem(row_i, 7, spike_item)
            self.table.setItem(row_i, 8, vol_item)
            self.table.setItem(row_i, 9, age_item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(7, Qt.DescendingOrder)


class CraftDetailDialog(QDialog):
    def __init__(self, parent, row: dict):
        super().__init__(parent)
        self.setWindowTitle(tr("{name} — craft breakdown").format(name=row['name']))
        self.resize(620, 480)
        layout = QVBoxLayout(self)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self._build_text(row))
        layout.addWidget(text)

    @staticmethod
    def _build_text(r: dict) -> str:
        rr = r["return_rate"]
        lines = [f"=== {r['name']}  (T{r['tier']}.{r['enchant']}) ===", ""]
        if r["no_recipe"]:
            lines.append(tr("No known crafting recipe for this item."))
            return "\n".join(lines)
        lines.append(tr("Resource return rate: {pct}%  (refunds returnable materials)").format(
            pct=f"{rr * 100:.1f}"))
        lines.append("")
        lines.append(tr("--- Materials (bought instant at cheapest selected city) ---"))
        for m in r["materials"]:
            ret_note = "" if m["ret"] else tr("  [artifact/token — never refunded]")
            if m["unit_price"] is None:
                lines.append(tr("{count}x {name}: no live price{ret_note}").format(
                    count=m['count'], name=m['name'], ret_note=ret_note))
            else:
                lines.append(
                    tr("{count}x {name} @ {price} in {city}  ->  effective {eff} x {price} = {cost}{ret_note}").format(
                        count=m['count'], name=m['name'], price=f"{m['unit_price']:,}", city=m['city'],
                        eff=f"{m['eff_count']:.2f}", cost=f"{int(round(m['cost'])):,}", ret_note=ret_note)
                )
        lines.append("")
        cc = r["craft_cost"]
        lines.append(tr("Total craft cost: {v}").format(
            v=('—' if cc is None else format(int(round(cc)), ','))))
        lines.append("")
        lines.append(tr("--- Sell ---"))
        if r["net_sell"] is None:
            lines.append(tr("No live sell price (no city listing and no Black Market buy order)."))
        else:
            tax = tr("4% BM tax") if r["sell_venue"] == "Black Market" else tr("6.5% sell-order tax/fee")
            lines.append(
                tr("Best: {venue} @ {price}  ->  net {net} (after {tax})").format(
                    venue=tr(r['sell_venue']), price=f"{r['sell_price']:,}",
                    net=f"{int(round(r['net_sell'])):,}", tax=tax)
            )
        lines.append("")
        if r["profit"] is not None:
            lines.append(tr("PROFIT per craft: {v}").format(v=f"{int(round(r['profit'])):,}"))
            if r["roi"] is not None:
                lines.append(tr("ROI on materials: {pct}%").format(pct=f"{r['roi'] * 100:.1f}"))
        else:
            lines.append(tr("Profit unavailable — missing: {missing}").format(
                missing=', '.join(r['missing'])))
        lines.append("")
        lines.append(tr("Note: assumes Normal quality and ignores station usage fees."))
        return "\n".join(lines)


class CraftTab(QWidget):
    """Search any craftable gear and see its net craft profit.

    Buy materials instant at the cheapest selected city (return rate refunds the
    returnable ones), then sell the crafted item the better of: list in a city
    (net 6.5%) or instant-sell to the Black Market (net 4%). The return rate is
    driven by the crafting-city / focus / bonus-day toggles.
    """

    def __init__(self):
        super().__init__()
        self._worker = None
        self.results: list[dict] = []

        layout = QVBoxLayout(self)
        info = TransLabel(
            "Search any craftable item, add it to the list, and check the net profit of "
            "crafting it. Materials are bought instant at the cheapest selected city; the "
            "crafted item is sold the better of a city listing (net 6.5%) or the Black Market "
            "(net 4%). Set the return rate with the toggles below."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Crafting bonus toggles -> return rate.
        opt_row = QHBoxLayout()
        self.spec_cb = TransCheck("Crafting bonus city (+15)")
        self.spec_cb.setChecked(_load_bool("craft/spec", False))
        self.spec_cb.setToolTip(tr("Crafting in the city that specializes in this item type."))
        self.spec_cb.stateChanged.connect(self._on_rate_changed)
        opt_row.addWidget(self.spec_cb)

        self.focus_cb = TransCheck("Use focus (+59)")
        self.focus_cb.setChecked(_load_bool("craft/focus", False))
        self.focus_cb.stateChanged.connect(self._on_rate_changed)
        opt_row.addWidget(self.focus_cb)

        opt_row.addSpacing(10)
        opt_row.addWidget(TransLabel("Bonus day:"))
        self.bonus_combo = QComboBox()
        for label in CRAFT_BONUS_DAY:
            self.bonus_combo.addItem(tr(label), label)  # display translated, value English
        saved_bonus = settings.value("craft/bonus_day", "None")
        idx = self.bonus_combo.findData(saved_bonus)
        self.bonus_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.bonus_combo.currentTextChanged.connect(self._on_rate_changed)
        opt_row.addWidget(self.bonus_combo)

        self.rate_label = QLabel()
        self.rate_label.setStyleSheet("font-weight: bold;")
        opt_row.addSpacing(15)
        opt_row.addWidget(self.rate_label)
        opt_row.addStretch()
        layout.addLayout(opt_row)

        # Cities (drive the fetch: where materials are bought / item listed).
        city_row = QHBoxLayout()
        city_row.addWidget(TransLabel("Cities:"))
        self.city_checks = {}
        for city in CITIES:
            cb = QCheckBox(city)
            cb.setChecked(_load_bool(f"craft/city/{city}", city != "Caerleon"))
            city_row.addWidget(cb)
            self.city_checks[city] = cb
        city_row.addStretch()
        layout.addLayout(city_row)

        # Search (left) + chosen items (right).
        picker = QHBoxLayout()
        left = QVBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Type an item name, e.g. Broadsword, Mercenary Jacket…"))
        self.search_box.textChanged.connect(self._on_search)
        left.addWidget(self.search_box)
        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(self._add_item)
        left.addWidget(self.results_list)
        picker.addLayout(left)

        mid = QVBoxLayout()
        mid.addStretch()
        add_btn = TransButton("Add →")
        add_btn.clicked.connect(lambda: self._add_item(self.results_list.currentItem()))
        mid.addWidget(add_btn)
        rm_btn = TransButton("← Remove")
        rm_btn.clicked.connect(self._remove_item)
        mid.addWidget(rm_btn)
        mid.addStretch()
        picker.addLayout(mid)

        right = QVBoxLayout()
        right.addWidget(TransLabel("Items to craft-check:"))
        self.craft_list = QListWidget()
        self.craft_list.itemDoubleClicked.connect(lambda _: self._remove_item())
        right.addWidget(self.craft_list)
        picker.addLayout(right)
        layout.addLayout(picker)

        action_row = QHBoxLayout()
        self.check_btn = TransButton("Check craft profit")
        self.check_btn.clicked.connect(self._check)
        action_row.addWidget(self.check_btn)
        clear_btn = TransButton("Clear list")
        clear_btn.clicked.connect(self.craft_list.clear)
        action_row.addWidget(clear_btn)
        self.status = QLabel(tr("Add items, then check."))
        action_row.addWidget(self.status)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.table = QTableWidget(0, 8)
        self._set_headers()
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(self._open_detail)
        layout.addWidget(self.table)

        self._update_rate_label()

    def _return_rate(self) -> float:
        return craft_return_rate(
            spec=self.spec_cb.isChecked(),
            focus=self.focus_cb.isChecked(),
            bonus_day=CRAFT_BONUS_DAY[self.bonus_combo.currentData()],
        )

    def _update_rate_label(self):
        self.rate_label.setText(tr("Return rate: {pct}%").format(pct=f"{self._return_rate() * 100:.1f}"))

    def _on_rate_changed(self):
        self._update_rate_label()
        self.save_state()

    def _set_headers(self):
        self.table.setHorizontalHeaderLabels(
            [tr("Item"), tr("Tier"), tr("Craft cost"), tr("Sell (net)"), tr("Venue"),
             tr("Profit"), tr("ROI %"), tr("Notes")]
        )

    def retranslate(self):
        retranslate_children(self)
        self._set_headers()
        self.spec_cb.setToolTip(tr("Crafting in the city that specializes in this item type."))
        for i in range(self.bonus_combo.count()):
            self.bonus_combo.setItemText(i, tr(self.bonus_combo.itemData(i)))
        self.search_box.setPlaceholderText(tr("Type an item name, e.g. Broadsword, Mercenary Jacket…"))
        self._update_rate_label()
        self._on_results(self.results)

    def save_state(self):
        settings.setValue("craft/spec", self.spec_cb.isChecked())
        settings.setValue("craft/focus", self.focus_cb.isChecked())
        settings.setValue("craft/bonus_day", self.bonus_combo.currentData())
        for c, cb in self.city_checks.items():
            settings.setValue(f"craft/city/{c}", cb.isChecked())

    def _on_search(self, text):
        self.results_list.clear()
        for iid in names.search_ids(text):
            meta = names.parse_id(iid)
            label = f"{names.get_name(iid)}  (T{meta['tier']}.{meta['enchant']})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, iid)
            self.results_list.addItem(item)

    def _add_item(self, item):
        if item is None:
            return
        iid = item.data(Qt.UserRole)
        for i in range(self.craft_list.count()):
            if self.craft_list.item(i).data(Qt.UserRole) == iid:
                return
        new = QListWidgetItem(item.text())
        new.setData(Qt.UserRole, iid)
        self.craft_list.addItem(new)

    def _remove_item(self):
        row = self.craft_list.currentRow()
        if row >= 0:
            self.craft_list.takeItem(row)

    def _check(self):
        ids = [
            self.craft_list.item(i).data(Qt.UserRole)
            for i in range(self.craft_list.count())
        ]
        if not ids:
            self.status.setText(tr("Add at least one item first."))
            return
        cities = [c for c, cb in self.city_checks.items() if cb.isChecked()]
        if not cities:
            self.status.setText(tr("Pick at least one city."))
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.save_state()
        self.check_btn.setEnabled(False)
        self.status.setText(tr("Checking {n} item(s)…").format(n=len(ids)))
        worker = CraftWorker(ids, cities, self._return_rate())
        worker.finished_ok.connect(self._on_results)
        worker.failed.connect(self._on_error)
        worker.finished.connect(self._on_worker_done)
        self._worker = worker
        worker.start()

    def _on_error(self, msg):
        self.status.setText(tr("Error: {msg}").format(msg=msg))
        self.check_btn.setEnabled(True)

    def _on_worker_done(self):
        self._worker = None

    def _open_detail(self, _item):
        row = self.table.currentRow()
        if 0 <= row < len(self.results):
            CraftDetailDialog(self, self.results[row]).exec()

    def _on_results(self, results):
        self.check_btn.setEnabled(True)
        self.results = results
        self.status.setText(
            tr("{n} item(s). Double-click a row for the material breakdown.").format(n=len(results))
            if results
            else tr("No results.")
        )
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(results))
        for row_i, r in enumerate(results):
            tier_label = f"T{r['tier']}.{r['enchant']}" if r["tier"] else "—"
            profit = r["profit"]
            profit_item = NumericItem(_silver_or_dash(profit), profit)
            if profit is not None:
                profit_item.setForeground(QColor("#2e7d32") if profit > 0 else QColor("#b71c1c"))
            roi = r["roi"]
            roi_item = NumericItem("—" if roi is None else f"{roi * 100:.1f}%", roi)
            if roi is not None:
                roi_item.setForeground(QColor("#2e7d32") if roi > 0 else QColor("#b71c1c"))

            if r["no_recipe"]:
                note = tr("no recipe")
            elif r["missing"]:
                note = tr("missing: ") + ", ".join(r["missing"])
            else:
                note = ""

            name_item = QTableWidgetItem(r["name"])
            name_item.setToolTip(r["id"])

            self.table.setItem(row_i, 0, name_item)
            self.table.setItem(row_i, 1, QTableWidgetItem(tier_label))
            self.table.setItem(row_i, 2, NumericItem(_silver_or_dash(r["craft_cost"]), r["craft_cost"]))
            self.table.setItem(row_i, 3, NumericItem(_silver_or_dash(r["net_sell"]), r["net_sell"]))
            self.table.setItem(row_i, 4, QTableWidgetItem(tr(r["sell_venue"]) if r["sell_venue"] else "—"))
            self.table.setItem(row_i, 5, profit_item)
            self.table.setItem(row_i, 6, roi_item)
            self.table.setItem(row_i, 7, QTableWidgetItem(note))
        self.table.setSortingEnabled(True)
        self.table.sortItems(5, Qt.DescendingOrder)


class CraftCalcEnchantPanel(QWidget):
    """One enchant level's calculator, shown as a sub-tab in the Craft calc tab.

    Owns its materials table, per-material price spins, sell venue + price and the
    profit/ROI summary. The return rate and batch size come from the shared
    controls on the parent CraftCalcTab; the parent recomputes every panel when
    those change. Material prices persist per material id, sell price per item id,
    so they auto-fill when you reopen the same item.
    """

    PRICE_MAX = 999_999_999
    STALE_AGE = 6 * 60 * 60  # older than this is flagged as stale after a fetch

    def __init__(self, tab, item_id: str, materials: list[dict]):
        super().__init__()
        self._tab = tab
        self.item_id = item_id
        self.materials = materials
        self.price_spins: dict[str, QSpinBox] = {}
        self._building = True

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("Material"), tr("Qty/ea"), tr("Unit price"), tr("Buy qty"), tr("Line cost")]
        )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        # Sell side (per enchant level — each sells at a different price).
        sell_row = QHBoxLayout()
        self.venue_label = QLabel(tr("Sell venue:"))
        sell_row.addWidget(self.venue_label)
        self.venue_combo = QComboBox()
        self.venue_combo.addItem(tr("City listing (6.5% tax)"), "city")
        self.venue_combo.addItem(tr("Black Market (4% tax)"), "bm")
        saved_venue = settings.value(
            f"craftcalc/sellvenue/{item_id}", settings.value("craftcalc/venue", "city")
        )
        vidx = self.venue_combo.findData(saved_venue)
        self.venue_combo.setCurrentIndex(vidx if vidx >= 0 else 0)
        self.venue_combo.currentTextChanged.connect(self._on_sell_changed)
        sell_row.addWidget(self.venue_combo)

        sell_row.addSpacing(10)
        self.sell_label = QLabel(tr("Sell price:"))
        sell_row.addWidget(self.sell_label)
        self.sell_spin = QSpinBox()
        self.sell_spin.setRange(0, self.PRICE_MAX)
        self.sell_spin.setSingleStep(100)
        self.sell_spin.setGroupSeparatorShown(True)
        self.sell_spin.valueChanged.connect(self._on_sell_changed)
        sell_row.addWidget(self.sell_spin)
        sell_row.addStretch()
        layout.addLayout(sell_row)

        # Summary line.
        summary = QHBoxLayout()
        self.cost_label = QLabel(tr("Craft cost: —"))
        self.net_label = QLabel(tr("Total income: —"))
        self.profit_label = QLabel(tr("Profit: —"))
        self.profit_label.setStyleSheet("font-weight: bold;")
        self.unit_profit_label = QLabel(tr("Profit/unit: —"))
        self.roi_label = QLabel(tr("ROI: —"))
        for w in (self.cost_label, self.net_label, self.profit_label,
                  self.unit_profit_label, self.roi_label):
            summary.addWidget(w)
            summary.addSpacing(20)
        summary.addStretch()
        layout.addLayout(summary)

        self._build_table()
        self.recompute()

    def _build_table(self):
        """(Re)populate the materials table + price spins from saved prices."""
        self._building = True
        self.price_spins = {}
        self.table.setRowCount(len(self.materials))
        for row, m in enumerate(self.materials):
            name = names.get_name(m["id"])
            if not m["ret"]:
                name += tr("  [artifact]")
            name_item = QTableWidgetItem(name)
            name_item.setToolTip(m["id"])
            self.table.setItem(row, 0, name_item)

            qty_item = QTableWidgetItem(str(m["count"]))
            qty_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 1, qty_item)

            spin = QSpinBox()
            spin.setRange(0, self.PRICE_MAX)
            spin.setSingleStep(100)
            spin.setGroupSeparatorShown(True)
            spin.setValue(int(settings.value(f"craftcalc/price/{m['id']}", 0)))
            spin.valueChanged.connect(lambda v, mid=m["id"]: self._on_price_changed(mid, v))
            self.table.setCellWidget(row, 2, spin)
            self.price_spins[m["id"]] = spin

            buy_item = QTableWidgetItem("")
            buy_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 3, buy_item)
            cost_item = QTableWidgetItem("")
            cost_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 4, cost_item)

        self.sell_spin.blockSignals(True)
        self.sell_spin.setValue(int(settings.value(f"craftcalc/sell/{self.item_id}", 0)))
        self.sell_spin.blockSignals(False)
        self._building = False

    def _on_price_changed(self, material_id: str, value: int):
        settings.setValue(f"craftcalc/price/{material_id}", value)
        self.recompute()

    def _on_sell_changed(self, *_):
        v = self.venue_combo.currentData()
        settings.setValue(f"craftcalc/sellvenue/{self.item_id}", v)
        settings.setValue("craftcalc/venue", v)  # last choice = default for new items
        settings.setValue(f"craftcalc/sell/{self.item_id}", self.sell_spin.value())
        self.recompute()

    def apply_fetch(self, data: dict) -> dict:
        """Fill prices from a fetch result for this item. Returns tally counts."""
        mats = data.get("materials", {})
        filled = missing = stale = 0
        self._building = True
        for mid, info in mats.items():
            spin = self.price_spins.get(mid)
            if spin is None:
                continue
            if info is None:
                missing += 1
                continue
            spin.blockSignals(True)
            spin.setValue(info["price"])
            spin.blockSignals(False)
            settings.setValue(f"craftcalc/price/{mid}", info["price"])
            old = info["age"] is not None and info["age"] > self.STALE_AGE
            if old:
                stale += 1
            tip = tr("{city} · {age} old").format(city=tr(info["city"]), age=_fmt_age(info["age"]))
            if old:
                tip = "⚠ " + tip + " " + tr("(check this — old data)")
            spin.setToolTip(tip)
            filled += 1

        sell = data.get("sell_bm") if self.venue_combo.currentData() == "bm" else data.get("sell_city")
        sell_missing = 0
        if sell is not None:
            self.sell_spin.blockSignals(True)
            self.sell_spin.setValue(sell["price"])
            self.sell_spin.blockSignals(False)
            settings.setValue(f"craftcalc/sell/{self.item_id}", sell["price"])
        else:
            sell_missing = 1

        self._building = False
        self.recompute()
        return {"filled": filled, "missing": missing, "stale": stale, "sell_missing": sell_missing}

    def recompute(self):
        if self._building:
            return
        rr = self._tab._return_rate()
        runs = self._tab.runs_spin.value()
        craft_cost = 0
        for row, m in enumerate(self.materials):
            price = self.price_spins[m["id"]].value()
            # Relics/artifacts (ret=False) are never refunded — full count. Returnable
            # materials are scaled by (1 - return rate); buy whole units (round up).
            needed = m["count"] * runs * (1 - rr) if m["ret"] else m["count"] * runs
            buy_qty = ceil(needed - 1e-9)
            line = buy_qty * price
            craft_cost += line

            buy_item = self.table.item(row, 3)
            buy_item.setText(_fmt_silver(buy_qty))
            leftover = buy_qty - needed
            if m["ret"] and leftover > 0.01:
                buy_item.setToolTip(
                    tr("Need ~{needed}; buy {buy} (≈{leftover} leftover)").format(
                        needed=f"{needed:.1f}", buy=f"{buy_qty:,}", leftover=f"{leftover:.1f}"
                    )
                )
            else:
                buy_item.setToolTip("")
            self.table.item(row, 4).setText(_fmt_silver(line))

        if not self.materials:
            for lbl, key in (
                (self.cost_label, "Craft cost: —"), (self.net_label, "Total income: —"),
                (self.profit_label, "Profit: —"), (self.unit_profit_label, "Profit/unit: —"),
                (self.roi_label, "ROI: —"),
            ):
                lbl.setText(tr(key))
            self.profit_label.setStyleSheet("font-weight: bold;")
            return

        tax = BM_TAX if self.venue_combo.currentData() == "bm" else SELL_ORDER_TAX
        income = self.sell_spin.value() * (1 - tax) * runs
        profit = income - craft_cost
        roi = profit / craft_cost if craft_cost > 0 else None

        self.cost_label.setText(tr("Craft cost: {v}").format(v=_fmt_silver(int(round(craft_cost)))))
        self.net_label.setText(tr("Total income: {v}").format(v=_fmt_silver(int(round(income)))))
        self.profit_label.setText(tr("Profit: {v}").format(v=_fmt_silver(int(round(profit)))))
        self.unit_profit_label.setText(
            tr("Profit/unit: {v}").format(v=_fmt_silver(int(round(profit / runs))))
        )
        color = "#2e7d32" if profit > 0 else ("#c62828" if profit < 0 else "#888888")
        self.profit_label.setStyleSheet(f"font-weight: bold; color: {color};")
        self.roi_label.setText(
            tr("ROI: —") if roi is None else tr("ROI: {pct}%").format(pct=f"{roi * 100:.1f}")
        )

    def retranslate(self):
        self.table.setHorizontalHeaderLabels(
            [tr("Material"), tr("Qty/ea"), tr("Unit price"), tr("Buy qty"), tr("Line cost")]
        )
        self.venue_label.setText(tr("Sell venue:"))
        self.venue_combo.setItemText(0, tr("City listing (6.5% tax)"))
        self.venue_combo.setItemText(1, tr("Black Market (4% tax)"))
        self.sell_label.setText(tr("Sell price:"))
        self._build_table()  # re-localize material names + [artifact] tag
        self.recompute()


class CraftCalcTab(QWidget):
    """Manual-price crafting calculator (Nendys-style), one tab per enchant level.

    Search lists each craftable item by TIER only (4–8). Pick one and a sub-tab
    opens for every enchant level that has a recipe (e.g. 5, 5.1, 5.2, 5.3, 5.4),
    each a self-contained CraftCalcEnchantPanel. Type material prices (or press
    Fetch to pull live prices for all levels at once) and the sell price; craft
    cost / profit / ROI update live. The shared return rate (refunds returnable
    materials, never artifacts) and batch size drive every tab.
    """

    INFO = (
        "Pick a craftable item — the search lists each tier (4–8). Double-click one and a "
        "tab opens for every enchant level (e.g. 5, 5.1, 5.2…). In each tab set the "
        "materials' buy price (or press Fetch to pull live prices for all levels) and the "
        "sell price; craft cost, profit and ROI update as you type. The return rate refunds "
        "returnable materials, so you buy fewer; artifacts and relics (flagged) are never "
        "refunded. Prices are remembered per material. Station fees are not modelled."
    )

    def __init__(self):
        super().__init__()
        self._panels: list[CraftCalcEnchantPanel] = []  # one per enchant level
        self._fetch_worker = None  # live-price fetch (auto-fill), guarded

        layout = QVBoxLayout(self)
        self.info_label = QLabel(tr(self.INFO))
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        # Return-rate toggles (same model as the live Crafting tab).
        opt_row = QHBoxLayout()
        self.spec_cb = QCheckBox(tr("Crafting bonus city (+15)"))
        self.spec_cb.setChecked(_load_bool("craftcalc/spec", False))
        self.spec_cb.setToolTip(tr("Crafting in the city that specializes in this item type."))
        self.spec_cb.stateChanged.connect(self._on_rate_changed)
        opt_row.addWidget(self.spec_cb)

        self.focus_cb = QCheckBox(tr("Use focus (+59)"))
        self.focus_cb.setChecked(_load_bool("craftcalc/focus", False))
        self.focus_cb.stateChanged.connect(self._on_rate_changed)
        opt_row.addWidget(self.focus_cb)

        opt_row.addSpacing(10)
        self.bonus_label = QLabel(tr("Bonus day:"))
        opt_row.addWidget(self.bonus_label)
        self.bonus_combo = QComboBox()
        for label in CRAFT_BONUS_DAY:
            self.bonus_combo.addItem(tr(label), label)  # display translated, value English
        saved_bonus = settings.value("craftcalc/bonus_day", "None")
        idx = self.bonus_combo.findData(saved_bonus)
        self.bonus_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.bonus_combo.currentTextChanged.connect(self._on_rate_changed)
        opt_row.addWidget(self.bonus_combo)

        self.rate_label = QLabel()
        self.rate_label.setStyleSheet("font-weight: bold;")
        opt_row.addSpacing(15)
        opt_row.addWidget(self.rate_label)
        opt_row.addStretch()
        layout.addLayout(opt_row)

        # Item search.
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Type an item name, e.g. Broadsword, Mercenary Jacket…"))
        self.search_box.textChanged.connect(self._on_search)
        layout.addWidget(self.search_box)
        self.results_list = QListWidget()
        self.results_list.setMaximumHeight(110)
        self.results_list.itemDoubleClicked.connect(self._on_pick)
        layout.addWidget(self.results_list)

        self.selected_label = QLabel(tr("No item selected — double-click a search result."))
        self.selected_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.selected_label)

        # Batch size.
        runs_row = QHBoxLayout()
        self.runs_label = QLabel(tr("Pieces to craft:"))
        runs_row.addWidget(self.runs_label)
        self.runs_spin = QSpinBox()
        self.runs_spin.setRange(1, 1_000_000)
        self.runs_spin.setGroupSeparatorShown(True)
        self.runs_spin.setValue(int(settings.value("craftcalc/runs", 1)))
        self.runs_spin.valueChanged.connect(self._on_runs_changed)
        runs_row.addWidget(self.runs_spin)
        runs_row.addStretch()
        layout.addLayout(runs_row)

        # Auto-fill prices from the live market (still editable afterwards).
        fetch_row = QHBoxLayout()
        self.fetch_city_label = QLabel(tr("Buy materials in:"))
        fetch_row.addWidget(self.fetch_city_label)
        self.fetch_city_combo = QComboBox()
        self.fetch_city_combo.addItem(tr("Cheapest royal city"), "__cheapest__")
        for city in CITIES:
            self.fetch_city_combo.addItem(tr(city), city)
        saved_fc = settings.value("craftcalc/fetch_city", "__cheapest__")
        fci = self.fetch_city_combo.findData(saved_fc)
        self.fetch_city_combo.setCurrentIndex(fci if fci >= 0 else 0)
        self.fetch_city_combo.currentTextChanged.connect(
            lambda *_: settings.setValue(
                "craftcalc/fetch_city", self.fetch_city_combo.currentData()
            )
        )
        fetch_row.addWidget(self.fetch_city_combo)
        self.fetch_btn = QPushButton(tr("Fetch market prices"))
        self.fetch_btn.clicked.connect(self._on_fetch)
        fetch_row.addWidget(self.fetch_btn)
        self.fetch_status = QLabel("")
        self.fetch_status.setStyleSheet("color: #888888;")
        fetch_row.addWidget(self.fetch_status)
        fetch_row.addStretch()
        layout.addLayout(fetch_row)

        # One sub-tab per enchant level of the picked item (built on selection).
        self.enchant_tabs = QTabWidget()
        layout.addWidget(self.enchant_tabs)

        self._update_rate_label()
        last = settings.value("craftcalc/last_item", "")
        if last and recipes.has_recipe(last):
            self._load_item(last)

    # ----- return rate -----
    def _return_rate(self) -> float:
        return craft_return_rate(
            spec=self.spec_cb.isChecked(),
            focus=self.focus_cb.isChecked(),
            bonus_day=CRAFT_BONUS_DAY[self.bonus_combo.currentData()],
        )

    def _update_rate_label(self):
        self.rate_label.setText(tr("Return rate: {pct}%").format(pct=f"{self._return_rate() * 100:.1f}"))

    def _on_rate_changed(self):
        self._update_rate_label()
        self.save_state()
        self._recompute_all()

    def _recompute_all(self):
        for p in self._panels:
            p.recompute()

    # ----- search / pick -----
    def _on_search(self, text):
        # base_only → one entry per tier (enchant variants become sub-tabs on pick).
        self.results_list.clear()
        for iid in names.search_ids(text, base_only=True):
            meta = names.parse_id(iid)
            label = f"{names.get_name(iid)}  (T{meta['tier']})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, iid)
            self.results_list.addItem(item)

    def _on_pick(self, item):
        if item is not None:
            self._load_item(item.data(Qt.UserRole))

    def _load_item(self, base_id: str):
        """Open one sub-tab per enchant level (0–4) of the picked item that has a recipe."""
        base = base_id.split("@")[0]
        meta = names.parse_id(base)
        if not meta:
            return
        tier = meta["tier"]
        variants = []
        for ench in range(5):
            vid = base if ench == 0 else f"{base}@{ench}"
            rec = recipes.get_recipe(vid)
            if rec:
                variants.append((ench, vid, rec))

        self.enchant_tabs.clear()
        self._panels = []
        name = names.get_name(base)
        if not variants:
            self.selected_label.setText(
                tr("{name} ({tier}) — no known recipe.").format(name=name, tier=f"T{tier}")
            )
            return

        for ench, vid, rec in variants:
            panel = CraftCalcEnchantPanel(self, vid, rec["materials"])
            self._panels.append(panel)
            self.enchant_tabs.addTab(panel, str(tier) if ench == 0 else f"{tier}.{ench}")

        self.selected_label.setText(
            tr("Selected: {name} ({tier}) — {n} enchant levels").format(
                name=name, tier=f"T{tier}", n=len(variants)
            )
        )
        settings.setValue("craftcalc/last_item", base)

    # ----- batch size -----
    def _on_runs_changed(self, *_):
        settings.setValue("craftcalc/runs", self.runs_spin.value())
        self._recompute_all()

    # ----- live-price fetch (auto-fill, all enchant tabs in one batch) -----
    def _on_fetch(self):
        if not self._panels:
            self.fetch_status.setStyleSheet("color: #c62828;")
            self.fetch_status.setText(tr("Select an item first."))
            return
        if self._fetch_worker is not None:  # a fetch is already running
            return
        data = self.fetch_city_combo.currentData()
        cities = [c for c in CITIES if c != "Caerleon"] if data == "__cheapest__" else [data]
        items = [(p.item_id, [m["id"] for m in p.materials]) for p in self._panels]

        self.fetch_btn.setEnabled(False)
        self.fetch_status.setStyleSheet("color: #888888;")
        self.fetch_status.setText(tr("Fetching prices…"))
        worker = CraftCalcFetchWorker(items, cities)
        worker.finished_ok.connect(self._on_fetched)
        worker.failed.connect(self._on_fetch_failed)
        worker.finished.connect(self._fetch_cleanup)
        self._fetch_worker = worker
        worker.start()

    def _fetch_cleanup(self):
        self._fetch_worker = None
        self.fetch_btn.setEnabled(True)

    def _on_fetch_failed(self, err: str):
        self.fetch_status.setStyleSheet("color: #c62828;")
        self.fetch_status.setText(tr("Fetch failed: {err}").format(err=err))

    def _on_fetched(self, result: dict):
        filled = missing = stale = sell_missing = 0
        for p in self._panels:
            c = p.apply_fetch(result.get(p.item_id, {}))
            filled += c["filled"]
            missing += c["missing"]
            stale += c["stale"]
            sell_missing += c["sell_missing"]

        total = filled + missing
        msg = tr("Updated {n} of {total} materials across {tabs} tabs.").format(
            n=filled, total=total, tabs=len(self._panels)
        )
        if missing:
            msg += " " + tr("{n} not listed — left unchanged.").format(n=missing)
        if stale:
            msg += " " + tr("{n} use old data (hover to check).").format(n=stale)
        if sell_missing:
            msg += " " + tr("{n} tabs have no sell price.").format(n=sell_missing)
        bad = missing or stale or sell_missing
        self.fetch_status.setStyleSheet("color: #c62828;" if bad else "color: #2e7d32;")
        self.fetch_status.setText(msg)

    def save_state(self):
        settings.setValue("craftcalc/spec", self.spec_cb.isChecked())
        settings.setValue("craftcalc/focus", self.focus_cb.isChecked())
        settings.setValue("craftcalc/bonus_day", self.bonus_combo.currentData())

    def retranslate(self):
        """Re-apply all visible strings in the current language (live toggle)."""
        self.info_label.setText(tr(self.INFO))
        self.spec_cb.setText(tr("Crafting bonus city (+15)"))
        self.spec_cb.setToolTip(tr("Crafting in the city that specializes in this item type."))
        self.focus_cb.setText(tr("Use focus (+59)"))
        self.bonus_label.setText(tr("Bonus day:"))
        for i in range(self.bonus_combo.count()):
            self.bonus_combo.setItemText(i, tr(self.bonus_combo.itemData(i)))
        self.search_box.setPlaceholderText(tr("Type an item name, e.g. Broadsword, Mercenary Jacket…"))
        self.runs_label.setText(tr("Pieces to craft:"))
        self.fetch_city_label.setText(tr("Buy materials in:"))
        for i in range(self.fetch_city_combo.count()):
            self.fetch_city_combo.setItemText(i, tr(self.fetch_city_combo.itemData(i)
                                                    if self.fetch_city_combo.itemData(i) != "__cheapest__"
                                                    else "Cheapest royal city"))
        self.fetch_btn.setText(tr("Fetch market prices"))
        if self._panels:
            base = self._panels[0].item_id.split("@")[0]
            meta = names.parse_id(base)
            tier = meta["tier"] if meta else 0
            self.selected_label.setText(
                tr("Selected: {name} ({tier}) — {n} enchant levels").format(
                    name=names.get_name(base), tier=f"T{tier}", n=len(self._panels)
                )
            )
            for p in self._panels:
                p.retranslate()
        else:
            self.selected_label.setText(tr("No item selected — double-click a search result."))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("Albion Market Scanner — NA (Americas)"))
        self.resize(1150, 740)

        central = QWidget()
        outer = QVBoxLayout(central)

        # Language selector (live EN/ES toggle).
        top = QHBoxLayout()
        top.addStretch()
        self.lang_label = QLabel(tr("Language:"))
        top.addWidget(self.lang_label)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem(tr("English"), "en")
        self.lang_combo.addItem(tr("Spanish"), "es")
        li = self.lang_combo.findData(get_lang())
        self.lang_combo.setCurrentIndex(li if li >= 0 else 0)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self.lang_combo)
        outer.addLayout(top)

        self.tabs = QTabWidget()
        self.refining = RefiningTab()
        self.bm = BlackMarketTab()
        self.haul = ResourceHaulTab()
        self.gather = GatherTab()
        self.craft = CraftTab()
        self.craftcalc = CraftCalcTab()
        self.scam = ScamTab()
        # (widget, English tab title) — title re-translated live.
        self._tabs = [
            (self.refining, "Refining"),
            (self.bm, "Black Market flip"),
            (self.haul, "Resource haul"),
            (self.gather, "Gather advisor"),
            (self.craft, "Crafting"),
            (self.craftcalc, "Craft calc"),
            (self.scam, "Scam check"),
        ]
        for widget, title in self._tabs:
            self.tabs.addTab(widget, tr(title))
        outer.addWidget(self.tabs)
        self.setCentralWidget(central)

    def _on_lang_changed(self):
        code = self.lang_combo.currentData()
        if code == get_lang():
            return
        set_lang(code)
        self._retranslate()

    def _retranslate(self):
        self.setWindowTitle(tr("Albion Market Scanner — NA (Americas)"))
        self.lang_label.setText(tr("Language:"))
        self.lang_combo.setItemText(0, tr("English"))
        self.lang_combo.setItemText(1, tr("Spanish"))
        for i, (widget, title) in enumerate(self._tabs):
            self.tabs.setTabText(i, tr(title))
            if hasattr(widget, "retranslate"):
                widget.retranslate()

    def closeEvent(self, event):
        for widget, _ in self._tabs:
            widget.save_state()
        super().closeEvent(event)


def main():
    app = QApplication([])
    load_lang()
    win = MainWindow()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
