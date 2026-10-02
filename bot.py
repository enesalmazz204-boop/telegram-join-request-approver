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

# ============================================================
# AYARLAR
# ============================================================

# Railway > Variables:
# BOT_TOKEN = BotFather tokenın
BOT_TOKEN = '8206822443:AAE3zOkzOpU3pD6KpI_0EnjY4B73wSZDsmc'

# Railway > Variables:
# TARGET_GROUP = novaprimesohbet
TARGET_GROUP = os.getenv(
    "TARGET_GROUP",
    "novaprimesohbet"
).strip().lstrip("@")

# Aynı anda kaç istek onaylanabilir?
APPROVAL_CONCURRENCY = max(
    1,
    int(os.getenv("APPROVAL_CONCURRENCY", "20"))
)

# İsteğe bağlı:
# ADMIN_IDS=123456789,987654321
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

# ============================================================
# KONTROLLER
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN bulunamadı. "
        "Railway > Variables bölümüne BOT_TOKEN ekle."
    )

if not TARGET_GROUP:
    raise RuntimeError(
        "TARGET_GROUP boş. "
        "Railway > Variables bölümüne TARGET_GROUP=novaprimesohbet ekle."
    )

# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("join-approver")

# ============================================================
# DURUM
# ============================================================

# ÖNEMLİ:
# Bot ilk açıldığında otomatik onay KAPALI.
# /onayla yazınca aktif olur.
approval_enabled = False

semaphore = asyncio.Semaphore(APPROVAL_CONCURRENCY)

# ============================================================
# HEDEF GRUP KONTROLÜ
# ============================================================

def is_target_chat(chat) -> bool:
    if not chat:
        return False

    username = (chat.username or "").lower()
    target = TARGET_GROUP.lower()

    # @novaprimesohbet
    if username == target:
        return True

    # Eğer TARGET_GROUP Telegram chat ID olarak verilirse
    if str(chat.id) == TARGET_GROUP:
        return True

    return False


# ============================================================
# ADMİN KONTROLÜ
# ============================================================

async def is_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> bool:

    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return False

    # ADMIN_IDS tanımlandıysa sadece bu kişiler yetkili.
    if ADMIN_IDS:
        return user.id in ADMIN_IDS

    # ADMIN_IDS yoksa Telegram grup yöneticisini kontrol et.
    try:
        member = await context.bot.get_chat_member(
            chat_id=chat.id,
            user_id=user.id,
        )

        return member.status in ("administrator", "creator")

    except TelegramError as e:
        logger.warning(
            "Admin kontrolü başarısız | user_id=%s | %s",
            user.id,
            e,
        )
        return False


# ============================================================
# /ONAYLA
# ============================================================

