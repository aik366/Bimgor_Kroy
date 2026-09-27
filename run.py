#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Расчёт расхода ПВХ-плёнки для облицовки мебельных фасадов.

Работа с заказами: размеры берутся из файла 22.txt (кодировка cp1251).
Пользователь вводит номер заказа (например 0002), программа находит блок
заказа и забирает все строки деталей вида №|длина|ширина|количество|...
Каждая позиция разворачивается по количеству в отдельные изделия.
Если заказ не помещается на один стол (отрез ≤ 3000 мм), раскладка
автоматически делится на несколько столов; схема сохраняется
отдельным файлом на каждый стол (layout_NNNN_st1.png, ...).

Плёнка бывает С ТЕКСТУРОЙ (направление рисунка важно — поворот на 90°
запрещён) и БЕЗ ТЕКСТУРЫ (поворот разрешён). Программа спрашивает о типе
плёнки (или принимает флаги --texture / --no-texture) и ведёт расчёт
исходя из ответа.

Неинтерактивный запуск: python run.py --order 0002 --no-texture
Демо-тесты алгоритма: python run.py --demo

Алгоритм: эвристический Bottom-Left Fill (BLF) + перебор нескольких
стратегий сортировки и ориентаций. НЕ гарантирует доказанный математический
минимум (задача NP-трудна), но находит хорошее допустимое решение.

Требования: Python 3.11+, matplotlib (только для визуализации).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Параметры по умолчанию (мм)
# ---------------------------------------------------------------------------
TABLE_LENGTH = 3000   # Длина рабочего стола, мм
TABLE_WIDTH = 1200    # Ширина рабочего стола, мм
FILM_WIDTH = 1200     # Ширина рулона ПВХ-плёнки, мм
GAP = 50              # Технологический зазор между фасадами, мм
EDGE_GAP = 50         # Краевой отступ от краёв стола/плёнки, мм
ALLOW_ROTATION = True  # Разрешить поворот фасадов на 90°
ORDERS_FILE = "22.txt"  # Файл заказов (кодировка cp1251, блоки через #@#)

# Тестовый набор из задания
FACADES: List[List[int]] = [
    [2200, 600],
    [720, 400],
    [720, 350],
    [350, 700],
]


# ---------------------------------------------------------------------------
# Модель данных
# ---------------------------------------------------------------------------
@dataclass
class PlacedFacade:
    """Один размещённый фасад на полотне плёнки."""
    idx: int          # номер в исходном списке (0-based)
    orig_len: int     # исходная длина (первый параметр), мм — вдоль пленки (ось Y)
    orig_wid: int     # исходная ширина (второй параметр), мм — поперек пленки (ось X)
    x: int            # координата левого нижнего угла, мм
    y: int            # координата левого нижнего угла, мм
    w: int            # фактическая ширина на полотне (ось X), мм
    h: int            # фактическая высота на полотне (ось Y), мм
    rotated: bool = False  # True — повёрнут на 90°

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def top(self) -> int:
        return self.y + self.h


# ---------------------------------------------------------------------------
# 1. Проверка входных данных
# ---------------------------------------------------------------------------
def validate_facades(
    facades: List[List[int]],
    film_width: int = FILM_WIDTH,
    table_length: int = TABLE_LENGTH,
    table_width: int = TABLE_WIDTH,
    edge_gap: int = EDGE_GAP,
) -> None:
    """Проверка корректности исходных данных.

    Raises:
        ValueError: при любой некорректности с понятным сообщением.
    """
    if not isinstance(facades, list):
        raise ValueError("facades должен быть списком списков [[длина, ширина], ...].")
    for i, f in enumerate(facades):
        if not isinstance(f, (list, tuple)) or len(f) != 2:
            raise ValueError(f"Фасад №{i + 1}: ожидается пара [длина, ширина], получено {f!r}.")
        a, b = f
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            raise ValueError(f"Фасад №{i + 1}: размеры должны быть числами, получено {f!r}.")
        if a <= 0 or b <= 0:
            raise ValueError(f"Фасад №{i + 1}: размеры должны быть > 0, получено {f!r}.")
        a, b = int(a), int(b)

        # Проверка с учётом краевых отступов: хотя бы одна ориентация
        # должна входить в рабочую зону одного стола
        # (usable_w x usable_l). Иначе деталь не разместить в принципе.
        usable_w = min(film_width, table_width) - 2 * edge_gap
        usable_l = table_length - 2 * edge_gap
        fits_one_table = (
            (b <= usable_w and a <= usable_l)
            or (a <= usable_w and b <= usable_l)
        )
        if not fits_one_table:
            raise ValueError(
                f"Фасад №{i + 1} ({a}x{b}): не помещается в рабочую зону "
                f"одного стола {usable_w}x{usable_l} мм (стол "
                f"{table_length}x{table_width} мм минус краевые отступы "
                f"{edge_gap} мм) ни в одной ориентации."
            )
    if film_width <= 0 or table_length <= 0 or table_width <= 0:
        raise ValueError("Размеры рулона и стола должны быть > 0.")


