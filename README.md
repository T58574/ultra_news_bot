# Ultra News Bot

Персональный one-shot генератор утренних и вечерних Telegram-дайджестов из Hacker News, Reddit, GitHub и RSS/Atom в нетраннерском формате.

Работает по расписанию через `systemd timer` (0 МБ ОЗУ в простое, отправка напрямую в Telegram Bot API через `httpx` без `aiogram`).

## Архитектура под Free Tier Gemini

Пайплайн делает **всего 2 LLM-запроса** за один дайджест:
1. Алгоритмический отсев дублей (URL + заголовок) и локальный скоринг оставляют топ-70 кандидатов.
2. **Шаг 1 (`gemini-3.1-flash-lite`)** — одним батчем выбирает 10–12 главных новостей и склеивает пересекающиеся темы.
3. Локальная докачка полных текстов и `og:image` через `trafilatura` и `BeautifulSoup`.
4. **Шаг 2 (`gemini-3.5-flash-lite`)** — одним запросом пишет сводку на русском в Telegram HTML-формате.

При `503 UNAVAILABLE` или `429 RESOURCE_EXHAUSTED` роутер переключается по цепочке `GEMINI_FALLBACK_MODELS` (включая обычные `flash`-модели и предыдущее поколение `2.5`) и повторяет попытки с экспоненциальным backoff (`GEMINI_MAX_ATTEMPTS`, `GEMINI_BACKOFF_BASE_SECONDS`, `GEMINI_BACKOFF_MAX_SECONDS`).

## Быстрый старт

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# Заполни TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, ALLOWED_USER_IDS и GOOGLE_API_KEY в .env
.venv/bin/ultra-news-bot
```

## Запуск по расписанию (systemd user timer)

Шаблоны юнитов лежат в `deploy/ultra-news-bot.service` и `deploy/ultra-news-bot.timer` (замените `PROJECT_ROOT` на путь к репозиторию):

```bash
mkdir -p ~/.config/systemd/user
sed "s|PROJECT_ROOT|$PWD|g" deploy/ultra-news-bot.service > ~/.config/systemd/user/ultra-news-bot.service
cp deploy/ultra-news-bot.timer ~/.config/systemd/user/ultra-news-bot.timer
systemctl --user daemon-reload
systemctl --user enable --now ultra-news-bot.timer
systemctl --user list-timers ultra-news-bot.timer
```

Ручной запуск в любой момент:

```bash
systemctl --user start ultra-news-bot.service
# или напрямую:
.venv/bin/ultra-news-bot
```

## Проверка тестами

```bash
.venv/bin/pytest
```