async def cmd_onayla(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global approval_enabled

    if not update.effective_chat:
        return

    # Sadece hedef grupta çalışsın
    if not is_target_chat(update.effective_chat):
        return

    # Sadece admin çalıştırabilsin
    if not await is_admin(update, context):
        await update.effective_message.reply_text(
            "❌ Bu komutu sadece grup yöneticileri kullanabilir."
        )
        return

    approval_enabled = True

    await update.effective_message.reply_text(
        "✅ Otomatik onay AKTİF.\n\n"
        "Yeni katılma istekleri otomatik olarak onaylanacak."
    )

    logger.info(
        "OTOMATİK ONAY AKTİF | user_id=%s",
        update.effective_user.id,
    )


# ============================================================
# /DURDUR
# ============================================================

async def cmd_durdur(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global approval_enabled

    if not update.effective_chat:
        return

    if not is_target_chat(update.effective_chat):
        return

    if not await is_admin(update, context):
        await update.effective_message.reply_text(
            "❌ Bu komutu sadece grup yöneticileri kullanabilir."
        )
        return

    approval_enabled = False

    await update.effective_message.reply_text(
        "⛔ Otomatik onay DURDURULDU."
    )

    logger.info(
        "OTOMATİK ONAY DURDURULDU | user_id=%s",
        update.effective_user.id,
    )


# ============================================================
# /DURUM
# ============================================================

async def cmd_durum(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_chat:
        return

    if not is_target_chat(update.effective_chat):
        return

    if not await is_admin(update, context):
        await update.effective_message.reply_text(
            "❌ Bu komutu sadece grup yöneticileri kullanabilir."
        )
        return

    durum = (
        "AKTİF ✅"
        if approval_enabled
        else "KAPALI ⛔"
    )

    await update.effective_message.reply_text(
        "📊 Katılma İsteği Sistemi\n\n"
        f"Durum: {durum}\n"
        f"⚡ Eşzamanlı onay: {APPROVAL_CONCURRENCY}\n"
        f"👥 Hedef: @{TARGET_GROUP}"
    )


# ============================================================
# KATILMA İSTEĞİ
# ============================================================

async def approve_join_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global approval_enabled

    request = update.chat_join_request

    if not request:
        return

    # /onayla yazılmadıysa hiçbir şey yapma
    if not approval_enabled:
        logger.info(
            "İSTEK GELDİ fakat otomatik onay kapalı | user_id=%s",
            request.from_user.id,
        )
        return

    chat = request.chat
    user = request.from_user

    # Sadece hedef grup
    if not is_target_chat(chat):
        return

    logger.info(
        "KATILMA İSTEĞİ | grup=@%s | user=%s | user_id=%s",
        TARGET_GROUP,
        user.full_name,
        user.id,
    )

    async with semaphore:

        while True:

            try:

                await context.bot.approve_chat_join_request(
                    chat_id=chat.id,
                    user_id=user.id,
                )

                logger.info(
                    "ONAYLANDI ✅ | %s | user_id=%s",
                    user.full_name,
                    user.id,
                )

                return

            except RetryAfter as e:

                wait = max(
                    0.1,
                    float(e.retry_after)
                )

                logger.warning(
                    "Telegram rate limit | %.2f saniye bekleniyor",
                    wait,
                )

                await asyncio.sleep(wait)

            except TelegramError as e:

                logger.error(
                    "ONAYLANAMADI ❌ | user_id=%s | %s",
                    user.id,
                    e,
                )

                return

            except Exception:

                logger.exception(
                    "BEKLENMEYEN HATA | user_id=%s",
                    user.id,
                )

                return


# ============================================================
# BOT BAŞLANGICI
# ============================================================

async def post_init(application: Application):

    # Telegram komut menüsü
    await application.bot.set_my_commands([
        BotCommand(
            "onayla",
            "Otomatik katılma onayını aç"
        ),
        BotCommand(
            "durdur",
            "Otomatik katılma onayını kapat"
        ),
        BotCommand(
            "durum",
            "Otomatik onay durumunu göster"
        ),
    ])

    # Webhook varsa polling kullanabilmek için kaldır
    await application.bot.delete_webhook(
        drop_pending_updates=False
    )

    logger.info(
        "Telegram bağlantısı hazır"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "BOT BAŞLIYOR | hedef=@%s | concurrency=%s | otomatik_onay=%s",
        TARGET_GROUP,
        APPROVAL_CONCURRENCY,
        approval_enabled,
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # --------------------------------------------------------
    # KOMUTLAR
    # --------------------------------------------------------

    app.add_handler(
        CommandHandler(
            "onayla",
            cmd_onayla
        )
    )

    app.add_handler(
        CommandHandler(
            "durdur",
            cmd_durdur
        )
    )

    app.add_handler(
        CommandHandler(
            "durum",
            cmd_durum
        )
    )

    # --------------------------------------------------------
    # KATILMA İSTEKLERİ
    # --------------------------------------------------------

    app.add_handler(
        ChatJoinRequestHandler(
            approve_join_request
        )
    )

    logger.info(
        "BOT BAŞLADI | hedef=@%s | concurrency=%s | otomatik_onay=%s",
        TARGET_GROUP,
        APPROVAL_CONCURRENCY,
        approval_enabled,
    )

    # --------------------------------------------------------
    # TEK POLLING
    # --------------------------------------------------------

    app.run_polling(
        allowed_updates=[
            "message",
            "chat_join_request",
        ],
        drop_pending_updates=False,
    )


# ============================================================
# ÇALIŞTIR
# ============================================================

if __name__ == "__main__":
    main()
