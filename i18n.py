"""Tiny in-app translation layer with a live EN/ES toggle.

Strings are wrapped in tr("English text"). When the language is Spanish, tr()
looks the English source up in ES and returns the translation, falling back to
the English text when a key is missing (so an untranslated string is visible
and easy to spot, never a crash). Dynamic strings keep {placeholder} markers in
both languages: tr("Return rate: {pct}%").format(pct=12.3).

The language is persisted in QSettings under "app/lang" and applied at startup.
"""
from PySide6.QtCore import QSettings

_ORG = "AlbionMarket"
_APP = "AlbionMarket"

_lang = "en"  # "en" or "es"


# English source -> Spanish. Add new strings here as tabs are translated.
ES: dict[str, str] = {
    # --- window / language ---
    "Albion Market Scanner — NA (Americas)": "Escáner de Mercado Albion — NA (Américas)",
    "Language:": "Idioma:",
    "English": "Inglés",
    "Spanish": "Español",
    # --- tab titles ---
    "Refining": "Refinado",
    "Black Market flip": "Reventa Mercado Negro",
    "Resource haul": "Transporte de recursos",
    "Gather advisor": "Asesor de recolección",
    "Crafting": "Fabricación",
    "Craft calc": "Calc. fabricación",
    "Scam check": "Detector de estafas",
    # --- shared widgets ---
    "Cities:": "Ciudades:",
    "Bonus day:": "Día de bonificación:",
    "None": "Ninguno",
    "Silver (+10)": "Plata (+10)",
    "Gold (+20)": "Oro (+20)",
    "Crafting bonus city (+15)": "Ciudad con bonificación de fabricación (+15)",
    "Crafting in the city that specializes in this item type.":
        "Fabricar en la ciudad especializada en este tipo de objeto.",
    "Use focus (+59)": "Usar enfoque (+59)",
    "Return rate: {pct}%": "Tasa de devolución: {pct}%",
    # --- Craft calc tab ---
    "Set the market prices yourself. Pick a craftable item, set how many pieces to "
    "craft, and type each material's buy price — craft cost, income, profit and ROI "
    "update as you type. The return rate refunds returnable materials, so you buy "
    "fewer of them (Buy qty); artifacts and relics (flagged) are never refunded and "
    "cost full count. Prices are remembered per material across items. Station fees "
    "are not modelled.":
        "Tú pones los precios del mercado. Elige un objeto fabricable, indica cuántas "
        "piezas fabricar y escribe el precio de compra de cada material: el coste de "
        "fabricación, los ingresos, la ganancia y el ROI se actualizan al escribir. La "
        "tasa de devolución reembolsa los materiales retornables, así que compras menos "
        "(Cant. a comprar); los artefactos y reliquias (marcados) nunca se reembolsan y "
        "cuestan la cantidad completa. Los precios se recuerdan por material entre "
        "objetos. No se incluyen las tarifas de estación.",
    "Type an item name, e.g. Broadsword, Mercenary Jacket…":
        "Escribe el nombre de un objeto, p. ej. Espadón, Chaqueta de mercenario…",
    "No item selected — double-click a search result.":
        "Ningún objeto seleccionado — haz doble clic en un resultado.",
    "Selected: {name}  ({tier})": "Seleccionado: {name}  ({tier})",
    "{name} ({tier}) — no known recipe.": "{name} ({tier}) — receta desconocida.",
    "Pieces to craft:": "Piezas a fabricar:",
    "Material": "Material",
    "Qty/ea": "Cant./u",
    "Unit price": "Precio unitario",
    "Buy qty": "Cant. a comprar",
    "Line cost": "Coste de línea",
    "  [artifact]": "  [artefacto]",
    "Need ~{needed}; buy {buy} (≈{leftover} leftover)":
        "Necesitas ~{needed}; compra {buy} (≈{leftover} sobrante)",
    "Sell venue:": "Lugar de venta:",
    "City listing (6.5% tax)": "Orden en ciudad (6,5% impuesto)",
    "Black Market (4% tax)": "Mercado Negro (4% impuesto)",
    "Sell price:": "Precio de venta:",
    "Craft cost: —": "Coste de fabricación: —",
    "Total income: —": "Ingresos totales: —",
    "Profit: —": "Ganancia: —",
    "Profit/unit: —": "Ganancia/u: —",
    "ROI: —": "ROI: —",
    "Craft cost: {v}": "Coste de fabricación: {v}",
    "Total income: {v}": "Ingresos totales: {v}",
    "Profit: {v}": "Ganancia: {v}",
    "Profit/unit: {v}": "Ganancia/u: {v}",
    "ROI: {pct}%": "ROI: {pct}%",
}


def load_lang():
    global _lang
    settings = QSettings(_ORG, _APP)
    _lang = "es" if str(settings.value("app/lang", "en")) == "es" else "en"
    return _lang


def set_lang(code: str):
    global _lang
    _lang = "es" if code == "es" else "en"
    QSettings(_ORG, _APP).setValue("app/lang", _lang)


def get_lang() -> str:
    return _lang


def tr(text: str) -> str:
    if _lang == "es":
        return ES.get(text, text)
    return text
