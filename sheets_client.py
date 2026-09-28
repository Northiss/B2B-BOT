"""
Хранилище лидов на Google Sheets — временная "CRM-lite".

Когда выберете полноценную CRM (amoCRM/Bitrix24/HubSpot), эту функцию
append_lead достаточно заменить на вызов их API — весь остальной код
бота менять не придётся.

Настройка:
  1. Создайте проект в Google Cloud, включите Google Sheets API.
  2. Создайте Service Account, скачайте JSON-ключ, положите путь в
     GOOGLE_SHEETS_CREDS_FILE.
  3. Создайте таблицу, дайте Service Account доступ (Editor) по email
     из JSON-ключа.
  4. Впишите ID таблицы в GOOGLE_SHEET_ID (из URL таблицы).
"""

import datetime
import os

import gspread

CREDS_FILE = os.environ["GOOGLE_SHEETS_CREDS_FILE"]
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]

_gc = gspread.service_account(filename=CREDS_FILE)
_sheet = _gc.open_by_key(SHEET_ID).sheet1

HEADER = [
    "Дата", "Telegram ID", "Username", "Статус", "Скоринг",
    "Задача", "Роль", "Бюджет", "Сроки", "Имя", "Контакт",
]


def _ensure_header():
    if _sheet.row_values(1) != HEADER:
        _sheet.insert_row(HEADER, 1)


def append_lead(lead, status: str, score: int) -> None:
    _ensure_header()
    _sheet.append_row([
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        lead.telegram_id,
        lead.username,
        status,
        score,
        lead.pain_summary,
        lead.role_label,
        lead.budget_label,
        lead.timeline_label,
        lead.name,
        lead.contact,
    ])
