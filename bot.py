#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Телеграм-бот расчёта расхода ПВХ-плёнки (aiogram 3).

Сценарий: кнопка «Расход пленки» -> «Напишите номер заказа» ->
кнопки «Структурой» / «Без структуры» -> все PNG-схемы столов +
строка «Итого плёнки: ... пог. м» (с учётом правила оплаты:
отрез >= 2000 мм — кусок 3200 мм, короче — отрез + 200 мм).

Запуск (Windows):
    set BOT_TOKEN=<токен>
    .venv\\Scripts\\python.exe bot.py
"""

import asyncio
import logging
import os
import shutil
import sys
import tempfile

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from config import BOT_TOKEN
import run as calc

ORDER_BUTTON = "Расход пленки"
CB_TEXTURE = "film:texture"  # плёнка со структурой — поворот запрещён
CB_PLAIN = "film:plain"      # без структуры — поворот разрешён

dp = Dispatcher()


class Calc(StatesGroup):
    waiting_order = State()
    waiting_texture = State()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=ORDER_BUTTON)]],
        resize_keyboard=True,
    )


def texture_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Структурой", callback_data=CB_TEXTURE),
            InlineKeyboardButton(text="Без структуры", callback_data=CB_PLAIN),
        ]]
    )


def get_order(query: str):
    """Найти заказ по номеру (файл перечитывается при каждом запросе)."""
    return calc.find_order(calc.load_orders(), query)


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Расчёт расхода ПВХ-плёнки.\n"
        "Нажмите «Расход пленки» и введите номер заказа.",
        reply_markup=main_keyboard(),
    )


@dp.message(F.text == ORDER_BUTTON)
async def ask_order(message: Message, state: FSMContext) -> None:
    await state.set_state(Calc.waiting_order)
    await message.answer("Напишите номер заказа:")


@dp.message(Calc.waiting_order)
async def got_order(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    try:
        order = get_order(query)
    except ValueError as e:
        await message.answer(f"ОШИБКА: {e}\nНапишите номер заказа:")
        return
    if order is None:
        await message.answer(f"Заказ «{query}» не найден. Напишите номер заказа:")
        return
    if not order.details:
        await message.answer(
            f"Заказ {order.number}: нет деталей — считать нечего. "
            "Напишите другой номер:"
        )
        return
    await state.update_data(order_number=order.number)
    await state.set_state(Calc.waiting_texture)
    await message.answer(
        f"Заказ {order.number}: позиций {len(order.details)}, "
        f"изделий {order.total_pieces}. Выберите плёнку:",
        reply_markup=texture_keyboard(),
    )


@dp.callback_query(Calc.waiting_texture, F.data.in_({CB_TEXTURE, CB_PLAIN}))
async def got_texture(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    await state.clear()
    allow_rotation = callback.data == CB_PLAIN
    status = await callback.message.answer("Считаю...")
    try:
        order = get_order(data.get("order_number", ""))
        if order is None or not order.details:
            await callback.message.answer("Заказ не найден. Нажмите «Расход пленки».")
            return
        facades, _labels, pos_nums = calc.expand_order_facades(order)
        calc.validate_facades(facades, calc.FILM_WIDTH, calc.TABLE_LENGTH,
                              calc.TABLE_WIDTH, calc.EDGE_GAP)
        tables = calc.optimize_layout(facades, calc.FILM_WIDTH, calc.GAP,
                                      allow_rotation, calc.EDGE_GAP,
                                      calc.TABLE_LENGTH)
        problems: list = []
        for t in tables:
            problems += calc.verify_layout(t, calc.FILM_WIDTH, calc.GAP,
                                           calc.TABLE_LENGTH, calc.TABLE_WIDTH,
                                           calc.EDGE_GAP)
        if sum(len(t) for t in tables) != len(facades):
            problems.append("размещены не все изделия!")
        tmpdir = tempfile.mkdtemp(prefix="film_")
        try:
            for i, t in enumerate(tables, 1):
                ln = calc.calculate_film_length(t, calc.EDGE_GAP)
                path = os.path.join(
                    tmpdir,
                    calc.table_save_path(f"layout_{order.number}.png",
                                         i, len(tables)),
                )
                note = f", стол {i} из {len(tables)}" if len(tables) > 1 else ""
                calc.visualize_layout(t, calc.FILM_WIDTH, ln, calc.GAP, path,
                                      False, calc.EDGE_GAP, note, pos_nums)
                await callback.message.answer_photo(
                    FSInputFile(path),
                    caption=f"Стол {i} из {len(tables)}: "
                            f"отрез {ln} мм ({ln / 1000:.3f} пог. м)",
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        billed_total = sum(
            calc.billed_film_length(calc.calculate_film_length(t, calc.EDGE_GAP))
            for t in tables
        )
        await callback.message.answer(
            f"Итого плёнки: {billed_total / 1000:.3f} пог. м"
        )
        if problems:
            await callback.message.answer(
                "Внимание, нарушения проверки: " + "; ".join(problems)
            )
    except ValueError as e:
        await callback.message.answer(f"ОШИБКА: {e}")
    finally:
        try:
            await status.delete()
        except Exception:  # noqa: BLE001 — чистка статуса не должна ронять хендлер
            pass


async def main() -> None:
    token = BOT_TOKEN
    if not token:
        print("ОШИБКА: задайте токен в переменной окружения BOT_TOKEN.")
        sys.exit(1)
    await dp.start_polling(Bot(token=token))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