# ---------------------------------------------------------------------------
# 2. Площадь фасадов
# ---------------------------------------------------------------------------
def calculate_facade_area(facades: List[List[int]]) -> float:
    """Raschet obshchey ploshchadi fasadov, m2."""
    return sum(int(f[0]) * int(f[1]) for f in facades) / 1_000_000.0


# ---------------------------------------------------------------------------
# 2a. Заказы из файла 22.txt
# ---------------------------------------------------------------------------
# Формат файла (кодировка cp1251): заказы разделены строкой #@#.
# Шапка заказа:  NNNN|Клиент|даты|...
# Строки деталей: №|длина|ширина|количество|...|тип|...
# Пример: 1|960|411|2||16|15/БФ|Фасад||481|0.79|0.79|


@dataclass
class OrderDetail:
    """Одна строка деталей заказа."""
    num: int        # № позиции в заказе
    length: int     # длина, мм (1-й размер — вдоль плёнки)
    width: int      # ширина, мм (2-й размер — поперёк плёнки)
    qty: int        # количество, шт
    typ: str = ""   # тип (Фасад, Стекло, ...)


@dataclass
class Order:
    """Заказ целиком."""
    number: str
    client: str = ""
    details: List[OrderDetail] = field(default_factory=list)

    @property
    def total_pieces(self) -> int:
        return sum(d.qty for d in self.details)


def _read_orders_text(path: str) -> str:
    """Прочитать файл заказов (cp1251, запасной вариант — utf-8)."""
    for enc in ("cp1251", "utf-8-sig", "utf-8"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Не удалось декодировать файл {path} (пробовал cp1251/utf-8).")


def load_orders(path: str = ORDERS_FILE) -> "dict[str, Order]":
    """Загрузить все заказы из файла. Возвращает словарь номер -> Order."""
    import os

    if not os.path.isabs(path):
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, path)
    if not os.path.isfile(path):
        raise ValueError(f"Файл заказов не найден: {path}")
    text = _read_orders_text(path)
    orders: "dict[str, Order]" = {}
    skipped = 0
    for block in text.split("#@#"):
        rows = [ln for ln in block.splitlines() if ln.strip()]
        if not rows:
            continue
        head = rows[0].split("|")
        number = head[0].strip()
        if not number:
            continue
        client = head[1].strip() if len(head) > 1 else ""
        order = Order(number=number, client=client)
        for ln in rows[1:]:
            f = ln.split("|")
            if len(f) < 4:
                continue
            try:
                num, length, width, qty = (int(float(f[i].strip())) for i in range(4))
            except ValueError:
                continue  # не строка детали (шапка материала и т.п.)
            if length <= 0 or width <= 0 or qty <= 0:
                skipped += 1
                continue
            typ = f[7].strip() if len(f) > 7 else ""
            order.details.append(OrderDetail(num, length, width, qty, typ))
        # Пустые заказы (без деталей) тоже сохраняем — сообщим при выборе.
        orders[number] = order
    if skipped:
        print(f"  [пропущено строк с нулевыми размерами: {skipped}]")
    return orders


def find_order(orders: "dict[str, Order]", query: str) -> Order | None:
    """Найти заказ по номеру: точное совпадение, без ведущих нулей, с паддингом."""
    q = query.strip()
    if not q:
        return None
    if q in orders:
        return orders[q]
    qz = q.lstrip("0") or "0"
    for number, order in orders.items():
        if number.lstrip("0") or "0" == qz or number == q.zfill(4):
            if (number.lstrip("0") or "0") == qz:
                return order
    return None


def expand_order_facades(order: Order) -> Tuple[List[List[int]], List[str], List[int]]:
    """Развернуть позиции заказа в список фасадов с учётом количества.

    Возвращает (facades, labels, pos_nums): facades — [[длина, ширина], ...]
    по одному на каждое изделие; labels — подписи вида "поз.3 (768x80) шт.2/2";
    pos_nums — номер позиции каждого изделия (для единого цвета на схеме).
    """
    facades: List[List[int]] = []
    labels: List[str] = []
    pos_nums: List[int] = []
    for d in order.details:
        for k in range(1, d.qty + 1):
            facades.append([d.length, d.width])
            tag = f"поз.{d.num} ({d.length}x{d.width})"
            if d.qty > 1:
                tag += f" шт.{k}/{d.qty}"
            if d.typ:
                tag += f" [{d.typ}]"
            labels.append(tag)
            pos_nums.append(d.num)
    return facades, labels, pos_nums


