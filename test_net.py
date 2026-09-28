"""Проверка стабильности соединения с api.telegram.org (10 попыток)."""
import http.client
import time

ok = 0
for i in range(1, 11):
    try:
        conn = http.client.HTTPSConnection("api.telegram.org", timeout=10)
        conn.request("GET", "/")
        status = conn.getresponse().status
        print(f"{i:>2}: OK ({status})")
        ok += 1
    except Exception as exc:
        print(f"{i:>2}: СБОЙ ({type(exc).__name__})")
    time.sleep(1)

print(f"\nУспешно: {ok} из 10")
