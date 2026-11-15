# Ultra News Bot

Персональный Telegram-бот для утренних и вечерних дайджестов из Hacker News, Reddit, GitHub и RSS/Atom в нетраннерском формате.

## Архитектура под Free Tier Gemini

Пайплайн делает **всего 2 LLM-запроса** за один дайджест:
1. Алгоритмический отсев дублей (URL + заголовок) и локальный скоринг оставляют топ-70 кандидатов.
2. **Шаг 1 (`gemini-3.1-flash-lite`)** — одним батчем выбирает 10–12 главных новостей и склеивает пересекающиеся темы.
3. Локальная докачка полных текстов и `og:image` через `trafilatura` и `BeautifulSoup`.
4. **Шаг 2 (`gemini-3.5-flash-lite`)** — одним запросом пишет сводку на русском в Telegram HTML-формате.

После бета-теста в `.env` можно переключить `GEMINI_SUMMARY_MODEL` на старшую модель (например, Hydrogen).

## Быстрый старт

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# Заполни TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS и GOOGLE_API_KEY в .env
.venv/bin/ultra-news-bot
```

## Проверка тестами

```bash
.venv/bin/pytest
```
