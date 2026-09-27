# TheEscapistsModding1

Моддинг **The Escapists 1 (PC / Clickteam Fusion 2.5)**: исследование + моды.

## ★ Модлаунчер `TE1_Mod_Launcher.bat`

Один файл рядом с `TheEscapists.exe` — и все моды ставятся через меню
(консоль, Windows, работает из-под прав администратора, если игра в
Program Files). Сейчас в нём три мода:

| Мод | Что делает |
|---|---|
| **Randomizer** | перемешивает параметры всех предметов при каждом запуске |
| **Better Translate** | чинит официальный русский перевод (469 правок) |
| **Guard Key Names** | офицеры названы по ключу, который носят (см. ниже) |

Как пользоваться: положи `TE1_Mod_Launcher.bat` в папку игры и запусти
двойным кликом → «Mod Workshop» → выбери мод → «Install». Запуск игры —
из того же лаунчера («Launch Game With Mods»). Движок лаунчера живёт в
`launcher_src/`, собирается в .bat скриптом `tools/build_launcher.py`
(после правок: `python3 tools/build_launcher.py`).

## ★ Мод «Guard Key Names» → [`mods/guard_keys/`](mods/guard_keys/README.md)

Офицеры автоматически называются по цвету ключа, который носят
(`Officer Cell Key`, `Officer Utility Key`, `Officer Entrance Key`,
**`Officer Staff Key`** — тот самый 4-й с красным ключом, `Officer Work Key`).
Вся остальная игра не меняется.

**Способ 1 (рекомендуется):** через `TE1_Mod_Launcher.bat` — мод ставится
пунктом меню, бэкап exe кладётся в `mods/original/exe`, патч автоматически
починяется после проверки файлов Steam.

**Способ 2 (вручную):**

```bash
python mods/guard_keys/te1_guardkeys.py check  TheEscapists.exe
python mods/guard_keys/te1_guardkeys.py patch  TheEscapists.exe -o TheEscapists_guardkeys.exe
```

**Windows:** перетащи `TheEscapists.exe` на `mods/guard_keys/INSTALL.bat`.

* Зачем это работает: в байткоде игры группа `assign_keys` (фрейм `game`)
  всегда раздаёт ключи одинаково: офицер1→Cell(жёлтая), 2→Utility(оранж),
  3→Entrance(фиолетовая), 4→Staff(**красная**), 5→Work(зелёная) — в любой тюрьме.
* Технический разбор: [`docs/GUARD_KEY_MOD.md`](docs/GUARD_KEY_MOD.md).

## Архивы с исследованием (основа репозитория)

| Файл | Содержимое |
|---|---|
| `TE1ModdingPart1.zip` | весь тулкит TE1Modding: лаунчер модов, тулзы (`te1_patch.py`, `te1_events.py`, `te_crypto.py`…), доки по шифрованию/форматам exe, дампы всех 5473 групп событий, образец `Data/`, банк 3948 PNG-иконок |
| `TE1ModdingPart2.zip` | дополнение: дампы exe **редактора карт** и pol-сборки игры, языковые `*.dat` редактора |

Распаковать всё в одну папку:

```bash
unzip TE1ModdingPart1.zip && unzip TE1ModdingPart2.zip
# общий корень: TE1Modding-arena-01a0e25c-te1modding/
```

## Структура репозитория

```
TE1_Mod_Launcher.bat    — ★ лаунчер всех модов (собирается из launcher_src/)
launcher_src/           — движок лаунчера (te1_engine.py, fixes_rus.json)
tools/build_launcher.py — сборка движка в TE1_Mod_Launcher.bat (ASCII+CRLF)
tools/test_launcher.py  — регрессия: два Data-мода + логика слежения за игрой
tools/test_guardkeys_launcher.py — e2e-тест exe-мода (нужен образец exe)
mods/guard_keys/        — ★ мод (патчер te1_guardkeys.py + INSTALL.bat + README)
data_samples/           — образец Data/ из игры (нужен тестам лаунчера)
tools/te_crypto…te1_events — минимальный набор TE1Modding под импорт патчером;
                          вендорено из TE1ModdingPart1.zip
docs/GUARD_KEY_MOD.md   — механика офицер↔ключ и устройство патча
TE1ModdingPart1.zip     — исследование/тулкит (часть 1)
TE1ModdingPart2.zip     — исследование/тулкит (часть 2)
```

## Замечания

* Пропатченные exe в репозитории не храним (это бинарь игры) — патчер
  собирает их локально за секунду (`out/` в `.gitignore`).
* Откат мода: Steam → «Проверить целостность файлов» или вернуть `.bak`.
