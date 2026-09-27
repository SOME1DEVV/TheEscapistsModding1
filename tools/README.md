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
