# tools/ — минимальный набор TE1Modding (вендорные копии)

Эти четыре файла скопированы из `TE1ModdingPart1.zip` (репозиторий TE1Modding)
без изменений логики, чтобы работал `mods/guard_keys/te1_guardkeys.py`:

| Файл | Назначение |
|---|---|
| `te_crypto.py` | расшифровка/шифрование чанков exe (Cipher) |
| `te1_icons.py` | поиск потока чанков в PE-оверлее, zdec/universal |
| `te1_frames.py` | обход фреймов и подчанков (правка: ещё ищет импорты в своей же папке) |
| `te1_events.py` | полный парсер байткода событий Clickteam Fusion 2.5 |

Полный тулкит (лаунчер, рандомайзер, правки перевода, дампы, доки) —
в архивах `TE1ModdingPart1.zip` / `TE1ModdingPart2.zip`.

## Скрипты лаунчера

| Файл | Назначение |
|---|---|
| `build_launcher.py` | собирает `launcher_src/te1_engine.py` + `fixes_rus.json` в корневой `TE1_Mod_Launcher.bat` (только ASCII, CRLF). После любой правки `launcher_src/`: `python3 tools/build_launcher.py`; `--check` — проверить, что .bat свежий |
| `test_launcher.py` | регрессия Data-модов и логики запуска игры на фейковой папке из `data_samples/` |
| `test_guardkeys_launcher.py` | e2e-тест exe-мода: настоящий exe + лаунчер (установка/починка/удаление). Нужен образец exe: `TE1_SAMPLE_EXE=/путь/к/theescapists_eur.exe` (без него — SKIP) |

`data_samples/` (в корне) — образец папки `Data/` игры, нужен `test_launcher.py`.
