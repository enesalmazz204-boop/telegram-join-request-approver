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

from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError


# =========================================================
# TELEGRAM API BİLGİLERİ
# =========================================================
#
# BURAYA KENDİ API ID VE API HASH'INI YAZ
#
# ÖRNEK:
# API_ID = 31895413
# API_HASH = "2d3a1e09a65f33ed4c54483dcb4a28bb"
#
# Bunları my.telegram.org/apps üzerinden alabilirsin.
#

API_ID = 31895413
API_HASH = "2d3a1e09a65f33ed4c54483dcb4a28bb"


# =========================================================
# BOT AYARLARI
# =========================================================

BOT_TOKEN = '8206822443:AAE3zOkzOpU3pD6KpI_0EnjY4B73wSZDsmc'

TARGET_GROUP = os.getenv(
    "TARGET_GROUP",
    "novaprimesohbet"
).strip().lstrip("@")

# User hesabıyla aynı anda kaç onay yapılacak.
# 10 güvenli bir başlangıç değeridir.
CONCURRENCY = max(
    1,
    int(os.getenv("APPROVAL_CONCURRENCY", "10"))
)

# =========================================================
# TELETHON SESSION
# =========================================================
#
# Railway Variables içine:
#
# SESSION_STRING=...
#
# koyacağız.
#
# Session String, API ID + API HASH ile giriş yapılmış
# normal Telegram hesabının oturumudur.
#

SESSION_STRING = 1BJWap1wBu63pJfv6cV_i8BlVZ3BpV5J1Yjl183tFwenBkDU7koOW9Mp8kbTDPMeE2woc_SWB6wShop5W9siIiv6FFKKJp8y1E_MKpJ9t0MoMHb-t0Q_sVPtxe0qjityOAg_lovAtH1jfA4gmumvuEGwcfLibjkFncp_t97_w0Jlvo81M1JJpP6drv8K1KdFQXowRD3z6iKEKc-jVRI6knIyT8hzBAMwj3mY-9LheQdt_FCKNlySsWheeM4DMiRQlu0LoenXo7U4aPqBcJRPUPYYY_R9-OgzwnD-oO_YWrz79b3nt8JDn4TZiDvZFxlT-XEeD4Ru8cxEO0-EBBy4Puli3GqS6nCc=


# =========================================================
# DURUM
# =========================================================

approval_enabled = False

bot_semaphore = asyncio.Semaphore(CONCURRENCY)

telethon_client = None

bulk_running = False


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("nova-join-approver")


# =========================================================
# KONTROLLER
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN Railway Variables içinde bulunamadı."
    )

if not API_ID or API_ID <= 0:
    raise RuntimeError(
        "API_ID geçersiz."
    )

if not API_HASH or API_HASH == "BURAYA_API_HASHINI_YAZ":
    raise RuntimeError(
        "API_HASH kodun içine girilmemiş."
    )

