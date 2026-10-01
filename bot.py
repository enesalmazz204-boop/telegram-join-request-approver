import os
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ChatJoinRequestHandler,
    ContextTypes,
)

# =========================================================
# AYARLAR
# =========================================================

BOT_TOKEN = '8206822443:AAGAYgUT5FJ85nPam-Mxoz6D1cBsFAkmyBY'

# Sadece bu Telegram ID komutları kullanabilir.
# Railway Variables kısmına ADMIN_ID ekleyebilirsin.
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# İsteklerin onaylanacağı grup
GROUP_USERNAME = "@novaprimesohbet"


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("nova_join_bot")


# =========================================================
# ADMIN KONTROLÜ
# =========================================================

def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


# =========================================================
# /start
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user is None:
        return

    if not is_admin(update.effective_user.id):
        return

    await update.message.reply_text(
        "🤖 <b>NOVA PRIME Katılma İsteği Botu</b>\n\n"
        "Bot aktif ve yeni katılma isteklerini otomatik onaylıyor.\n\n"
        "Komutlar:\n"
        "• /durum - Bot durumunu gösterir\n"
        "• /yardim - Yardım menüsü",
        parse_mode="HTML",
    )


# =========================================================
# /yardim
# =========================================================

async def yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user is None:
        return

    if not is_admin(update.effective_user.id):
        return

    await update.message.reply_text(
        "🤖 <b>NOVA PRIME BOT</b>\n\n"
        "/start - Bot menüsü\n"
        "/durum - Bot durumunu gösterir\n"
        "/yardim - Yardım\n\n"
        "📌 Grup:\n"
        f"{GROUP_USERNAME}\n\n"
        "✅ Yeni katılma istekleri otomatik kabul edilir.",
        parse_mode="HTML",
    )


# =========================================================
# /durum
# =========================================================

async def durum(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user is None:
        return

    if not is_admin(update.effective_user.id):
        return

    try:

        chat = await context.bot.get_chat(GROUP_USERNAME)

        await update.message.reply_text(
            "🟢 <b>BOT AKTİF</b>\n\n"
            f"📌 Grup: {chat.title}\n"
            f"🆔 Grup ID: <code>{chat.id}</code>\n\n"
            "✅ Yeni katılma istekleri otomatik onaylanıyor.",
            parse_mode="HTML",
        )

    except Exception as e:

        logger.error("Grup kontrol hatası: %s", e)

        await update.message.reply_text(
            "🔴 Grup kontrol edilemedi.\n\n"
            "Botun grupta yönetici olduğundan emin ol."
        )


# =========================================================
# YENİ KATILMA İSTEĞİ
# =========================================================

async def join_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    request = update.chat_join_request

    if request is None:
        return

    try:

        # Sadece hedef grubun isteklerini işle
        if request.chat.username:
            username = request.chat.username.lower()

            if username != "novaprimesohbet":
                logger.info(
                    "Başka bir gruptan istek geldi: @%s",
                    username
                )
                return

        logger.info(
            "Yeni katılma isteği: user_id=%s username=%s",
            request.from_user.id,
            request.from_user.username,
        )

        # Katılma isteğini kabul et
        await context.bot.approve_chat_join_request(
            chat_id=request.chat.id,
            user_id=request.from_user.id,
        )

        logger.info(
            "İstek başarıyla onaylandı: user_id=%s",
            request.from_user.id,
        )

    except Exception as e:

        logger.exception(
            "Katılma isteği onaylanamadı: %s",
            e
        )


# =========================================================
# HATA YAKALAMA
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Telegram hatası:",
        exc_info=context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info("=" * 60)
    logger.info("NOVA PRIME KATILMA İSTEĞİ BOTU BAŞLIYOR")
    logger.info("GRUP: %s", GROUP_USERNAME)
    logger.info("=" * 60)

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN bulunamadı. "
            "Railway Variables bölümüne BOT_TOKEN ekle."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Komutlar
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("yardim", yardim)
    )

    application.add_handler(
        CommandHandler("durum", durum)
    )

    # Katılma istekleri
    application.add_handler(
        ChatJoinRequestHandler(join_request)
    )

    # Hatalar
    application.add_error_handler(error_handler)

    logger.info("BOT AKTİF.")
    logger.info(
        "Yeni katılma istekleri otomatik onaylanacak."
    )

    application.run_polling(
        allowed_updates=[
            "message",
            "chat_join_request",
        ],
        drop_pending_updates=False,
    )


# =========================================================
# ÇALIŞTIR
# =========================================================

if __name__ == "__main__":
    main()
