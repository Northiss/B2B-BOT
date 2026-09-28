"""
Обёртка над Claude API для двух задач:
  1. summarize_pain — сжать свободный текст клиента в короткое резюме для
     менеджера и для скоринга.
  2. is_vague / clarify_question — определить, что ответ слишком общий,
     и сгенерировать уточняющий вопрос вместо жёсткого скрипта.

Требует переменную окружения ANTHROPIC_API_KEY.
"""

import os
import anthropic

_headers = {}
if os.environ.get("ANTHROPIC_WORKSPACE_ID"):
    # Нужно, если API-ключ не привязан к конкретному workspace
    _headers["anthropic-workspace-id"] = os.environ["ANTHROPIC_WORKSPACE_ID"]

client = anthropic.Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    default_headers=_headers or None,
)
MODEL = "claude-sonnet-4-6"


def summarize_pain(raw_text: str) -> str:
    """Возвращает одно предложение с сутью задачи клиента."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=100,
        system=(
            "Ты помогаешь отделу продаж B2B-компании. Сожми ответ клиента "
            "в одно короткое деловое предложение на русском языке: какая у "
            "него задача/боль. Без вступлений, без кавычек, только суть."
        ),
        messages=[{"role": "user", "content": raw_text}],
    )
    return response.content[0].text.strip()


def is_vague(raw_text: str) -> bool:
    """Простая эвристика + при необходимости проверка через модель."""
    if len(raw_text.strip().split()) >= 6:
        return False
    response = client.messages.create(
        model=MODEL,
        max_tokens=5,
        system=(
            "Ответь только 'да' или 'нет'. Достаточно ли конкретен этот "
            "ответ клиента, чтобы понять его бизнес-задачу?"
        ),
        messages=[{"role": "user", "content": raw_text}],
    )
    return "нет" in response.content[0].text.strip().lower()


def clarify_question(raw_text: str) -> str:
    """Генерирует один короткий уточняющий вопрос по теме ответа клиента."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=60,
        system=(
            "Ты менеджер по продажам B2B. Клиент дал слишком общий ответ. "
            "Задай ОДИН короткий уточняющий вопрос на русском, чтобы понять "
            "его задачу конкретнее. Без приветствий, только сам вопрос."
        ),
        messages=[{"role": "user", "content": raw_text}],
    )
    return response.content[0].text.strip()
