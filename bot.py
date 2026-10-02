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

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import HideAllChatJoinRequestsRequest


# ============================================================
# RAILWAY VARIABLES
# ============================================================

BOT_TOKEN = '8206822443:AAE3zOkzOpU3pD6KpI_0EnjY4B73wSZDsmc'

TARGET_GROUP = os.getenv(
    "TARGET_GROUP",
    "novaprimesohbet"
).strip().lstrip("@")

APPROVAL_CONCURRENCY = max(
    1,
    int(os.getenv("APPROVAL_CONCURRENCY", "20"))
)

API_ID = os.getenv("API_ID", "").strip()
API_HASH = os.getenv("API_HASH", "").strip()
SESSION_STRING = os.getenv("SESSION_STRING", "").strip()


# İstersen belirli Telegram kullanıcılarına komut yetkisi ver.
# Örnek:
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
        "BOT_TOKEN bulunamadı. Railway Variables bölümüne ekle."
    )

if not TARGET_GROUP:
    raise RuntimeError(
        "TARGET_GROUP bulunamadı."
    )

if not API_ID or not API_ID.isdigit():
    raise RuntimeError(
        "API_ID bulunamadı veya geçersiz."
    )

if not API_HASH:
    raise RuntimeError(
        "API_HASH bulunamadı."
    )

if not SESSION_STRING:
    raise RuntimeError(
        "SESSION_STRING bulunamadı."
    )

API_ID = int(API_ID)


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("telegram-join-approver")


# ============================================================
# DURUM
# ============================================================

# /onayla yazıldığında True olur.
# Program yeniden başlarsa tekrar /onayla gerekir.
approval_enabled = False

semaphore = asyncio.Semaphore(
    APPROVAL_CONCURRENCY
)


# ============================================================
# TELEGRAM USER CLIENT
# ============================================================

user_client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH,
    request_retries=5,
    connection_retries=5,
    retry_delay=2,
    auto_reconnect=True,
)


# ============================================================
# HEDEF GRUP KONTROLÜ
# ============================================================

def is_target_chat(chat) -> bool:

    if not chat:
        return False

    username = (
        chat.username or ""
    ).lower()

    target = TARGET_GROUP.lower()

    return (
        username == target
        or str(chat.id) == TARGET_GROUP
    )


# ============================================================
# ADMIN KONTROLÜ
# ============================================================

async def is_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:

    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return False

    # ADMIN_IDS tanımlıysa sadece onlar kullanabilir.
    if ADMIN_IDS:
        return user.id in ADMIN_IDS

    try:

        member = await context.bot.get_chat_member(
            chat_id=chat.id,
            user_id=user.id,
        )

        return member.status in (
            "administrator",
            "creator",
        )

    except TelegramError as e:

        logger.warning(
            "Admin kontrolü başarısız: %s",
            e,
        )

        return False


# ============================================================
# MEVCUT TÜM BEKLEYENLERİ ONAYLA
# ============================================================

async def approve_all_pending_requests():

    if not user_client.is_connected():
        await user_client.connect()

    if not await user_client.is_user_authorized():
        raise RuntimeError(
            "SESSION_STRING yetkili değil."
        )

    logger.info(
        "Hedef grup çözülüyor: @%s",
        TARGET_GROUP,
    )

    entity = await user_client.get_entity(
        TARGET_GROUP
    )

    logger.info(
        "TOPLU ONAY BAŞLADI | hedef=@%s",
        TARGET_GROUP,
    )

    # Telegram'ın resmi MTProto metodudur.
    #
    # approved=True:
    # mevcut bekleyen tüm join request'leri onaylar.
    await user_client(
        HideAllChatJoinRequestsRequest(
            peer=entity,
            approved=True,
        )
    )

    logger.info(
        "TOPLU ONAY TAMAMLANDI | hedef=@%s",
        TARGET_GROUP,
    )


# ============================================================
# YENİ GELEN İSTEĞİ OTOMATİK ONAYLA
# ============================================================

