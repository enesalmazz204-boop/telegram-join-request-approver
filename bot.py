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


# =========================================================
# AYARLAR
# =========================================================

BOT_TOKEN = '8206822443:AAE3zOkzOpU3pD6KpI_0EnjY4B73wSZDsmc'

TARGET_GROUP = os.getenv(
    "TARGET_GROUP",
    "novaprimesohbet"
).strip().lstrip("@")

# Aynı anda kaç onay isteği gönderilecek
CONCURRENCY = int(
    os.getenv("APPROVAL_CONCURRENCY", "20")
)

# /onayla sonrası otomatik onay
approval_enabled = False

# Bekleyen request'leri burada tutuyoruz.
pending_requests = {}

# Aynı anda çalışan onaylar
semaphore = asyncio.Semaphore(CONCURRENCY)


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("join-bot")


# =========================================================
# KONTROLLER
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN Railway Variables içinde bulunamadı."
    )

if not TARGET_GROUP:
    raise RuntimeError(
        "TARGET_GROUP bulunamadı."
    )


# =========================================================
# GRUP KONTROLÜ
# =========================================================

def is_target_chat(chat) -> bool:

    if not chat:
        return False

    username = (
        chat.username or ""
    ).lower()

    return username == TARGET_GROUP.lower()


# =========================================================
# ADMIN KONTROLÜ
# =========================================================

async def is_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:

    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return False

    try:

        member = await context.bot.get_chat_member(
            chat_id=chat.id,
            user_id=user.id,
        )

        return member.status in (
            "administrator",
            "creator",
        )

    except TelegramError:

        return False


# =========================================================
# TEK İSTEĞİ ONAYLA
# =========================================================

async def approve_one(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
):

    async with semaphore:

        try:

            await context.bot.approve_chat_join_request(
                chat_id=chat_id,
                user_id=user_id,
            )

            logger.info(
                "ONAYLANDI | user_id=%s",
                user_id,
            )

            return True

        except RetryAfter as e:

            wait = float(e.retry_after)

            logger.warning(
                "RATE LIMIT | %.2f saniye bekleniyor",
                wait,
            )

            await asyncio.sleep(wait)

            try:

                await context.bot.approve_chat_join_request(
                    chat_id=chat_id,
                    user_id=user_id,
                )

                logger.info(
                    "ONAYLANDI | user_id=%s",
                    user_id,
                )

                return True

            except TelegramError as err:

                logger.error(
                    "Tekrar denemede hata | %s",
                    err,
                )

                return False

        except TelegramError as e:

            logger.warning(
                "ONAYLANAMADI | user_id=%s | %s",
                user_id,
                e,
            )

            return False


# =========================================================
# YENİ KATILMA İSTEĞİ
# =========================================================

async def join_request(
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

    # İsteği hafızaya al
    pending_requests[user.id] = {
        "chat_id": chat.id,
        "user_id": user.id,
    }

    logger.info(
        "YENİ İSTEK | user_id=%s | toplam_bekleyen=%s",
        user.id,
        len(pending_requests),
    )

    # Otomatik onay açıksa hemen onayla
    if approval_enabled:

        success = await approve_one(
            context,
            chat.id,
            user.id,
        )

        if success:
            pending_requests.pop(
                user.id,
                None,
            )


# =========================================================
# /ONAYLA
# =========================================================

async def onayla(
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
            "❌ Bu komutu sadece grup yöneticisi kullanabilir."
        )

        return

    if not pending_requests:

        approval_enabled = True

        await update.effective_message.reply_text(
            "🟢 Otomatik onay AKTİF.\n\n"
            "Şu anda botun hafızasında bekleyen istek yok.\n"
            "Yeni gelenler otomatik onaylanacak."
        )

        return

    requests = list(
        pending_requests.values()
    )

    total = len(requests)

    await update.effective_message.reply_text(
        f"⚡ {total:,} bekleyen istek "
        f"hızlı şekilde onaylanıyor..."
    )

    logger.info(
        "TOPLU ONAY BAŞLADI | adet=%s",
        total,
    )

    # Paralel onay
    tasks = []

    for item in requests:

        tasks.append(
            approve_one(
                context,
                item["chat_id"],
                item["user_id"],
            )
        )

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )

    success_count = 0

    for item, result in zip(
        requests,
        results,
    ):

        if result is True:

            success_count += 1

            pending_requests.pop(
                item["user_id"],
                None,
            )

    # Otomatik onayı açık bırak
    approval_enabled = True

    failed = total - success_count

    logger.info(
        "TOPLU ONAY BİTTİ | başarılı=%s | başarısız=%s",
        success_count,
        failed,
    )

    await update.effective_message.reply_text(
        "✅ ONAYLAMA TAMAMLANDI\n\n"
        f"👥 Toplam: {total:,}\n"
        f"✅ Onaylanan: {success_count:,}\n"
        f"❌ Onaylanamayan: {failed:,}\n\n"
        "🟢 Otomatik onay AKTİF.\n"
        "Yeni gelen üyeler otomatik onaylanacak."
    )


# =========================================================
# /DURDUR
# =========================================================

async def durdur(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global approval_enabled

    if not await is_admin(
        update,
        context,
    ):

        return

    approval_enabled = False

    await update.effective_message.reply_text(
        "⛔ Otomatik onay durduruldu.\n\n"
        "Tekrar başlatmak için /onayla yaz."
    )


# =========================================================
# /DURUM
# =========================================================

async def durum(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await is_admin(
        update,
        context,
    ):

        return

    state = (
        "AKTİF 🟢"
        if approval_enabled
        else "KAPALI 🔴"
    )

    await update.effective_message.reply_text(
        "📊 SİSTEM DURUMU\n\n"
        f"Durum: {state}\n"
        f"Bekleyen hafıza: {len(pending_requests):,}\n"
        f"Paralel onay: {CONCURRENCY}\n"
        f"Hedef: @{TARGET_GROUP}"
    )


# =========================================================
# BAŞLANGIÇ
# =========================================================

async def post_init(
    application: Application,
):

    await application.bot.set_my_commands([
        BotCommand(
            "onayla",
            "Bekleyenleri onayla ve otomatik aç",
        ),
        BotCommand(
            "durdur",
            "Otomatik onayı durdur",
        ),
        BotCommand(
            "durum",
            "Sistemi göster",
        ),
    ])

    await application.bot.delete_webhook(
        drop_pending_updates=False
    )

    logger.info(
        "BOT HAZIR | hedef=@%s | concurrency=%s",
        TARGET_GROUP,
        CONCURRENCY,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "onayla",
            onayla,
        )
    )

    app.add_handler(
        CommandHandler(
            "durdur",
            durdur,
        )
    )

    app.add_handler(
        CommandHandler(
            "durum",
            durum,
        )
    )

    app.add_handler(
        ChatJoinRequestHandler(
            join_request
        )
    )

    logger.info(
        "BOT BAŞLADI | hedef=@%s",
        TARGET_GROUP,
    )

    app.run_polling(
        allowed_updates=[
            "message",
            "chat_join_request",
        ],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