def ask_order_number(orders: "dict[str, Order]") -> Order | None:
    """Спросить у пользователя номер заказа. Пустой ввод — выход."""
    print(f"Загружено заказов: {len(orders)}.")
    while True:
        try:
            ans = input("Введите номер заказа (пусто — выход): ").strip()
        except EOFError:
            print()
            return None
        if ans == "":
            return None
        order = find_order(orders, ans)
        if order is not None:
            return order
        print(f"  Заказ «{ans}» не найден. Примеры номеров: "
              + ", ".join(list(orders)[:5]))


# ---------------------------------------------------------------------------
# 3. Проверка возможности размещения
# ---------------------------------------------------------------------------
def can_place_facade(
    x: int,
    y: int,
    w: int,
    h: int,
    placed: List[PlacedFacade],
    film_width: int,
    gap: int,
    edge_gap: int = EDGE_GAP,
) -> bool:
    """Можно ли поставить прямоугольник (x, y, w, h) с учётом зазоров и границ.

    Правила:
      * краевые отступы: x >= edge_gap, y >= edge_gap,
        x + w <= film_width - edge_gap (по длине сверху ограничений нет —
        длина и минимизируется, верхний отступ учитывается в её расчёте);
      * для каждой уже размещённой детали расширенные на `gap` зоны
        не должны пересекаться.
    """
    if x < edge_gap or y < edge_gap or x + w > film_width - edge_gap:
        return False
    for p in placed:
        # Нет пересечения, если есть разделительная полоса >= gap
        # хотя бы с одной стороны.
        if not (
            x >= p.x + p.w + gap
            or p.x >= x + w + gap
            or y >= p.y + p.h + gap
            or p.y >= y + h + gap
        ):
            return False
    return True


# ---------------------------------------------------------------------------
# 4. Оптимизация раскладки (эвристика BLF + несколько стратегий)
# ---------------------------------------------------------------------------
class TableFull(Exception):
    """Деталь не помещается в текущий стол — пора открывать следующий."""


def _place_single(
    idx: int,
    a: int,
    b: int,
    placed: List[PlacedFacade],
    candidates: List[Tuple[int, int]],
    film_width: int,
    gap: int,
    allow_rotation: bool,
    edge_gap: int = EDGE_GAP,
    max_top: float = math.inf,
) -> PlacedFacade:
    """Поставить одну деталь (длина a, ширина b) методом Bottom-Left.

    `placed`/`candidates` — текущее состояние стола (candidates дополняется).
    `max_top` — предельный верх детали (длина стола минус верхний отступ);
    при превышении — TableFull. При пустом столе деталь, не входящая даже
    в пустой стол, даёт ValueError.
    """
    # a — длина (ось Y), b — ширина (ось X)
    orientations = [(b, a, False)]
    if allow_rotation and a != b:
        orientations.append((a, b, True))
    usable = film_width - 2 * edge_gap

    best = None  # (top, x, y, w, h, rotated)
    # Кандидаты просматриваем снизу вверх, слева направо.
    for cx, cy in sorted(set(candidates), key=lambda c: (c[1], c[0])):
        for w, h, rot in orientations:
            if cx + w > film_width - edge_gap:
                continue
            if cy + h > max_top:
                continue
            if can_place_facade(cx, cy, w, h, placed, film_width, gap, edge_gap):
                key = (cy + h, cx)
                if best is None or key < (best[0], best[1]):
                    best = (cy + h, cx, cy, w, h, rot)
    if best is None:
        # Не нашлось места среди углов — кладём новой «полкой» сверху.
        top = max((p.top for p in placed), default=edge_gap - gap)
        new_y = top + gap  # при пустой раскладке это edge_gap
        feasible = [(w, h, r) for w, h, r in orientations
                    if w <= usable and new_y + h <= max_top]
        if not feasible:
            if not placed:
                raise ValueError(
                    f"Фасад №{idx + 1} ({a}x{b}): не входит в стол "
                    f"{film_width} мм (рабочая ширина {usable} мм) ни в одной ориентации."
                )
            raise TableFull(f"Фасад №{idx + 1} ({a}x{b}) не помещается в текущий стол.")
        w, h, rot = min(feasible, key=lambda o: (new_y + o[1], o[0]))
        best = (new_y + h, edge_gap, new_y, w, h, rot)

    _, bx, by, bw, bh, brot = best
    item = PlacedFacade(idx=idx, orig_len=a, orig_wid=b,
                        x=bx, y=by, w=bw, h=bh, rotated=brot)
    placed.append(item)
    # Новые кандидатные углы: справа и сверху от placed-детали.
    candidates.append((bx + bw + gap, by))
    candidates.append((bx, by + bh + gap))
    return item