if not SESSION_STRING:
    raise RuntimeError(
        "SESSION_STRING Railway Variables içinde bulunamadı."
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
# BOT ADMIN KONTROLÜ
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

    except TelegramError as e:

        logger.warning(
            "Admin kontrolü başarısız: %s",
            e,
        )

        return False


# =========================================================
# BOT API İLE YENİ GELENİ ONAYLA
# =========================================================

async def approve_new_request(
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

    if not approval_enabled:
        logger.info(
            "Yeni istek bekletiliyor | user_id=%s",
            user.id,
        )
        return

    async with bot_semaphore:

        while True:

            try:

                await context.bot.approve_chat_join_request(
                    chat_id=chat.id,
                    user_id=user.id,
                )

                logger.info(
                    "YENİ İSTEK ONAYLANDI | user_id=%s",
                    user.id,
                )

                return

            except RetryAfter as e:

                wait = float(e.retry_after)

                logger.warning(
                    "BOT RATE LIMIT | %.2f saniye",
                    wait,
                )

                await asyncio.sleep(wait)

            except TelegramError as e:

                logger.error(
                    "Yeni istek onaylanamadı | user_id=%s | %s",
                    user.id,
                    e,
                )

                return


# =========================================================
# TELETHON CLIENT
# =========================================================

async def start_telethon():

    global telethon_client

    telethon_client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH,
    )

    await telethon_client.connect()

    if not await telethon_client.is_user_authorized():

        raise RuntimeError(
            "SESSION_STRING geçersiz veya Telegram hesabı "
            "oturum açmış değil."
        )

    me = await telethon_client.get_me()

    logger.info(
        "TELETHON HAZIR | user_id=%s | isim=%s",
        me.id,
        me.first_name,
    )


# =========================================================
# MEVCUT BEKLEYENLERİ BUL
# =========================================================

async def get_pending_requests():

    global telethon_client

    if telethon_client is None:
        raise RuntimeError(
            "Telethon client başlatılmamış."
        )

    entity = await telethon_client.get_entity(
        TARGET_GROUP
    )

    pending = []

    offset_date = 0
    offset_user = types.InputUserEmpty()

    while True:

        try:

            result = await telethon_client(
                functions.messages.GetChatInviteImportersRequest(
                    peer=entity,
                    requested=True,
                    offset_date=offset_date,
                    offset_user=offset_user,
                    limit=100,
                )
            )

        except FloodWaitError as e:

            logger.warning(
                "BEKLEYENLERİ ÇEKERKEN FLOOD WAIT | %s saniye",
                e.seconds,
            )

            await asyncio.sleep(e.seconds)

            continue

        if not result.importers:
            break

        # user_id listesini al
        for importer in result.importers:

            if getattr(importer, "requested", False):

                pending.append(
                    importer.user_id
                )

        logger.info(
            "BEKLEYENLER OKUNDU | bu_sayfa=%s | toplam=%s",
            len(result.importers),
            len(pending),
        )

        # Sayfalama için son kullanıcıyı bul
        last_importer = result.importers[-1]

        last_user = None

        for user in result.users:

            if user.id == last_importer.user_id:

                last_user = user
                break

        if last_user is None:

            break

        offset_date = last_importer.date

        offset_user = types.InputUser(
            user_id=last_user.id,
            access_hash=last_user.access_hash,
        )

        # Aynı kullanıcı tekrar gelirse dur.
        if len(pending) > 0:

            if len(result.importers) < 100:
                break

    # duplicate temizle
    pending = list(
        dict.fromkeys(pending)
    )

    return entity, pending


# =========================================================
# TEK KULLANICIYI ONAYLA
# =========================================================

async def approve_existing_user(
    entity,
    user_id,
):

    global telethon_client

    try:

        await telethon_client(
            functions.messages.HideChatJoinRequestRequest(
                peer=entity,
                user_id=user_id,
                approved=True,
            )
        )

        logger.info(
            "MEVCUT İSTEK ONAYLANDI | user_id=%s",
            user_id,
        )

        return True

    except FloodWaitError as e:

        logger.warning(
            "FLOOD WAIT | user_id=%s | %s saniye",
            user_id,
            e.seconds,
        )

        await asyncio.sleep(e.seconds)

        try:

            await telethon_client(
                functions.messages.HideChatJoinRequestRequest(
                    peer=entity,
                    user_id=user_id,
                    approved=True,
                )
            )

            logger.info(
                "TEKRAR DENEME BAŞARILI | user_id=%s",
                user_id,
            )

            return True

        except Exception as retry_error:

            logger.error(
                "TEKRAR DENEME BAŞARISIZ | user_id=%s | %s",
                user_id,
                retry_error,
            )

            return False

    except RPCError as e:

        logger.warning(
            "ONAYLANAMADI | user_id=%s | %s",
            user_id,
            e,
        )

        return False

    except Exception as e:

        logger.exception(
            "BEKLENMEYEN HATA | user_id=%s | %s",
            user_id,
            e,
        )

        return False


# =========================================================
# TOPLU ONAY
# =========================================================

async def process_existing_requests():

    global bulk_running

    if bulk_running:
        return {
            "success": 0,
            "failed": 0,
            "total": 0,
            "already_running": True,
        }

    bulk_running = True

    try:

        logger.info(
            "MEVCUT BEKLEYENLER ARANIYOR..."
        )

        entity, users = await get_pending_requests()

        total = len(users)

        logger.info(
            "MEVCUT BEKLEYEN İSTEK SAYISI: %s",
            total,
        )

        if total == 0:

            return {
                "success": 0,
                "failed": 0,
                "total": 0,
                "already_running": False,
            }

        success = 0
        failed = 0

        # Kontrollü paralel çalışma
        semaphore = asyncio.Semaphore(
            CONCURRENCY
        )

        async def worker(user_id):

            async with semaphore:

                return await approve_existing_user(
                    entity,
                    user_id,
                )

        # Hepsini görev olarak oluştur
        tasks = [
            asyncio.create_task(
                worker(user_id)
            )
            for user_id in users
        ]

        # Sonuçları parça parça topla
        for future in asyncio.as_completed(tasks):

            try:

                result = await future

                if result:
                    success += 1
                else:
                    failed += 1

            except Exception as e:

                failed += 1

                logger.error(
                    "Worker hatası: %s",
                    e,
                )

            processed = success + failed

            if (
                processed % 100 == 0
                or processed == total
            ):

                logger.info(
                    "İLERLEME | %s/%s | başarılı=%s | başarısız=%s",
                    processed,
                    total,
                    success,
                    failed,
                )

        return {
            "success": success,
            "failed": failed,
            "total": total,
            "already_running": False,
        }

    finally:

        bulk_running = False


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

    if bulk_running:

        await update.effective_message.reply_text(
            "⏳ Zaten bekleyen istekler onaylanıyor."
        )

        return

    # Yeni gelenleri de bu andan itibaren otomatik onayla
    approval_enabled = True

    status_message = await update.effective_message.reply_text(
        "⏳ Mevcut bekleyen katılma istekleri aranıyor...\n\n"
        "🟢 Yeni gelenler otomatik onaylanacak."
    )

    try:

        result = await process_existing_requests()

        if result["already_running"]:

            await status_message.edit_text(
                "⏳ Onay işlemi zaten çalışıyor."
            )

            return

        total = result["total"]
        success = result["success"]
        failed = result["failed"]

        if total == 0:

            await status_message.edit_text(
                "🟢 Otomatik onay AKTİF.\n\n"
                "📭 Şu anda bekleyen istek bulunamadı.\n\n"
                "Yeni gelen katılma istekleri otomatik "
                "olarak onaylanacak."
            )

            return

        await status_message.edit_text(
            "✅ ONAYLAMA TAMAMLANDI\n\n"
            f"👥 Bulunan: {total:,}\n"
            f"✅ Onaylanan: {success:,}\n"
            f"❌ Onaylanamayan: {failed:,}\n\n"
            "🟢 Otomatik onay AKTİF.\n"
            "Yeni gelenler otomatik onaylanacak."
        )

    except Exception as e:

        logger.exception(
            "TOPLU ONAY HATASI"
        )

        await status_message.edit_text(
            "❌ Toplu onay sırasında hata oluştu.\n\n"
            f"{str(e)[:500]}\n\n"
            "🟢 Otomatik onay açık bırakıldı."
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
        "⛔ Otomatik onay DURDURULDU.\n\n"
        "Tekrar başlatmak ve bekleyenleri onaylamak için:\n"
        "/onayla"
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

    bulk_state = (
        "ÇALIŞIYOR ⏳"
        if bulk_running
        else "BOŞ"
    )

    await update.effective_message.reply_text(
        "📊 SİSTEM DURUMU\n\n"
        f"🤖 Otomatik onay: {state}\n"
        f"⚡ Toplu işlem: {bulk_state}\n"
        f"🚀 Paralel işlem: {CONCURRENCY}\n"
        f"🎯 Hedef: @{TARGET_GROUP}"
    )


# =========================================================
# TELEGRAM BOT BAŞLANGICI
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

    await start_telethon()

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

    # Komutlar
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

    # Yeni katılma istekleri
    app.add_handler(
        ChatJoinRequestHandler(
            approve_new_request
        )
    )

    logger.info(
        "BOT BAŞLIYOR | hedef=@%s | concurrency=%s",
        TARGET_GROUP,
        CONCURRENCY,
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
