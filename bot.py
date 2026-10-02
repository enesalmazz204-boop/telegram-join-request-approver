import asyncio
import logging

from telegram import Update, BotCommand
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ChatJoinRequestHandler,
    ContextTypes,
)

from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError


# =========================================================
# SADECE BURAYI DOLDUR
# =========================================================

BOT_TOKEN = "8206822443:AAE3zOkzOpU3pD6KpI_0EnjY4B73wSZDsmc"

API_ID = 31895413
API_HASH = "2d3a1e09a65f33ed4c54483dcb4a28bb"
SESSION_STRING = "1BJWap1wBu63pJfv6cV_i8BlVZ3BpV5J1Yjl183tFwenBkDU7koOW9Mp8kbTDPMeE2woc_SWB6wShop5W9siIiv6FFKKJp8y1E_MKpJ9t0MoMHb-t0Q_sVPtxe0qjityOAg_lovAtH1jfA4gmumvuEGwcfLibjkFncp_t97_w0Jlvo81M1JJpP6drv8K1KdFQXowRD3z6iKEKc-jVRI6knIyT8hzBAMwj3mY-9LheQdt_FCKNlySsWheeM4DMiRQlu0LoenXo7U4aPqBcJRPUPYYY_R9-OgzwnD-oO_YWrz79b3nt8JDn4TZiDvZFxlT-XEeD4Ru8cxEO0-EBBy4Puli3GqS6nCc="


# =========================================================
# SABİT AYARLAR
# =========================================================

TARGET_GROUP = "novaprimesohbet"

# Aynı anda kaç isteğin işleneceği
CONCURRENCY = 10


# =========================================================
# KONTROLLER
# =========================================================

if not BOT_TOKEN or BOT_TOKEN == "BURAYA_YENI_BOT_TOKEN":
    raise RuntimeError("BOT_TOKEN değerini bot.py içinde doldur.")

if not isinstance(API_ID, int) or API_ID <= 0:
    raise RuntimeError("API_ID sayı olarak girilmelidir.")

if not API_HASH or API_HASH == "BURAYA_API_HASH":
    raise RuntimeError("API_HASH değerini bot.py içinde doldur.")

if not SESSION_STRING or SESSION_STRING == "BURAYA_SESSION_STRING":
    raise RuntimeError("SESSION_STRING değerini bot.py içinde doldur.")

if not TARGET_GROUP:
    raise RuntimeError("TARGET_GROUP boş olamaz.")


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("nova-join-approver")


# =========================================================
# DURUM
# =========================================================

approval_enabled = False
bulk_running = False
telethon_client = None


# =========================================================
# HEDEF GRUP KONTROLÜ
# =========================================================

def is_target_chat(chat) -> bool:

    if not chat:
        return False

    username = (chat.username or "").lower()

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

    except TelegramError as e:

        logger.warning(
            "Admin kontrolü başarısız: %s",
            e,
        )

        return False


# =========================================================
# TELETHON BAŞLAT
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
            "SESSION_STRING geçersiz veya yetkisiz."
        )

    me = await telethon_client.get_me()

    logger.info(
        "TELETHON HAZIR | user_id=%s | isim=%s",
        me.id,
        me.first_name or "",
    )


# =========================================================
# BEKLEYEN İSTEKLERİ BUL
# =========================================================

async def get_pending_requests():

    if telethon_client is None:
        raise RuntimeError(
            "Telethon bağlantısı başlatılmamış."
        )

    entity = await telethon_client.get_entity(
        TARGET_GROUP
    )

    pending = []

    offset_date = None
    offset_user = types.InputUserEmpty()

    while True:

        try:

            result = await telethon_client(
                functions.messages.GetChatInviteImportersRequest(
                    peer=entity,
                    requested=True,
                    offset_date=offset_date,
                    offset_user=offset_user,
                    offset_link="",
                    limit=100,
                )
            )

        except FloodWaitError as e:

            logger.warning(
                "FloodWait: %s saniye bekleniyor.",
                e.seconds,
            )

            await asyncio.sleep(e.seconds)
            continue

        importers = result.importers

        if not importers:
            break

        users_by_id = {
            user.id: user
            for user in result.users
        }

        for importer in importers:

            user = users_by_id.get(
                importer.user_id
            )

            if not user:
                continue

            if not getattr(
                importer,
                "requested",
                False,
            ):
                continue

            access_hash = getattr(
                user,
                "access_hash",
                None,
            )

            if access_hash is None:
                continue

            pending.append(
                types.InputUser(
                    user_id=user.id,
                    access_hash=access_hash,
                )
            )

        logger.info(
            "SAYFA | bulunan=%s | toplam=%s",
            len(importers),
            len(pending),
        )

        if len(importers) < 100:
            break

        last_importer = importers[-1]

        last_user = users_by_id.get(
            last_importer.user_id
        )

        if not last_user:
            break

        last_access_hash = getattr(
            last_user,
            "access_hash",
            None,
        )

        if last_access_hash is None:
            break

        offset_date = last_importer.date

        offset_user = types.InputUser(
            user_id=last_user.id,
            access_hash=last_access_hash,
        )

    # Aynı kullanıcıyı bir kez tut.
    unique = {}

    for user in pending:
        unique[user.user_id] = user

    pending = list(unique.values())

    logger.info(
        "TOPLAM BEKLEYEN İSTEK: %s",
        len(pending),
    )

    return entity, pending


# =========================================================
# TEK BEKLEYEN İSTEĞİ ONAYLA
# =========================================================