def _pack_blf(
    order: List[Tuple[int, int, int]],
    film_width: int,
    gap: int,
    allow_rotation: bool,
    edge_gap: int = EDGE_GAP,
    max_top: float = math.inf,
) -> List[PlacedFacade]:
    """Разместить фасады в заданном порядке методом Bottom-Left Fill.

    `order` — список (исходный_индекс, длина, ширина), где длина — размер
    вдоль пленки (ось Y), ширина — поперек пленки (ось X).
    Исходная ориентация («без поворота»): длина вдоль пленки, ширина поперек.
    Поворот на 90° меняет их местами.
    Рабочая зона ограничена краевыми отступами edge_gap со всех сторон:
    первая позиция — (edge_gap, edge_gap), правая граница — film_width - edge_gap.
    `max_top` ограничивает верх детали (для разбивки по столам).
    """
    placed: List[PlacedFacade] = []
    candidates: List[Tuple[int, int]] = [(edge_gap, edge_gap)]
    for idx, a, b in order:
        _place_single(idx, a, b, placed, candidates,
                      film_width, gap, allow_rotation, edge_gap, max_top)
    return placed


def pack_tables(
    order: List[Tuple[int, int, int]],
    film_width: int,
    gap: int,
    allow_rotation: bool,
    edge_gap: int = EDGE_GAP,
    table_length: int = TABLE_LENGTH,
) -> List[List[PlacedFacade]]:
    """Разложить фасады по столам: каждый стол — отрез плёнки длиной <= table_length.

    Детали идут в заданном порядке; каждая ставится в текущий стол, а если
    не помещается — открывается новый стол. Возвращает список столов, в каждом
    координаты локальные (отсчёт от края своего стола).
    """
    max_top = table_length - edge_gap
    tables: List[List[PlacedFacade]] = []
    placed: List[PlacedFacade] = []
    candidates: List[Tuple[int, int]] = [(edge_gap, edge_gap)]
    for idx, a, b in order:
        try:
            _place_single(idx, a, b, placed, candidates,
                          film_width, gap, allow_rotation, edge_gap, max_top)
        except TableFull:
            tables.append(placed)
            placed = []
            candidates = [(edge_gap, edge_gap)]
            _place_single(idx, a, b, placed, candidates,
                          film_width, gap, allow_rotation, edge_gap, max_top)
    if placed:
        tables.append(placed)
    for t in tables:
        t.sort(key=lambda p: (p.y, p.x))
    return tables


def optimize_layout(
    facades: List[List[int]],
    film_width: int = FILM_WIDTH,
    gap: int = GAP,
    allow_rotation: bool = ALLOW_ROTATION,
    edge_gap: int = EDGE_GAP,
    table_length: int = TABLE_LENGTH,
) -> List[List[PlacedFacade]]:
    """Оптимизация размещения по столам: перебор стратегий, выбор лучшей.

    Стратегии сортировки (убывание): площадь, максимальная сторона,
    высота, ширина, периметр + исходный порядок. Для каждой строится
    раскладка по столам (pack_tables); возвращается вариант с минимальной
    СУММАРНОЙ длиной плёнки. Каждый стол — отдельный отрез длиной
    не более table_length; если всё поместилось — стол один.

    Это ЭВРИСТИКА: результат — хорошее допустимое решение, но не
    доказанный глобальный минимум (Strip Packing NP-трудна).
    """
    if not facades:
        return []
    items = [(i, int(f[0]), int(f[1])) for i, f in enumerate(facades)]
    strategies = {
        "площадь (убыв.)": sorted(items, key=lambda t: t[1] * t[2], reverse=True),
        "макс. сторона (убыв.)": sorted(items, key=lambda t: max(t[1], t[2]), reverse=True),
        "высота (убыв.)": sorted(items, key=lambda t: t[1], reverse=True),
        "ширина (убыв.)": sorted(items, key=lambda t: t[2], reverse=True),
        "периметр (убыв.)": sorted(items, key=lambda t: 2 * (t[1] + t[2]), reverse=True),
        "исходный порядок": list(items),
    }
    best_tables: List[List[PlacedFacade]] = []
    best_total = math.inf
    best_name = ""
    best_count = 0
    for name, order in strategies.items():
        tables = pack_tables(order, film_width, gap, allow_rotation,
                             edge_gap, table_length)
        total = sum(calculate_film_length(t, edge_gap) for t in tables)
        if total < best_total:
            best_total = total
            best_tables = tables
            best_name = name
            best_count = len(tables)
    print(f"  [стратегия-победитель: «{best_name}», "
          f"столов: {best_count}, суммарная длина {best_total} мм]")
    return best_tables


