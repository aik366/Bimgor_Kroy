# AGENTS.md

Single-file Python app: all logic is in `run.py` (~930 lines). `bot.py` is a thin aiogram3 wrapper around it. No packages, tests, lint, CI, or config files.

## Run

- Deps: `pip install -r requirements.txt` (`matplotlib>=3.8` for schemes, `aiogram>=3.0` only for `bot.py`). `--demo` and order runs visualize (`visualize=True`), so they need it; pure compute works without it only if `visualize=False`.
- Requires Python 3.11+ (`X | Y` type syntax in `run.py`).
- `python run.py --demo` — built-in synthetic tests, no input file needed. Use this to verify algorithm changes.
- `python run.py --order 0002 --no-texture` — non-interactive order run. `--texture` = rotation forbidden, `--no-texture` = rotation allowed.
- Without flags it prompts interactively: order number, then film texture (empty answer defaults to no-texture).
- `python run.py --help` — all options (`--order`, `--file`, `--texture/--no-texture`, `--demo`).
- `python bot.py` — Telegram bot, needs `BOT_TOKEN` env var. Renders PNGs to a temp dir (never next to `run.py`, see `cleanup_schemes` gotcha below).

## Gotchas

- `main()` deletes every `*.png` next to `run.py` on each start (`cleanup_schemes`). Don't store anything valuable as PNG there.
- Multi-table orders write one file per table: `layout_<order>_stN.png` (single table: no suffix). `--demo` writes `layout_main.png`.
- `22.txt` (order database) is gitignored, contains personal data — never commit or paste its contents. Expected next to `run.py` (or pass `--file`). Encoding `cp1251` with `utf-8-sig`/`utf-8` fallback; orders split by `#@#`; detail rows are `№|length|width|qty|...`, non-numeric header rows are skipped.
- Dimensions have no CLI flags — edit constants at top of `run.py`: `TABLE_LENGTH=3000`, `TABLE_WIDTH=1250`, `FILM_WIDTH=1200`, `GAP=50`, `EDGE_GAP=50` (all mm). Billing rule constants: `BILL_THRESHOLD=2000`, `BILL_FULL_CUT=3200`, `BILL_SMALL_EXTRA=200`.
- Coordinate convention (do not swap): facade `[length, width]` = `[Y-along-film, X-across-film]`; unrotated placement is `w=width, h=length`. Rotation semantics depend on film texture — keep `ask_film_texture` / `--texture` mapping intact.
- Packing is heuristic BLF + 6 sort strategies (`optimize_layout`), not optimal; `pack_tables` is first-fit across open tables (each piece tries existing tables first) so early tables get backfilled. `verify_layout` + piece-count check in `run_case` is the correctness gate. Film length = `max(top) + EDGE_GAP`; each cut must stay `<= TABLE_LENGTH`. Report shows both physical cut and billed length (`billed_film_length`: cut >= 2000 → 3200, else cut + 200).