async def approve_existing_user(
    entity,
    user,
):

    if telethon_client is None:
        return False

    while True:

        try:

            await telethon_client(
                functions.messages.HideChatJoinRequestRequest(
                    peer=entity,
                    user_id=user,
                    approved=True,
                )
            )

            logger.info(
                "ONAYLANDI | user_id=%s",
                user.user_id,
            )

            return True

        except FloodWaitError as e:

            logger.warning(
                "FLOOD WAIT | %s saniye | user_id=%s",
                e.seconds,
                user.user_id,
            )

            await asyncio.sleep(e.seconds)

        except RPCError as e:

            logger.error(
                "ONAYLANAMADI | user_id=%s | %s",
                user.user_id,
                e,
            )

            return False

        except Exception as e:

            logger.exception(
                "BEKLENMEYEN HATA | user_id=%s | %s",
                user.user_id,
                e,
            )

            return False


# =========================================================
# TÜM BEKLEYENLERİ ONAYLA
# =========================================================

async def approve_all_pending():

    global bulk_running

    if bulk_running:

        return {
            "total": 0,
            "success": 0,
            "failed": 0,
            "already_running": True,
        }

    bulk_running = True

    try:

        entity, users = (
            await get_pending_requests()
        )

        total = len(users)

        if total == 0:

            return {
                "total": 0,
                "success": 0,
                "failed": 0,
                "already_running": False,
            }

        logger.info(
            "TOPLU ONAY BAŞLADI | toplam=%s",
            total,
        )

        semaphore = asyncio.Semaphore(
            CONCURRENCY
        )

        success = 0
        failed = 0

        async def worker(user):

            async with semaphore:

                return await approve_existing_user(
                    entity,
                    user,
                )

        tasks = [
            asyncio.create_task(
                worker(user)
            )
            for user in users
        ]

        for future in asyncio.as_completed(tasks):

            try:

                result = await future

                if result:
                    success += 1
                else:
                    failed += 1

            except Exception as e:

                failed += 1

                logger.exception(
                    "Worker hatası: %s",
                    e,
                )

            processed = success + failed

            if (
                processed % 50 == 0
                or processed == total
            ):

                logger.info(
                    "İLERLEME | %s/%s | başarılı=%s | başarısız=%s",
                    processed,
                    total,
                    success,
                    failed,
                )

        logger.info(
            "TOPLU ONAY BİTTİ | başarılı=%s | başarısız=%s",
            success,
            failed,
        )

        return {
            "total": total,
            "success": success,
            "failed": failed,
            "already_running": False,
        }

    finally:

        bulk_running = False


# =========================================================
# YENİ GELEN KATILMA İSTEĞİ
# =========================================================

async def new_join_request(
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
            "İSTEK BEKLETİLİYOR | user_id=%s",
            user.id,
        )

        return

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

            logger.warning(
                "BOT RATE LIMIT | %.2f saniye",
                float(e.retry_after),
            )

            await asyncio.sleep(
                float(e.retry_after)
            )

        except TelegramError as e:

            logger.error(
                "YENİ İSTEK ONAYLANAMADI | user_id=%s | %s",
                user.id,
                e,
            )

            return


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
            "⏳ Bekleyen istekler zaten onaylanıyor."
        )

        return

    # Bundan sonra yeni gelenler otomatik onaylanacak.
    approval_enabled = True

    status = await update.effective_message.reply_text(
        "⏳ Bekleyen katılma istekleri aranıyor...\n\n"
        "🟢 Yeni gelen istekler otomatik onaylanacak."
    )

    try:

        result = await approve_all_pending()

        total = result["total"]
        success = result["success"]
        failed = result["failed"]

        if total == 0:

            await status.edit_text(
                "🟢 OTOMATİK ONAY AKTİF\n\n"
                "📭 Bekleyen katılma isteği bulunamadı.\n\n"
                "Yeni gelenler otomatik olarak onaylanacak."
            )

            return

        await status.edit_text(
            "✅ ONAYLAMA TAMAMLANDI\n\n"
            f"👥 Bulunan: {total:,}\n"
            f"✅ Onaylanan: {success:,}\n"
            f"❌ Başarısız: {failed:,}\n\n"
            "🟢 Otomatik onay AKTİF."
        )

    except Exception as e:

        logger.exception(
            "TOPLU ONAY HATASI"
        )

        # Hata olsa bile otomatik onay açık kalır.
        approval_enabled = True

        await status.edit_text(
            "❌ Toplu onay sırasında hata oluştu.\n\n"
            f"{str(e)[:500]}\n\n"
            "🟢 Otomatik onay açık."
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
        "Tekrar başlatmak için /onayla"
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

    bulk = (
        "ÇALIŞIYOR ⏳"
        if bulk_running
        else "BOŞ"
    )

    await update.effective_message.reply_text(
        "📊 SİSTEM DURUMU\n\n"
        f"🤖 Otomatik onay: {state}\n"
        f"⚡ Toplu işlem: {bulk}\n"
        f"🚀 Paralel işlem: {CONCURRENCY}\n"
        f"🎯 Hedef: @{TARGET_GROUP}"
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

    logger.info(
        "BOT BAŞLIYOR | hedef=@%s | concurrency=%s",
        TARGET_GROUP,
        CONCURRENCY,
    )

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
            new_join_request
        )
    )

    logger.info(
        "BOT BAŞLADI | hedef=@%s | otomatik_onay=%s",
        TARGET_GROUP,
        approval_enabled,
    )

    app.run_polling(
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