# ---------------------------------------------------------------------------
# 5. Длина плёнки и метрики
# ---------------------------------------------------------------------------
def calculate_film_length(layout: List[PlacedFacade], edge_gap: int = EDGE_GAP) -> int:
    """Расчёт необходимой длины плёнки, мм.

    Длина = верх самой высокой детали + краевой отступ edge_gap
    (отступ от верхнего края стола/плёнки). Пустой раскладке — 0.
    """
    if not layout:
        return 0
    return max(p.top for p in layout) + edge_gap


def material_utilization(
    facades: List[List[int]], film_width: int, film_length: int
) -> float:
    """Коэффициент использования материала (0..1)."""
    if film_length <= 0:
        return 0.0
    return calculate_facade_area(facades) * 1_000_000.0 / (film_width * film_length)


# ---------------------------------------------------------------------------
# 6. Проверки результата
# ---------------------------------------------------------------------------
def verify_layout(
    layout: List[PlacedFacade],
    film_width: int,
    gap: int,
    table_length: int = TABLE_LENGTH,
    table_width: int = TABLE_WIDTH,
    edge_gap: int = EDGE_GAP,
) -> List[str]:
    """Независимая проверка ограничений. Возвращает список нарушений (пусто — ОК)."""
    errors: List[str] = []
    length = calculate_film_length(layout, edge_gap)
    for p in layout:
        if p.x < edge_gap or p.y < edge_gap or p.x + p.w > film_width - edge_gap:
            errors.append(
                f"Фасад №{p.idx + 1}: нарушение краевого отступа {edge_gap} мм "
                f"(x={p.x}, y={p.y}, w={p.w})."
            )
        if p.top + edge_gap > length:
            errors.append(f"Фасад №{p.idx + 1}: выход за верхний край плёнки.")
    for i in range(len(layout)):
        for j in range(i + 1, len(layout)):
            a, b = layout[i], layout[j]
            overlap = not (
                a.x >= b.x + b.w + gap
                or b.x >= a.x + a.w + gap
                or a.y >= b.y + b.h + gap
                or b.y >= a.y + a.h + gap
            )
            if overlap:
                errors.append(
                    f"Фасады №{a.idx + 1} и №{b.idx + 1}: пересечение / зазор < {gap} мм."
                )
    # Контроль длины стола: стол — отдельный отрез, длиннее стола быть не может.
    if length > table_length:
        errors.append(
            f"ОШИБКА: длина отреза {length} мм превышает длину стола {table_length} мм."
        )
    return errors


