# adultbots

Два Telegram-бота подписок на закрытый канал: `lettiebrown` и `vixieblaze`.

Каждый бот — отдельная папка с `main.py`. Все настройки — в блоке CONFIG в начале `main.py`
(.env не нужен). Секреты (токены) выносятся в `local_config.py` рядом с `main.py` —
этот файл в git не попадает, его нужно создать один раз на каждой машине.

## Запуск через Docker

```bash
git clone https://github.com/hundlervpn/adultbots.git
cd adultbots

# создать local_config.py для каждого бота (или скопировать с локальной машины):
# scp vixieblaze/local_config.py root@СЕРВЕР:/путь/adultbots/vixieblaze/

docker compose up -d --build
docker compose logs -f          # смотреть логи (логи одного бота: docker compose logs -f vixieblaze)
```

База оплат `payments.sqlite3` создаётся автоматически рядом с `main.py` и переживает
перезапуски. Чтобы перенести базу со старой машины — просто скопируйте этот файл.

## Обновление ботов

```bash
git pull
docker compose up -d --build
```

## Остановка / перезапуск

```bash
docker compose stop             # остановить
docker compose up -d            # запустить
docker compose restart vixieblaze   # перезапустить одного бота
```

## Содержимое local_config.py

```python
BOT_TOKEN = "123456:ABC-DEF..."            # от @BotFather
CRYPTO_PAY_TOKEN = "..."                   # @CryptoBot -> Crypto Pay -> Create App
CRYSTALPAY_AUTH_LOGIN = ""                 # логин кассы Crystal Pay (если используется)
CRYSTALPAY_AUTH_SECRET = ""                # секрет кассы Crystal Pay
```

## Запуск без Docker (для локальной проверки)

```bash
python3 -m venv .venv
.venv/bin/pip install -r lettiebrown/requirements.txt
.venv/bin/python lettiebrown/main.py
```
