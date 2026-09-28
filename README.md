# Автоматический квалификатор лидов (Telegram, B2B)

Telegram-бот на **aiogram 3**, который проводит первичное интервью с клиентом,
выявляет боль, роль в принятии решения, бюджет и сроки, считает скоринг
(hot / warm / cold), записывает лида в Google Sheets («CRM-lite») и отправляет
уведомление менеджеру, если лид тёплый или горячий.

## Как это работает

```
Клиент → Telegram-бот (aiogram 3, FSM)
            ├─ сценарий: боль → роль → бюджет → сроки → имя → контакт → подтверждение
            ├─ Claude API: резюме «боли» и уточняющий вопрос, если ответ слишком общий
            ├─ скоринг 0–100 → hot / warm / cold
            ├─ Google Sheets: строка на каждого лида
            └─ уведомление менеджеру в Telegram (для hot / warm)
```

**Скоринг** (4 параметра по 25 баллов): чёткая боль, роль в решении, бюджет, сроки.
`≥75` — hot, `40–74` — warm, `<40` — cold (менеджеру не уходит).

## Структура проекта

| Файл | Назначение |
|---|---|
| `bot.py` | Сценарий диалога, кнопки, скоринг, уведомления |
| `claude_extractor.py` | Вызовы Claude API (резюме, уточняющие вопросы) |
| `sheets_client.py` | Запись лидов в Google Sheets |
| `test_net.py` | Диагностика стабильности соединения с Telegram |
| `.env.example` | Шаблон переменных окружения |

## Быстрый старт

```bash
pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env
# заполните .env своими значениями
python bot.py
```

## Настройка

1. **Telegram-бот** — создайте у [@BotFather](https://t.me/BotFather), токен → `TELEGRAM_BOT_TOKEN`.
2. **Получатель уведомлений** — свой ID у [@userinfobot](https://t.me/userinfobot)
   (или ID группы) → `MANAGERS_CHAT_ID`. Напишите боту `/start` хотя бы раз,
   иначе Telegram не даст ему писать вам первым.
3. **Claude API** — ключ в [console.anthropic.com](https://console.anthropic.com) → `ANTHROPIC_API_KEY`.
   Если API отвечает ошибкой про workspace, создайте ключ внутри workspace
   либо задайте `ANTHROPIC_WORKSPACE_ID`.
4. **Google Sheets**
   1. В Google Cloud создайте проект и включите **Google Sheets API**.
   2. Создайте Service Account и скачайте JSON-ключ (сохраните как `service-account.json`).
   3. Создайте таблицу и откройте к ней доступ «Редактор» для `client_email` из JSON-ключа.
   4. ID таблицы (часть URL между `/d/` и `/edit`) → `GOOGLE_SHEET_ID`.

## Деплой на Render

Бот работает через long polling и не слушает HTTP-порт, поэтому в Render это
**Background Worker** (не Web Service). Параметры лежат в `render.yaml`.

| Параметр | Значение |
|---|---|
| Type | Background Worker |
| Runtime | Python 3 |
| Region | Frankfurt (или ближайший к вам) |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python bot.py` |
| Instance | Starter ($7/мес) или выше — воркер не работает на free-тарифе |

Переменные окружения (раздел Environment): `TELEGRAM_BOT_TOKEN`, `MANAGERS_CHAT_ID`,
`ANTHROPIC_API_KEY`, `GOOGLE_SHEET_ID`, `GOOGLE_CREDS_JSON` (всё содержимое JSON-ключа),
`PYTHON_VERSION=3.12.8`.

Запускайте **ровно один экземпляр** бота: два одновременных polling-процесса с одним
токеном конфликтуют. Не запускайте бота локально, пока он работает на Render.

## Безопасность

Никогда не коммитьте `.env` и JSON-ключ сервисного аккаунта — они уже
перечислены в `.gitignore`. Если токен или ключ случайно попали в репозиторий,
перевыпустите их (`/revoke` у @BotFather, новый ключ в Google Cloud и Anthropic Console).

## Кастомизация

- **Вопросы, кнопки и веса** — `ROLE_OPTIONS`, `BUDGET_OPTIONS`, `TIMELINE_OPTIONS` в `bot.py`.
- **Пороги hot / warm / cold** — `LeadData.status()`.
- **Промпты Claude** — `claude_extractor.py`.
- **Хранилище** — при переходе на amoCRM / Bitrix24 / HubSpot замените `append_lead()`
  в `sheets_client.py`, остальной код менять не нужно.

## Известные особенности

- Состояние диалогов хранится в памяти (`MemoryStorage`) и сбрасывается при перезапуске.
  Для продакшена подключите `RedisStorage`.
- При нестабильном соединении с Telegram бот повторяет неудавшиеся запросы
  (до 5 раз, см. `RetryMiddleware`). Если сбои частые, используйте VPN/прокси
  (`TELEGRAM_PROXY`) или запускайте бота на VPS. Проверить связь: `python test_net.py`.
- Язык интерфейса — русский, тексты заданы в коде.

## Roadmap

1. Ответы на произвольные вопросы клиента через Claude с базой знаний о продукте.
2. Миграция на полноценную CRM, распределение лидов между менеджерами.
3. Аналитика воронки диалога (на каком вопросе клиенты уходят).