# ---------------------------------------------------------------------------
# 7. Визуализация
# ---------------------------------------------------------------------------
def visualize_layout(
    layout: List[PlacedFacade],
    film_width: int,
    film_length: int,
    gap: int,
    save_path: str = "layout.png",
    show: bool = False,
    edge_gap: int = EDGE_GAP,
    title_note: str = "",
    pos_nums: List[int] | None = None,
) -> None:
    """Построение графической схемы размещения (matplotlib).

    pos_nums — номер позиции заказа для каждого изделия (параллельно
    исходному списку фасадов): все штуки одной позиции красятся в один
    цвет и подписываются «поз.N» + размеры. Без pos_nums — старый вид
    («№» сквозной нумерации, цвет по индексу).
    """
    import colorsys

    import matplotlib.patches as patches
    import matplotlib.pyplot as plt

    def pos_color(k: int):
        """Пастельный цвет по порядковому номеру позиции (золотое сечение)."""
        r, g, b = colorsys.hsv_to_rgb((k * 0.618033988749895) % 1.0, 0.45, 0.96)
        return (r, g, b)

    pos_index: dict = {}
    if pos_nums is not None:
        for n in sorted(set(pos_nums)):
            pos_index[n] = len(pos_index)

    fig, ax = plt.subplots(figsize=(6, max(4, 10 * film_length / max(film_width, 1) * 0.5)))
    # Полотно плёнки
    ax.add_patch(
        patches.Rectangle(
            (0, 0), film_width, film_length, linewidth=1.5,
            edgecolor="black", facecolor="#f2f2f2", linestyle="-",
        )
    )
    # Рабочая зона (за вычетом краевых отступов) — зелёный пунктир
    if film_width > 2 * edge_gap and film_length > 2 * edge_gap:
        ax.add_patch(
            patches.Rectangle(
                (edge_gap, edge_gap),
                film_width - 2 * edge_gap, film_length - 2 * edge_gap,
                linewidth=1.0, edgecolor="green", facecolor="none",
                linestyle="--", alpha=0.7,
            )
        )
        ax.text(edge_gap + 3, film_length - edge_gap - 14,
                f"краевой отступ {edge_gap} мм", fontsize=6, color="green")
    colors = ["#a8d5ff", "#b5e8b5", "#ffd9a8", "#e3b8ff", "#fff3a8", "#ffb8b8", "#a8fff3"]
    for k, p in enumerate(layout):
        if pos_nums is not None and p.idx < len(pos_nums):
            pos = pos_nums[p.idx]
            color = pos_color(pos_index[pos])
            caption = f"поз.{pos}\n{p.w}x{p.h}"
        else:
            color = colors[p.idx % len(colors)]
            caption = f"№{p.idx + 1}\n{p.w}x{p.h}"
        # Зазор — пунктирная рамка вокруг детали
        ax.add_patch(
            patches.Rectangle(
                (p.x - gap / 2 if p.x > 0 else p.x, p.y - gap / 2 if p.y > 0 else p.y),
                p.w + (gap / 2 if p.x > 0 else 0),
                p.h + (gap / 2 if p.y > 0 else 0),
                linewidth=0.8, edgecolor="red", facecolor="none", linestyle="--", alpha=0.6,
            )
        )
        ax.add_patch(
            patches.Rectangle(
                (p.x, p.y), p.w, p.h, linewidth=1.2, edgecolor="navy", facecolor=color, alpha=0.9,
            )
        )
        ax.text(
            p.x + p.w / 2, p.y + p.h / 2,
            caption,
            ha="center", va="center",
            fontsize=9 if min(p.w, p.h) > 150 else 7, fontweight="bold",
        )
        ax.text(p.x + 3, p.y + p.h - 14, f"x={p.x}, y={p.y}", fontsize=6, color="dimgray")

    ax.set_xlim(-60, film_width + 60)
    ax.set_ylim(-80, film_length + 80)
    ax.set_aspect("equal")
    ax.set_xlabel("Ширина рулона, мм")
    ax.set_ylabel("Длина плёнки, мм")
    ax.set_title(
        f"Раскладка {len(layout)} фасад. на пленке {film_width}x{film_length} мм{title_note}\n"
        f"({film_length / 1000:.3f} пог. м), зазор {gap} мм, край {edge_gap} мм",
        fontsize=11,
    )
    ax.grid(True, linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    print(f"  Схема сохранена: {save_path}")
    if show:
        plt.show()
    plt.close(fig)


# ---------------------------------------------------------------------------
# 8. Отчёт и main
# ---------------------------------------------------------------------------
def print_table_report(
    table: List[PlacedFacade],
    table_no: int,
    table_count: int,
    film_width: int,
    film_length: int,
    labels: List[str] | None = None,
) -> None:
    """Отчёт по одному столу: длина отреза + таблица деталей (локальные координаты)."""
    print(f"\n--- Стол {table_no} из {table_count}: "
          f"отрез {film_length} мм ({film_length / 1000:.3f} пог. м), "
          f"деталей: {len(table)} ---")
    print(f"{'№':<4}{'Фасад':<14}{'Ориентация':<14}{'X':<8}{'Y':<8}{'На столе (XxY)':<18}Источник")
    for p in sorted(table, key=lambda q: q.idx):
        orient = "Поворот 90°" if p.rotated else "Без поворота"
        src = labels[p.idx] if labels and p.idx < len(labels) else ""
        print(
            f"{p.idx + 1:<4}{p.orig_len}x{p.orig_wid:<9}{orient:<14}"
            f"{p.x:<8}{p.y:<8}{p.w}x{p.h:<14}{src}"
        )


def table_save_path(save_path: str, table_no: int, table_count: int) -> str:
    """Имя файла схемы: один стол — как есть, несколько — с суффиксом _stN."""
    if table_count <= 1:
        return save_path
    import os

    root, ext = os.path.splitext(save_path)
    return f"{root}_st{table_no}{ext or '.png'}"


def print_report(
    facades: List[List[int]],
    tables: List[List[PlacedFacade]],
    film_width: int,
    gap: int,
    edge_gap: int = EDGE_GAP,
    labels: List[str] | None = None,
) -> None:
    lengths = [calculate_film_length(t, edge_gap) for t in tables]
    total = sum(lengths)
    area = calculate_facade_area(facades)
    util = material_utilization(facades, film_width, total)
    print(f"Количество фасадов:            {len(facades)}")
    print(f"Общая площадь фасадов:         {area:.3f} м2")
    print(f"Ширина рулона плёнки:          {film_width} мм")
    print(f"Рабочий стол:                  {TABLE_LENGTH} x {TABLE_WIDTH} мм")
    print(f"Технологический зазор:         {gap} мм")
    print(f"Краевой отступ от краёв:       {edge_gap} мм")
    print(f"Столов (отрезов) нужно:        {len(tables)}")
    for i, ln in enumerate(lengths, 1):
        print(f"  Стол {i}: длина отреза       {ln} мм ({ln / 1000:.3f} пог. м)")
    print(f"Суммарная длина плёнки:        {total} мм")
    print(f"Суммарный расход:              {total / 1000:.3f} пог. м")
    print(f"Коэффициент использования:     {util:.1%}")
    for i, (t, ln) in enumerate(zip(tables, lengths), 1):
        print_table_report(t, i, len(tables), film_width, ln, labels)


def run_case(
    name: str,
    facades: List[List[int]],
    film_width: int = FILM_WIDTH,
    gap: int = GAP,
    allow_rotation: bool = ALLOW_ROTATION,
    visualize: bool = False,
    save_path: str = "layout.png",
    edge_gap: int = EDGE_GAP,
    labels: List[str] | None = None,
    pos_nums: List[int] | None = None,
) -> None:
    print(f"\n{'=' * 64}\n{name}\n{'=' * 64}")
    try:
        validate_facades(facades, film_width, TABLE_LENGTH, TABLE_WIDTH, edge_gap)
    except ValueError as e:
        print(f"ОШИБКА входных данных: {e}")
        return
    if not facades:
        print("Пустой список фасадов: плёнка не требуется (0 мм, 0 пог. м).")
        return
    try:
        tables = optimize_layout(facades, film_width, gap, allow_rotation,
                                 edge_gap, TABLE_LENGTH)
    except ValueError as e:
        print(f"ОШИБКА размещения: {e}")
        return
    print_report(facades, tables, film_width, gap, edge_gap, labels)
    problems: List[str] = []
    for i, t in enumerate(tables, 1):
        for msg in verify_layout(t, film_width, gap,
                                 TABLE_LENGTH, TABLE_WIDTH, edge_gap):
            problems.append(f"Стол {i}: {msg}")
    # Все детали должны быть размещены ровно по одному разу.
    placed_count = sum(len(t) for t in tables)
    if placed_count != len(facades):
        problems.append(
            f"ОШИБКА: размещено {placed_count} из {len(facades)} изделий!"
        )
    if problems:
        print("\nПроверка ограничений:")
        for msg in problems:
            print(f"  [X] {msg}")
    else:
        print("\nПроверка ограничений: [OK] все зазоры, границы и стол в норме.")
    if visualize:
        for i, t in enumerate(tables, 1):
            ln = calculate_film_length(t, edge_gap)
            path = table_save_path(save_path, i, len(tables))
            note = f", стол {i} из {len(tables)}" if len(tables) > 1 else ""
            visualize_layout(t, film_width, ln, gap, path, False, edge_gap,
                             note, pos_nums)


def ask_film_texture(default_no_texture: bool = True) -> bool:
    """Спросить пользователя о типе плёнки. Возвращает allow_rotation.

    Плёнка С ТЕКСТУРОЙ (структурой, рисунком) — поворачивать фасады на 90°
    нельзя (allow_rotation=False).
    Плёнка БЕЗ ТЕКСТУРЫ (однотонная) — поворот разрешён (allow_rotation=True).

    При отсутствии интерактивного ввода (EOF) возвращается значение
    по умолчанию (без текстуры — повороты разрешены).
    """
    print("Тип ПВХ-плёнки влияет на расчёт:")
    print("  - плёнка С ТЕКСТУРОЙ: поворот фасадов на 90° ЗАПРЕЩЁН;")
    print("  - плёнка БЕЗ ТЕКСТУРЫ: поворот РАЗРЕШЁН (меньше расход).")
    yes = {"да", "д", "yes", "y", "1", "с", "с текстурой", "текстура", "+"}
    no = {"нет", "н", "no", "n", "0", "без", "без текстуры", "без текстуры ", "-"}
    while True:
        try:
            ans = input("Плёнка с текстурой? (да/нет, по умолчанию — нет): ").strip().lower()
        except EOFError:
            print("\n  [нет ввода — принято по умолчанию: плёнка БЕЗ текстуры]")
            return default_no_texture
        if ans == "":
            return default_no_texture
        if ans in yes:
            return False
        if ans in no:
            return True
        print("  Пожалуйста, ответьте «да» или «нет».")


def parse_args():
    """Аргументы командной строки (позволяют пропустить вопрос)."""
    import argparse

    p = argparse.ArgumentParser(description="Расчёт расхода ПВХ-плёнки для фасадов.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--texture", action="store_true",
                   help="Плёнка С ТЕКСТУРОЙ: повороты на 90° запрещены (без вопроса).")
    g.add_argument("--no-texture", action="store_true",
                   help="Плёнка БЕЗ ТЕКСТУРЫ: повороты разрешены (без вопроса).")
    p.add_argument("--order", default=None,
                   help="Номер заказа из файла (без вопроса).")
    p.add_argument("--file", default=ORDERS_FILE,
                   help="Путь к файлу заказов (по умолчанию 22.txt рядом со скриптом).")
    p.add_argument("--demo", action="store_true",
                   help="Запустить встроенные демо-тесты вместо работы с заказами.")
    return p.parse_args()


def run_demo_tests() -> None:
    """Встроенные синтетические тесты (проверка алгоритма)."""
    # --- Дополнительные тесты из п. 9 ---
    run_case("Тест: один фасад", [[800, 500]])
    run_case("Тест: одинаковые фасады", [[600, 400]] * 6)
    run_case("Тест: фасад макс. размера (с учётом краевых отступов)", [[2900, 1100]])
    run_case(
        "Тест: только после поворота (1100x1300, рулон 1200)",
        [[1100, 1300]],  # исходная ширина 1300 > 1200: вход только поворотом
    )
    run_case(
        "Тест: деталь шире рулона в обеих ориентациях (ошибка)",
        [[1300, 1250]],
    )
    run_case("Тест: пустой список", [])
    run_case(
        "Тест: без поворотов",
        FACADES, allow_rotation=False,
    )


def cleanup_schemes() -> int:
    """Удалить все PNG-схемы рядом со скриптом (чтобы не копились старые).

    Возвращает число удалённых файлов.
    """
    import glob
    import os

    base = os.path.dirname(os.path.abspath(__file__))
    removed = 0
    for path in glob.glob(os.path.join(base, "*.png")):
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def main() -> None:
    """Запуск программы: номер заказа -> размеры -> текстура -> расчёт."""
    args = parse_args()
    n = cleanup_schemes()
    if n:
        print(f"Удалены старые схемы: {n} файл(а).")
    if args.demo:
        run_case(
            "ОСНОВНОЙ ТЕСТ (из задания)",
            FACADES, visualize=True, save_path="layout_main.png",
        )
        run_demo_tests()
        return

    try:
        orders = load_orders(args.file)
    except ValueError as e:
        print(f"ОШИБКА: {e}")
        return
    if args.order:
        order = find_order(orders, args.order)
        if order is None:
            print(f"ОШИБКА: заказ «{args.order}» не найден в файле {args.file}.")
            return
    else:
        order = ask_order_number(orders)
        if order is None:
            print("Выход.")
            return
    if not order.details:
        print(f"Заказ {order.number} ({order.client}): нет строк деталей — считать нечего.")
        return
    facades, labels, pos_nums = expand_order_facades(order)
    print(f"Заказ {order.number} ({order.client}): "
          f"позиций {len(order.details)}, изделий {len(facades)}.")
    for d in order.details:
        print(f"  поз.{d.num}: {d.length}x{d.width} x{d.qty} шт."
              + (f" [{d.typ}]" if d.typ else ""))

    if args.texture:
        allow_rotation = False
        print("Режим: плёнка С ТЕКСТУРОЙ — повороты на 90° запрещены (флаг --texture).")
    elif args.no_texture:
        allow_rotation = True
        print("Режим: плёнка БЕЗ ТЕКСТУРЫ — повороты разрешены (флаг --no-texture).")
    else:
        allow_rotation = ask_film_texture()
        if allow_rotation:
            print("Режим: плёнка БЕЗ ТЕКСТУРЫ — повороты разрешены.")
        else:
            print("Режим: плёнка С ТЕКСТУРОЙ — повороты на 90° запрещены.")

    run_case(
        f"Заказ {order.number} ({order.client})",
        facades, allow_rotation=allow_rotation,
        visualize=True, save_path=f"layout_{order.number}.png",
        labels=labels, pos_nums=pos_nums,
    )


if __name__ == "__main__":
    main()