async def approve_join_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global approval_enabled

    request = update.chat_join_request

    if not request:
        return

    chat = request.chat
    user = request.from_user

    if not is_target_chat(chat):
        return

    # /onayla henüz çalıştırılmadıysa beklet.
    if not approval_enabled:

        logger.info(
            "İSTEK BEKLEMEDE | otomatik onay kapalı | user_id=%s",
            user.id,
        )

        return

    async with semaphore:

        while True:

            try:

                await context.bot.approve_chat_join_request(
                    chat_id=chat.id,
                    user_id=user.id,
                )

                logger.info(
                    "YENİ İSTEK ONAYLANDI ✅ | %s | user_id=%s",
                    user.full_name,
                    user.id,
                )

                return

            except RetryAfter as e:

                wait = max(
                    0.1,
                    float(e.retry_after),
                )

                logger.warning(
                    "Telegram rate limit | %.2f saniye bekleniyor",
                    wait,
                )

                await asyncio.sleep(wait)

            except TelegramError as e:

                logger.error(
                    "İstek onaylanamadı | user_id=%s | %s",
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


# ============================================================
# /ONAYLA
# ============================================================

async def cmd_onayla(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global approval_enabled

    chat = update.effective_chat

    if not chat:
        return

    if not is_target_chat(chat):
        return

    if not await is_admin(
        update,
        context,
    ):

        await update.effective_message.reply_text(
            "❌ Bu komutu sadece grup yöneticileri kullanabilir."
        )

        return

    await update.effective_message.reply_text(
        "⏳ Bekleyen katılma istekleri topluca onaylanıyor..."
    )

    try:

        await approve_all_pending_requests()

    except Exception as e:

        logger.exception(
            "TOPLU ONAY HATASI"
        )

        await update.effective_message.reply_text(
            "❌ Toplu onay sırasında hata oluştu.\n\n"
            f"{type(e).__name__}: {e}\n\n"
            "SESSION_STRING hesabının grupta yönetici "
            "olduğunu ve üyelik isteklerini yönetme yetkisi "
            "bulunduğunu kontrol et."
        )

        return

    # Sadece toplu işlem başarılı olursa otomatik onayı aç.
    approval_enabled = True

    logger.info(
        "OTOMATİK ONAY AKTİF ✅ | /onayla kullanan=%s",
        update.effective_user.id,
    )

    await update.effective_message.reply_text(
        "✅ Bekleyen katılma istekleri topluca onaylandı.\n\n"
        "🟢 Otomatik onay AKTİF.\n"
        "Yeni gelen istekler otomatik olarak onaylanacak."
    )


# ============================================================
# /DURDUR
# ============================================================

async def cmd_durdur(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global approval_enabled

    chat = update.effective_chat

    if not chat:
        return

    if not is_target_chat(chat):
        return

    if not await is_admin(
        update,
        context,
    ):

        await update.effective_message.reply_text(
            "❌ Bu komutu sadece grup yöneticileri kullanabilir."
        )

        return

    approval_enabled = False

    await update.effective_message.reply_text(
        "⛔ Otomatik onay DURDURULDU.\n\n"
        "Tekrar açmak için /onayla yaz."
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
    context: ContextTypes.DEFAULT_TYPE,
):

    chat = update.effective_chat

    if not chat:
        return

    if not is_target_chat(chat):
        return

    if not await is_admin(
        update,
        context,
    ):

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
        f"⚡ Eşzamanlı yeni istek: {APPROVAL_CONCURRENCY}\n"
        f"👥 Hedef: @{TARGET_GROUP}"
    )


# ============================================================
# BAŞLANGIÇ
# ============================================================

async def post_init(
    application: Application,
):

    await application.bot.set_my_commands([
        BotCommand(
            "onayla",
            "Bekleyenleri onayla + otomatik onayı aç",
        ),
        BotCommand(
            "durdur",
            "Otomatik onayı durdur",
        ),
        BotCommand(
            "durum",
            "Sistemin durumunu göster",
        ),
    ])

    # Webhook varsa kaldır.
    await application.bot.delete_webhook(
        drop_pending_updates=False
    )

    # Normal Telegram hesabını bağla.
    await user_client.connect()

    if not await user_client.is_user_authorized():

        raise RuntimeError(
            "SESSION_STRING geçersiz veya yetkili değil."
        )

    me = await user_client.get_me()

    logger.info(
        "MTProto hesap bağlandı | user_id=%s | username=@%s",
        me.id,
        me.username or "-",
    )


# ============================================================
# KAPANIŞ
# ============================================================

async def post_shutdown(
    application: Application,
):

    if user_client.is_connected():

        await user_client.disconnect()

        logger.info(
            "MTProto bağlantısı kapatıldı."
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
        .post_shutdown(post_shutdown)
        .build()
    )

    # Komutlar
    app.add_handler(
        CommandHandler(
            "onayla",
            cmd_onayla,
        )
    )

    app.add_handler(
        CommandHandler(
            "durdur",
            cmd_durdur,
        )
    )

    app.add_handler(
        CommandHandler(
            "durum",
            cmd_durum,
        )
    )

    # Yeni katılma istekleri
    app.add_handler(
        ChatJoinRequestHandler(
            approve_join_request
        )
    )

    logger.info(
        "BOT BAŞLADI | hedef=@%s",
        TARGET_GROUP,
    )

    # TEK polling instance çalıştır.
    app.run_polling(
        allowed_updates=[
            "message",
            "chat_join_request",
        ],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
