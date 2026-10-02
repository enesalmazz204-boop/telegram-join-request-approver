import os
import asyncio
import logging

from telegram import Update, BotCommand
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ChatJoinRequestHandler,
    CommandHandler,
    ContextTypes,
)

# =========================
# RAILWAY VARIABLES
# =========================
# Railway Variables'tan token al.
# BOT_TOKEN önerilen isimdir; TELEGRAM_BOT_TOKEN da desteklenir.
BOT_TOKEN = '8206822443:AAG1wWM5woL05ruRNmdoJcKN3vcZNMzSTfc'
    

TARGET_GROUP = os.getenv("TARGET_GROUP", "novaprimesohbet").strip()
APPROVAL_CONCURRENCY = max(1, int(os.getenv("APPROVAL_CONCURRENCY", "20")))

# İstersen Railway'e ADMIN_IDS ekleyebilirsin:
# ADMIN_IDS=123456789,987654321
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError(
        "Telegram bot tokeni bulunamadı. Railway > Variables bölümüne "
        "BOT_TOKEN = BotFather tokeni ekle ve yeniden deploy et."
    )

TARGET_GROUP = TARGET_GROUP.lstrip("@")

if not TARGET_GROUP:
    raise RuntimeError(
        "TARGET_GROUP boş. Railway > Variables bölümüne "
        "TARGET_GROUP=novaprimesohbet ekle."
    )

semaphore = asyncio.Semaphore(APPROVAL_CONCURRENCY)

# Başlangıçta otomatik onay AÇIK.
approval_enabled = True

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("join-approver")


def is_target_chat(chat) -> bool:
    username = (chat.username or "").lower()
    target = TARGET_GROUP.lower()

    return (
        str(chat.id) == TARGET_GROUP
        or username == target
    )


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    chat = update.effective_chat

    if not user:
        return False

    # ADMIN_IDS verilmişse yalnızca bu kullanıcılar komut kullanabilir.
    if ADMIN_IDS:
        return user.id in ADMIN_IDS

    # ADMIN_IDS yoksa Telegram'daki grup yöneticisini kontrol et.
    if not chat:
        return False

    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        return False


async def cmd_onayla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global approval_enabled

    if not await is_admin(update, context):
        return

    approval_enabled = True

    await update.effective_message.reply_text(
        "✅ Katılma isteği otomatik onayı AKTİF.\n"
        "Yeni gelen istekler otomatik olarak onaylanacak."
    )

    logger.info(
        "ONAY AKTİF | komutu kullanan user_id=%s",
        update.effective_user.id,
    )


async def cmd_durdur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global approval_enabled

    if not await is_admin(update, context):
        return

    approval_enabled = False

    await update.effective_message.reply_text(
        "⛔ Katılma isteği otomatik onayı DURDURULDU."
    )

    logger.info(
        "ONAY DURDURULDU | komutu kullanan user_id=%s",
        update.effective_user.id,
    )


async def cmd_durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return

    durum = "AKTİF ✅" if approval_enabled else "DURDURULDU ⛔"

    await update.effective_message.reply_text(
        f"📊 Katılma İsteği Sistemi\n\n"
        f"Durum: {durum}\n"
        f"⚡ Eşzamanlı onay: {APPROVAL_CONCURRENCY}\n"
        f"👥 Hedef: @{TARGET_GROUP}"
    )


async def approve_join_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    global approval_enabled

    request = update.chat_join_request

    if not request or not approval_enabled:
        return

    chat = request.chat
    user = request.from_user

    if not is_target_chat(chat):
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
                wait = max(0.1, float(e.retry_after))
                logger.warning(
                    "Telegram rate limit: %.2fs bekleniyor.",
                    wait,
                )
                await asyncio.sleep(wait)

            except TelegramError as e:
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


async def post_init(application: Application) -> None:
    # Telegram'daki komut menüsünü ayarla.
    await application.bot.set_my_commands([
        BotCommand("onayla", "Katılma isteklerini otomatik onayla"),
        BotCommand("durdur", "Otomatik onayı durdur"),
        BotCommand("durum", "Onay sisteminin durumunu göster"),
    ])

    # Önceden kalmış webhook varsa polling'in önüne geçmesin.
    await application.bot.delete_webhook(drop_pending_updates=False)


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Komutlar
    app.add_handler(CommandHandler("onayla", cmd_onayla))
    app.add_handler(CommandHandler("durdur", cmd_durdur))
    app.add_handler(CommandHandler("durum", cmd_durum))

    # Katılma istekleri
    app.add_handler(
        ChatJoinRequestHandler(approve_join_request)
    )

    logger.info(
        "BOT BAŞLADI | hedef=@%s | concurrency=%s | otomatik_onay=%s",
        TARGET_GROUP,
        APPROVAL_CONCURRENCY,
        approval_enabled,
    )

    # Sadece mesaj ve chat_join_request update'lerini al.
    # Pending update'leri silme.
    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=["message", "chat_join_request"],
        close_loop=False,
    )


if __name__ == "__main__":
    main()
