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

# Railway > Variables
# BOT_TOKEN = yeni BotFather tokenın
BOT_TOKEN = '8206822443:AAE3zOkzOpU3pD6KpI_0EnjY4B73wSZDsmc'

# Railway > Variables
# TARGET_GROUP=novaprimesohbet
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

# Bot açıldığında otomatik onay KAPALI.
# /onayla ile açılır.
approval_enabled = False

# Bot kapalıyken gelen / bekleyen istekleri burada tutacağız.
#
# Key:
#     (chat_id, user_id)
#
# Value:
#     ChatJoinRequest objesi
pending_requests = {}

# Aynı anda yapılan onayları sınırla.
semaphore = asyncio.Semaphore(APPROVAL_CONCURRENCY)

# Kuyruğa erişimi güvenli hale getir.
pending_lock = asyncio.Lock()

# ============================================================
# HEDEF GRUP
# ============================================================

def is_target_chat(chat) -> bool:
    if not chat:
        return False

    username = (chat.username or "").lower()
    target = TARGET_GROUP.lower()

    # @novaprimesohbet
    if username == target:
        return True

    # TARGET_GROUP chat ID olarak verilirse
    if str(chat.id) == TARGET_GROUP:
        return True

    return False


# ============================================================
# ADMİN KONTROLÜ
# ============================================================

async def is_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:

    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return False

    # ADMIN_IDS tanımlandıysa yalnızca bunlar yetkili.
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
            "Admin kontrolü başarısız | user_id=%s | %s",
            user.id,
            e,
        )
        return False


# ============================================================
# TEK BİR İSTEĞİ ONAYLA
# ============================================================

async def approve_request(
    request,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:

    if not request:
        return False

    chat = request.chat
    user = request.from_user

    if not is_target_chat(chat):
        return False

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

                return True

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
                    "ONAYLANAMADI ❌ | %s | user_id=%s | %s",
                    user.full_name,
                    user.id,
                    e,
                )

                return False

            except Exception:

                logger.exception(
                    "BEKLENMEYEN HATA | user_id=%s",
                    user.id,
                )

                return False


# ============================================================
# BEKLEYEN KUYRUĞU TOPLU ONAYLA
# ============================================================

async def approve_all_pending(
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[int, int]:

    async with pending_lock:

        if not pending_requests:
            return 0, 0

        requests = list(
            pending_requests.values()
        )

        # Kuyruğu temizliyoruz.
        pending_requests.clear()

    logger.info(
        "TOPLU ONAY BAŞLADI | bekleyen=%s",
        len(requests),
    )

    # Aynı anda onayla.
    results = await asyncio.gather(
        *[
            approve_request(
                request,
                context,
            )
            for request in requests
        ],
        return_exceptions=True,
    )

    success = 0
    failed = 0

    for result in results:

        if result is True:
            success += 1
        else:
            failed += 1

    logger.info(
        "TOPLU ONAY BİTTİ | başarılı=%s | başarısız=%s",
        success,
        failed,
    )

    return success, failed


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

    # Sadece hedef grupta çalışsın.
    if not is_target_chat(chat):
        return

    # Sadece admin kullanabilsin.
    if not await is_admin(update, context):

        await update.effective_message.reply_text(
            "❌ Bu komutu sadece grup yöneticileri kullanabilir."
        )

        return

    # Önce aktif hale getir.
    # Böylece bundan sonra gelenler de otomatik onaylanır.
    approval_enabled = True

    logger.info(
        "OTOMATİK ONAY AKTİF | user_id=%s",
        update.effective_user.id,
    )

    # Şimdi mevcut kuyruktaki istekleri topluca onayla.
    success, failed = await approve_all_pending(
        context
    )

    # Sonuç mesajı.
    if success == 0 and failed == 0:

        text = (
            "✅ Otomatik onay AKTİF.\n\n"
            "📭 Bekleyen kuyrukta onaylanacak istek bulunamadı.\n\n"
            "Yeni gelen katılma istekleri otomatik olarak "
            "onaylanacak."
        )

    else:

        text = (
            "✅ Otomatik onay AKTİF.\n\n"
            f"👥 Bekleyen istekler:\n"
            f"✅ Onaylanan: {success}\n"
            f"❌ Onaylanamayan: {failed}\n\n"
            "Yeni gelen katılma istekleri otomatik olarak "
            "onaylanacak."
        )

    await update.effective_message.reply_text(
        text
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

    if not await is_admin(update, context):

        await update.effective_message.reply_text(
            "❌ Bu komutu sadece grup yöneticileri kullanabilir."
        )

        return

    approval_enabled = False

    await update.effective_message.reply_text(
        "⛔ Otomatik onay DURDURULDU.\n\n"
        "Yeni katılma istekleri beklemeye alınacak.\n"
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

    if not await is_admin(update, context):

        await update.effective_message.reply_text(
            "❌ Bu komutu sadece grup yöneticileri kullanabilir."
        )

        return

    async with pending_lock:
        queue_count = len(pending_requests)

    durum = (
        "AKTİF ✅"
        if approval_enabled
        else "KAPALI ⛔"
    )

    await update.effective_message.reply_text(
        "📊 Katılma İsteği Sistemi\n\n"
        f"Durum: {durum}\n"
        f"⏳ Kuyrukta: {queue_count}\n"
        f"⚡ Eşzamanlı onay: {APPROVAL_CONCURRENCY}\n"
        f"👥 Hedef: @{TARGET_GROUP}"
    )


# ============================================================
# KATILMA İSTEĞİ GELDİ
# ============================================================

async def approve_join_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    request = update.chat_join_request

    if not request:
        return

    chat = request.chat
    user = request.from_user

    # Başka grupsa görmezden gel.
    if not is_target_chat(chat):
        return

    key = (
        chat.id,
        user.id,
    )

    # ========================================================
    # OTOMATİK ONAY AÇIKSA
    # ========================================================

    if approval_enabled:

        logger.info(
            "YENİ İSTEK | otomatik onay açık | %s | user_id=%s",
            user.full_name,
            user.id,
        )

        await approve_request(
            request,
            context,
        )

        return

    # ========================================================
    # OTOMATİK ONAY KAPALIYSA
    # KUYRUĞA AL
    # ========================================================

    async with pending_lock:

        pending_requests[key] = request

        queue_count = len(
            pending_requests
        )

    logger.info(
        "İSTEK KUYRUĞA ALINDI ⏳ | %s | user_id=%s | kuyruk=%s",
        user.full_name,
        user.id,
        queue_count,
    )


# ============================================================
# BOT BAŞLANGICI
# ============================================================

async def post_init(
    application: Application,
):

    # Komut menüsü.
    await application.bot.set_my_commands([
        BotCommand(
            "onayla",
            "Bekleyenleri onayla ve otomatik onayı aç",
        ),
        BotCommand(
            "durdur",
            "Otomatik onayı durdur",
        ),
        BotCommand(
            "durum",
            "Onay sisteminin durumunu göster",
        ),
    ])

    # Webhook varsa kaldır.
    # Pending update'leri SİLME.
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

    # --------------------------------------------------------
    # KATILMA İSTEKLERİ
    # --------------------------------------------------------

    app.add_handler(
        ChatJoinRequestHandler(
            approve_join_request,
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
