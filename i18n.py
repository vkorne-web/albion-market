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
    "Pick a craftable item — the search lists each tier (4–8). Double-click one and a "
    "tab opens for every enchant level (e.g. 5, 5.1, 5.2…). In each tab set the "
    "materials' buy price (or press Fetch to pull live prices for all levels) and the "
    "sell price; craft cost, profit and ROI update as you type. The return rate refunds "
    "returnable materials, so you buy fewer; artifacts and relics (flagged) are never "
    "refunded. Prices are remembered per material. Station fees are not modelled.":
        "Elige un objeto fabricable — la búsqueda lista cada tier (4–8). Haz doble clic en "
        "uno y se abre una pestaña por cada nivel de encantamiento (p. ej. 5, 5.1, 5.2…). "
        "En cada pestaña pon el precio de compra de los materiales (o pulsa Obtener para "
        "traer precios en vivo de todos los niveles) y el precio de venta; el coste, la "
        "ganancia y el ROI se actualizan al escribir. La tasa de devolución reembolsa los "
        "materiales retornables, así que compras menos; los artefactos y reliquias "
        "(marcados) nunca se reembolsan. Los precios se recuerdan por material. No se "
        "incluyen las tarifas de estación.",
    "Selected: {name} ({tier}) — {n} enchant levels":
        "Seleccionado: {name} ({tier}) — {n} niveles de encantamiento",
    "Updated {n} of {total} materials across {tabs} tabs.":
        "Actualizados {n} de {total} materiales en {tabs} pestañas.",
    "{n} tabs have no sell price.": "{n} pestañas sin precio de venta.",
    "Type an item name, e.g. Broadsword, Mercenary Jacket…":
        "Escribe el nombre de un objeto, p. ej. Espadón, Chaqueta de mercenario…",
    "No item selected — double-click a search result.":
        "Ningún objeto seleccionado — haz doble clic en un resultado.",
    "Selected: {name}  ({tier})": "Seleccionado: {name}  ({tier})",
    "{name} ({tier}) — no known recipe.": "{name} ({tier}) — receta desconocida.",
    "Pieces to craft:": "Piezas a fabricar:",
    "Buy materials in:": "Comprar materiales en:",
    "Cheapest royal city": "Ciudad real más barata",
    "Fetch market prices": "Obtener precios del mercado",
    "Select an item first.": "Primero selecciona un objeto.",
    "Fetching prices…": "Obteniendo precios…",
    "Fetch failed: {err}": "Fallo al obtener: {err}",
    "{city} · {age} old": "{city} · hace {age}",
    "(check this — old data)": "(revísalo — dato viejo)",
    "Updated {n} of {total} materials.": "Actualizados {n} de {total} materiales.",
    "{n} not listed — left unchanged.": "{n} sin listar — sin cambiar.",
    "{n} use old data (hover to check).":
        "{n} con datos viejos (pasa el ratón para revisar).",
    "No sell price found — enter it manually.":
        "Sin precio de venta — escríbelo a mano.",
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

    # --- shared filter labels / actions ---
    "Idle.": "Inactivo.",
    "Tier:": "Nivel:",
    "Enchant:": "Encantamiento:",
    "Material:": "Material:",
    "Type:": "Tipo:",
    "Category:": "Categoría:",
    "Slot:": "Ranura:",
    "Source cities:": "Ciudades de origen:",
    "Min margin:": "Margen mín.:",
    "Min silver/unit:": "Plata mín./u:",
    "Min spike ×:": "Pico mín. ×:",
    "Min inflation:": "Inflación mín.:",
    "Max vol/day:": "Vol/día máx.:",
    "Auto-refresh every": "Auto-actualizar cada",
    "Refresh prices": "Actualizar precios",
    "Scan market": "Escanear mercado",
    "Error: {msg}": "Error: {msg}",
    "Pick at least one city.": "Elige al menos una ciudad.",
    "any": "cualquiera",
    "none": "ninguno",
    "no data": "sin datos",
    "unknown": "desconocido",

    # --- material display names ---
    "Ore": "Mineral",
    "Wood": "Madera",
    "Fiber": "Fibra",
    "Hide": "Cuero",
    "Rock": "Piedra",

    # --- slot groups / kinds / actions / venue ---
    "Armor": "Armadura",
    "Weapons": "Armas",
    "Accessories": "Accesorios",
    "Raw": "Bruto",
    "Refined": "Refinado",
    "Sell raw": "Vender bruto",
    "Refine → sell": "Refinar → vender",
    "Black Market": "Mercado Negro",

    # --- table headers ---
    "Item": "Objeto",
    "Buy in": "Comprar en",
    "Buy price": "Precio de compra",
    "Refine in": "Refinar en",
    "Sell in": "Vender en",
    "Sell price": "Precio de venta",
    "Net margin": "Margen neto",
    "Data age": "Antigüedad",
    "Quality": "Calidad",
    "BM price": "Precio MN",
    "Net (after 4% tax)": "Neto (tras 4% impuesto)",
    "Margin": "Margen",
    "Vol/day": "Vol/día",
    "Net (after 6.5%)": "Neto (tras 6,5%)",
    "ROI": "ROI",
    "Best action": "Mejor acción",
    "Net silver/unit": "Plata neta/u",
    "Sell raw /unit": "Vender bruto /u",
    "Refine /unit": "Refinar /u",
    "Inputs to buy": "Insumos a comprar",
    "City": "Ciudad",
    "Tier": "Nivel",
    "Listed price": "Precio listado",
    "Fair value": "Valor justo",
    "Spike ×": "Pico ×",
    "Verdict": "Veredicto",
    "Age": "Antigüedad",
    "Inflation": "Inflación",
    "Craft cost": "Coste de fabricación",
    "Sell (net)": "Venta (neto)",
    "Venue": "Lugar",
    "Profit": "Ganancia",
    "ROI %": "ROI %",
    "Notes": "Notas",

    # --- Refining tab ---
    "Fetching prices for {n} cities…": "Obteniendo precios de {n} ciudades…",
    "Loaded {n} pairs at {ts}.": "Cargados {n} pares a las {ts}.",
    " (no bonus)": " (sin bonificación)",
    "Raw price: {buy}\nRefined price: {sell}": "Precio bruto: {buy}\nPrecio refinado: {sell}",
    "No margin — missing live price: ": "Sin margen — falta precio en vivo: ",

    # --- Refining detail dialog ---
    "Recipe: {raws} raw + {prev} prev-tier refined":
        "Receta: {raws} bruto + {prev} refinado de nivel anterior",
    "Refine city: {city} ({note})": "Ciudad de refinado: {city} ({note})",
    "Return rate (no focus): {pct}%": "Tasa de devolución (sin enfoque): {pct}%",
    "--- Inputs ---": "--- Insumos ---",
    "Raw: buy {qty}x in {city} @ {price} = {total} silver":
        "Bruto: compra {qty}x en {city} @ {price} = {total} plata",
    "Prev-tier refined: buy {qty}x in {city} @ {price} = {total} silver":
        "Refinado de nivel anterior: compra {qty}x en {city} @ {price} = {total} plata",
    "Gross input cost: {v} silver": "Coste bruto de insumos: {v} plata",
    "Effective input cost (after {pct}% return): {v} silver":
        "Coste efectivo de insumos (tras {pct}% de devolución): {v} plata",
    "--- Output ---": "--- Producto ---",
    "Sell 1x refined in {city} @ {price} silver": "Vende 1x refinado en {city} @ {price} plata",
    "NET MARGIN per refined unit: {v} silver": "MARGEN NETO por unidad refinada: {v} plata",
    "--- Route ---": "--- Ruta ---",
    "1. Buy raws in {city}": "1. Compra brutos en {city}",
    "2. Buy prev-tier refined in {city}": "2. Compra refinado de nivel anterior en {city}",
    "{n}. Refine in {city}": "{n}. Refina en {city}",
    "{n}. Sell refined in {city}": "{n}. Vende refinado en {city}",
    "--- Data freshness ---": "--- Frescura de datos ---",
    "Raw: {age} old ({ts})": "Bruto: {age} de antigüedad ({ts})",
    "Prev refined: {age} old ({ts})": "Refinado anterior: {age} de antigüedad ({ts})",
    "Refined: {age} old ({ts})": "Refinado: {age} de antigüedad ({ts})",
    "Incomplete data — can't compute an honest margin.":
        "Datos incompletos — no se puede calcular un margen honesto.",
    "Missing live price(s): ": "Falta(n) precio(s) en vivo: ",
    "--- What we do have ---": "--- Lo que sí tenemos ---",
    "Raw: buy in {city} @ {price} silver": "Bruto: compra en {city} @ {price} plata",
    "Raw: no live price": "Bruto: sin precio en vivo",
    "Prev-tier refined: buy in {city} @ {price} silver":
        "Refinado de nivel anterior: compra en {city} @ {price} plata",
    "Prev-tier refined: no live price": "Refinado de nivel anterior: sin precio en vivo",
    "Refined sell order: {city} @ {price} silver": "Orden de venta de refinado: {city} @ {price} plata",
    "Refined sell order: no live buy order to sell into":
        "Orden de venta de refinado: sin orden de compra en vivo para vender",
    "Tip: the data is player-sourced — try Refresh, or check this item in-game.":
        "Consejo: los datos provienen de jugadores — prueba Actualizar, o revisa este objeto en el juego.",

    # --- Black Market tab ---
    "Buy gear cheap in a royal city → sell into Black Market buy orders in Caerleon. "
    "Margins include the 4% BM sales tax.":
        "Compra equipo barato en una ciudad real → véndelo en órdenes de compra del Mercado "
        "Negro en Caerleon. Los márgenes incluyen el 4% de impuesto de venta del MN.",
    "Pick at least one source city.": "Elige al menos una ciudad de origen.",
    "Pick at least one tier and enchant.": "Elige al menos un nivel y un encantamiento.",
    "Scanning tiers {tiers} enchants {enchants} across {n} cities + Black Market…":
        "Escaneando niveles {tiers} encantamientos {enchants} en {n} ciudades + Mercado Negro…",
    "Showing {shown} of {total} flips (margin ≥ {margin}) — scanned {ts}.":
        "Mostrando {shown} de {total} reventas (margen ≥ {margin}) — escaneado {ts}.",
    "City price: {city}\nBM price: {bm}": "Precio en ciudad: {city}\nPrecio MN: {bm}",
    "Volume only fetched for the top flips by margin — "
    "raise Min margin or check this item in-game":
        "El volumen solo se obtiene para las mejores reventas por margen — "
        "sube el margen mín. o revisa este objeto en el juego",
    "Avg units sold to BM per day (last 7 days)":
        "Unidades vendidas al MN por día en promedio (últimos 7 días)",

    # --- Resource haul tab ---
    "Buy a resource at its cheapest city → haul it → list your own sell order "
    "in the priciest city. Margin = destination price × (1 − 6.5% tax/fee) − buy price.":
        "Compra un recurso en su ciudad más barata → transpórtalo → pon tu propia orden de venta "
        "en la ciudad más cara. Margen = precio de destino × (1 − 6,5% impuesto/tarifa) − precio de compra.",
    "Pick at least two cities (a haul needs a source and a destination).":
        "Elige al menos dos ciudades (un transporte necesita origen y destino).",
    "Fetching resource prices across {n} cities…": "Obteniendo precios de recursos en {n} ciudades…",
    "Showing {shown} of {total} hauls (margin ≥ {margin}) — scanned {ts}.":
        "Mostrando {shown} de {total} transportes (margen ≥ {margin}) — escaneado {ts}.",
    "Buy price: {buy}\nSell price: {sell}": "Precio de compra: {buy}\nPrecio de venta: {sell}",
    "Volume only fetched for the top hauls by margin — "
    "raise Min margin or check this item in-game":
        "El volumen solo se obtiene para los mejores transportes por margen — "
        "sube el margen mín. o revisa este objeto en el juego",
    "Avg units sold per day in {city} (last 7 days)":
        "Unidades vendidas por día en promedio en {city} (últimos 7 días)",

    # --- Gather detail dialog ---
    "=== {label} (gathered) ===": "=== {label} (recolectado) ===",
    "Recommended: {action} — {v} silver per gathered unit":
        "Recomendado: {action} — {v} plata por unidad recolectada",
    "(Net of 6.5% sell-order tax/fee. Raws are free; refine path buys the lesser material.)":
        "(Neto del 6,5% de impuesto/tarifa de venta. Los brutos son gratis; la vía de refinado compra el material menor.)",
    "--- Option A: Sell raw ---": "--- Opción A: Vender bruto ---",
    "List raw in {city} @ {price}  ->  x0.935 = {v} /unit":
        "Lista bruto en {city} @ {price}  ->  x0,935 = {v} /u",
    "No live raw sell price.": "Sin precio de venta de bruto en vivo.",
    "--- Option B: Refine -> sell ---": "--- Opción B: Refinar -> vender ---",
    "{city} (bonus, {pct}% return)": "{city} (bonificación, {pct}% devolución)",
    "any royal city ({pct}% return)": "cualquier ciudad real ({pct}% devolución)",
    "Refine in {where}": "Refina en {where}",
    "{raws} raws (gathered)": "{raws} brutos (recolectados)",
    "Recipe per refined unit: {recipe}": "Receta por unidad refinada: {recipe}",
    "Buy T{tier} {name} in {city} @ {price}  (effective {eff}/refined after return)":
        "Compra T{tier} {name} en {city} @ {price}  (efectivo {eff}/refinado tras devolución)",
    "T{tier} {name}: no live price": "T{tier} {name}: sin precio en vivo",
    "Sell refined in {city} @ {price}  ->  x0.935 = {v}":
        "Vende refinado en {city} @ {price}  ->  x0,935 = {v}",
    "Net per refined unit = {net} - {cost} input = {result}":
        "Neto por unidad refinada = {net} - {cost} insumo = {result}",
    "Per gathered raw unit (/ {raws} raws) = {v} /unit":
        "Por unidad bruta recolectada (/ {raws} brutos) = {v} /u",
    "Refine path unavailable (missing refined or lesser-material price).":
        "Vía de refinado no disponible (falta precio de refinado o de material menor).",
    "Raw price: {age} old ({ts})": "Precio bruto: {age} de antigüedad ({ts})",
    "Refined price: {age} old ({ts})": "Precio refinado: {age} de antigüedad ({ts})",

    # --- Gather tab ---
    "You gather the raws for free — this ranks each resource by net silver per "
    "gathered unit, picking the better of selling it raw or refining it and selling "
    "the bar/cloth/etc. The refine path buys the lesser material at market and includes "
    "the 6.5% sell-order tax. Double-click a row for the full breakdown.":
        "Recolectas los brutos gratis — esto clasifica cada recurso por plata neta por unidad "
        "recolectada, eligiendo lo mejor entre venderlo bruto o refinarlo y vender la barra/tela/etc. "
        "La vía de refinado compra el material menor en el mercado e incluye el 6,5% de impuesto de "
        "venta. Haz doble clic en una fila para ver el desglose completo.",
    "Scanning gather options across {n} cities…":
        "Escaneando opciones de recolección en {n} ciudades…",
    "Showing {shown} of {total} resources (≥ {v}/unit) — scanned {ts}.":
        "Mostrando {shown} de {total} recursos (≥ {v}/u) — escaneado {ts}.",
    "Volume only fetched for the top options — "
    "raise Min silver/unit or check this item in-game":
        "El volumen solo se obtiene para las mejores opciones — "
        "sube la plata mín./u o revisa este objeto en el juego",
    "Avg units of the listed item sold per day in {city} (last 7 days)":
        "Unidades del objeto listado vendidas por día en promedio en {city} (últimos 7 días)",

    # --- Bundle check dialog ---
    "Check a bundle / trade": "Verificar un lote / intercambio",
    "Search the items someone is offering you, add them to the list, and check "
    "them against their real traded value. Uses your selected cities: {cities}.":
        "Busca los objetos que alguien te ofrece, agrégalos a la lista y compáralos con su valor "
        "real de mercado. Usa tus ciudades seleccionadas: {cities}.",
    "Type an item name, e.g. Rotcaller, Royal Cowl…":
        "Escribe el nombre de un objeto, p. ej. Invocapútrido, Capucha real…",
    "Add →": "Agregar →",
    "← Remove": "← Quitar",
    "Items to check:": "Objetos a verificar:",
    "Check bundle": "Verificar lote",
    "Clear list": "Limpiar lista",
    "Add items, then Check bundle.": "Agrega objetos, luego Verificar lote.",
    "Add at least one item first.": "Agrega al menos un objeto primero.",
    "Checking {n} item(s)…": "Verificando {n} objeto(s)…",
    "{n} listing(s) found. Most suspicious first.":
        "{n} listado(s) encontrado(s). Los más sospechosos primero.",
    "No live listings found for those items in the selected cities.":
        "No se encontraron listados en vivo para esos objetos en las ciudades seleccionadas.",

    # --- Scam tab ---
    "Scans the market for items listed FAR above their real recent traded price — "
    "the planted/overpriced listings used in bundle & trade scams. Spike × = current "
    "listing ÷ fair value (volume-weighted 30-day traded average). A high spike on a "
    "low-volume item is almost certainly fake.":
        "Escanea el mercado en busca de objetos listados MUY por encima de su precio real reciente — "
        "los listados plantados/sobrevalorados usados en estafas de lotes e intercambios. Pico × = "
        "listado actual ÷ valor justo (promedio negociado de 30 días ponderado por volumen). Un pico "
        "alto en un objeto de bajo volumen es casi seguro falso.",
    "Check a bundle…": "Verificar un lote…",
    "Verify the specific items in a trade someone is offering you, item by item.":
        "Verifica los objetos específicos de un intercambio que alguien te ofrece, objeto por objeto.",
    "Only show items trading at or below this many units/day.\n"
    "Fake prices live in thin markets — set e.g. 5 to focus on them. 'any' = no cap.":
        "Solo muestra objetos que se negocian a esta cantidad de unidades/día o menos.\n"
        "Los precios falsos viven en mercados delgados — pon p. ej. 5 para enfocarte en ellos. 'cualquiera' = sin límite.",
    "Pick at least one city first (bundle check uses them).":
        "Elige al menos una ciudad primero (la verificación de lotes las usa).",
    "Pick at least one city, tier and enchant.":
        "Elige al menos una ciudad, un nivel y un encantamiento.",
    "Scanning {tn} tier(s) across {cn} cities…": "Escaneando {tn} nivel(es) en {cn} ciudades…",
    "Showing {shown} of {total} flagged listings (spike ≥ {spike}×) — scanned {ts}.":
        "Mostrando {shown} de {total} listados marcados (pico ≥ {spike}×) — escaneado {ts}.",
    "Listed at {current}, but the 30-day traded average is {baseline} — {spike}× over fair value.":
        "Listado a {current}, pero el promedio negociado de 30 días es {baseline} — {spike}× sobre el valor justo.",
    "Very thin market — a fake price here can sit unchallenged. "
    "Strong corroboration that this listing is bogus.":
        "Mercado muy delgado — un precio falso aquí puede pasar desapercibido. "
        "Fuerte indicio de que este listado es falso.",
    "Actively traded — a real (if temporary) price spike is more "
    "plausible here than in a thin market.":
        "Negociado activamente — un pico de precio real (aunque temporal) es más "
        "plausible aquí que en un mercado delgado.",
    "Listing seen: {ts}": "Listado visto: {ts}",

    # --- Craft detail dialog ---
    "{name} — craft breakdown": "{name} — desglose de fabricación",
    "No known crafting recipe for this item.": "No se conoce receta de fabricación para este objeto.",
    "Resource return rate: {pct}%  (refunds returnable materials)":
        "Tasa de devolución de recursos: {pct}%  (reembolsa materiales retornables)",
    "--- Materials (bought instant at cheapest selected city) ---":
        "--- Materiales (comprados al instante en la ciudad seleccionada más barata) ---",
    "  [artifact/token — never refunded]": "  [artefacto/ficha — nunca reembolsado]",
    "{count}x {name}: no live price{ret_note}": "{count}x {name}: sin precio en vivo{ret_note}",
    "{count}x {name} @ {price} in {city}  ->  effective {eff} x {price} = {cost}{ret_note}":
        "{count}x {name} @ {price} en {city}  ->  efectivo {eff} x {price} = {cost}{ret_note}",
    "Total craft cost: {v}": "Coste total de fabricación: {v}",
    "--- Sell ---": "--- Venta ---",
    "No live sell price (no city listing and no Black Market buy order).":
        "Sin precio de venta en vivo (sin listado en ciudad ni orden de compra en el Mercado Negro).",
    "4% BM tax": "4% impuesto MN",
    "6.5% sell-order tax/fee": "6,5% impuesto/tarifa de orden de venta",
    "Best: {venue} @ {price}  ->  net {net} (after {tax})":
        "Mejor: {venue} @ {price}  ->  neto {net} (tras {tax})",
    "PROFIT per craft: {v}": "GANANCIA por fabricación: {v}",
    "ROI on materials: {pct}%": "ROI sobre materiales: {pct}%",
    "Profit unavailable — missing: {missing}": "Ganancia no disponible — falta: {missing}",
    "Note: assumes Normal quality and ignores station usage fees.":
        "Nota: asume calidad Normal e ignora las tarifas de uso de estación.",

    # --- Crafting tab ---
    "Search any craftable item, add it to the list, and check the net profit of "
    "crafting it. Materials are bought instant at the cheapest selected city; the "
    "crafted item is sold the better of a city listing (net 6.5%) or the Black Market "
    "(net 4%). Set the return rate with the toggles below.":
        "Busca cualquier objeto fabricable, agrégalo a la lista y revisa la ganancia neta de "
        "fabricarlo. Los materiales se compran al instante en la ciudad seleccionada más barata; "
        "el objeto fabricado se vende por lo mejor entre un listado en ciudad (neto 6,5%) o el "
        "Mercado Negro (neto 4%). Ajusta la tasa de devolución con los interruptores de abajo.",
    "Items to craft-check:": "Objetos a revisar:",
    "Check craft profit": "Revisar ganancia de fabricación",
    "Add items, then check.": "Agrega objetos, luego revisa.",
    "Checking {n} item(s)…": "Verificando {n} objeto(s)…",
    "{n} item(s). Double-click a row for the material breakdown.":
        "{n} objeto(s). Haz doble clic en una fila para ver el desglose de materiales.",
    "No results.": "Sin resultados.",
    "no recipe": "sin receta",
    "missing: ": "falta: ",
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
