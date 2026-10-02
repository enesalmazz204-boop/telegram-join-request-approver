import os
import asyncio
import logging

from telegram import Update
from telegram.error import RetryAfter, TelegramError
from telegram.ext import Application, ChatJoinRequestHandler, ContextTypes


# Railway Variables:
# BOT_TOKEN = '8206822443:AAG1wWM5woL05ruRNmdoJcKN3vcZNMzSTfc'
# TARGET_GROUP = @novaprimesohbet veya -1001234567890
# APPROVAL_CONCURRENCY = aynı anda kaç onay isteği gönderileceği (varsayılan 20)

BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_GROUP = os.getenv("TARGET_GROUP", "novaprimesohbet").strip()
APPROVAL_CONCURRENCY = max(1, int(os.getenv("APPROVAL_CONCURRENCY", "20")))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Railway Variables içinde tanımlı değil.")

if TARGET_GROUP.startswith("@"):
    TARGET_GROUP = TARGET_GROUP[1:]

# Aynı anda sınırlı sayıda Telegram API isteği gönderir.
semaphore = asyncio.Semaphore(APPROVAL_CONCURRENCY)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("join-approver")


async def approve_join_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    request = update.chat_join_request

    if not request:
        return

    chat = request.chat
    user = request.from_user

    # Sadece hedef gruptaki istekleri işle.
    username = (chat.username or "").lower()
    target = TARGET_GROUP.lower().lstrip("@")

    if str(chat.id) != TARGET_GROUP and username != target:
        return

    async with semaphore:
        while True:
            try:
                await context.bot.approve_chat_join_request(
                    chat_id=chat.id,
                    user_id=user.id,
                )

                logger.info(
                    "ONAYLANDI | %s | user_id=%s",
                    user.full_name,
                    user.id,
                )
                return

            except RetryAfter as e:
                # Telegram hız sınırı koyarsa bekleyip otomatik devam eder.
                wait = max(0.1, float(e.retry_after))
                logger.warning("Rate limit: %.2fs bekleniyor.", wait)
                await asyncio.sleep(wait)

            except TelegramError as e:
                # İstek zaten işlenmişse veya başka Telegram hatası varsa
                # tüm botu durdurma.
                logger.warning(
                    "Onaylanamadı | user_id=%s | %s",
                    user.id,
                    e,
                )
                return

            except Exception:
                logger.exception(
                    "Beklenmeyen hata | user_id=%s",
                    user.id,
                )
                return


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    # Sadece chat_join_request güncellemelerini dinle.
    app.add_handler(ChatJoinRequestHandler(approve_join_request))

    logger.info(
        "Katılma isteği onay botu başladı | hedef=%s | concurrency=%s",
        TARGET_GROUP,
        APPROVAL_CONCURRENCY,
    )

    # Bekleyen update'leri silme.
    # Sadece katılma isteği update'lerini al.
    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=["chat_join_request"],
        close_loop=False,
    )


if __name__ == "__main__":
    main()
