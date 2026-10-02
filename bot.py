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
# SADECE BU 4 ALANI DOLDUR
# =========================================================

BOT_TOKEN = "8206822443:AAE3zOkzOpU3pD6KpI_0EnjY4B73wSZDsmc"

API_ID = 31895413

API_HASH = "2d3a1e09a65f33ed4c54483dcb4a28bb"

SESSION_STRING = "1BJWap1wBu63pJfv6cV_i8BlVZ3BpV5J1Yjl183tFwenBkDU7koOW9Mp8kbTDPMeE2woc_SWB6wShop5W9siIiv6FFKKJp8y1E_MKpJ9t0MoMHb-t0Q_sVPtxe0qjityOAg_lovAtH1jfA4gmumvuEGwcfLibjkFncp_t97_w0Jlvo81M1JJpP6drv8K1KdFQXowRD3z6iKEKc-jVRI6knIyT8hzBAMwj3mY-9LheQdt_FCKNlySsWheeM4DMiRQlu0LoenXo7U4aPqBcJRPUPYYY_R9-OgzwnD-oO_YWrz79b3nt8JDn4TZiDvZFxlT-XEeD4Ru8cxEO0-EBBy4Puli3GqS6nCc="


# =========================================================
# AYARLAR
# =========================================================

TARGET_GROUP = "novaprimesohbet"

# Aynı anda kaç kullanıcı onaylanacak.
# Çok yüksek yapmak FloodWait riskini artırabilir.
CONCURRENCY = 5

# Telegram'dan bir seferde alınacak kullanıcı sayısı.
FETCH_LIMIT = 100

# FloodWait dışında geçici hatalarda kaç kez tekrar denenecek.
MAX_RETRIES = 8

# İşlem sırasında Telegram'a biraz nefes payı.
REQUEST_DELAY = 0.05


# =========================================================
# KONTROLLER
# =========================================================

if not BOT_TOKEN or BOT_TOKEN == "BURAYA_BOT_TOKEN":
    raise RuntimeError(
        "BOT_TOKEN değerini gir."
    )

if not isinstance(API_ID, int) or API_ID <= 0:
    raise RuntimeError(
        "API_ID sayı olmalıdır."
    )

if not API_HASH or API_HASH == "BURAYA_API_HASH":
    raise RuntimeError(
        "API_HASH değerini gir."
    )

if (
    not SESSION_STRING
    or SESSION_STRING == "BURAYA_YENI_SESSION_STRING"
):
    raise RuntimeError(
        "SESSION_STRING değerini gir."
    )

if not TARGET_GROUP:
    raise RuntimeError(
        "TARGET_GROUP boş olamaz."
    )


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(
    "nova-join-approver"
)


# =========================================================
# GLOBAL DURUM
# =========================================================

approval_enabled = False

bulk_running = False

telethon_client = None

bulk_lock = asyncio.Lock()


# =========================================================
# HEDEF GRUP KONTROLÜ
# =========================================================

def is_target_chat(chat) -> bool:

    if not chat:
        return False

    username = (
        getattr(chat, "username", None)
        or ""
    ).lower().lstrip("@")

    target = (
        TARGET_GROUP
        .lower()
        .lstrip("@")
    )

    return username == target


# =========================================================
# TELETHON BAĞLANTISI
# =========================================================

async def start_telethon():

    global telethon_client

    if telethon_client is not None:
        return

    logger.info(
        "Telethon bağlantısı başlatılıyor..."
    )

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
# HEDEF GRUBUN INPUT PEER'INI AL
# =========================================================

async def get_target_peer():

    if telethon_client is None:
        raise RuntimeError(
            "Telethon bağlantısı yok."
        )

    try:

        peer = await telethon_client.get_input_entity(
            TARGET_GROUP
        )

        if peer is None:
            raise RuntimeError(
                "Telegram hedef grup için InputPeer döndürmedi."
            )

        logger.info(
            "HEDEF PEER HAZIR | %s",
            type(peer).__name__,
        )

        return peer

    except Exception as e:

        logger.exception(
            "HEDEF PEER ALINAMADI"
        )

        raise RuntimeError(
            f"Hedef grup bulunamadı: {e}"
        )


# =========================================================
# BEKLEYENLERİN İLK SAYFASINI AL
#
# ÖNEMLİ:
# Her seferinde listenin BAŞINDAN alıyoruz.
#
# Aldıklarımızı onayladıktan sonra onlar listeden
# kayboluyor. Böylece offset/pagination problemi
# tamamen ortadan kalkıyor.
# =========================================================

async def get_pending_batch(peer):

    if telethon_client is None:
        raise RuntimeError(
            "Telethon bağlantısı yok."
        )

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            result = await telethon_client(
                functions.messages.GetChatInviteImportersRequest(
                    peer=peer,
                    requested=True,
                    offset_date=0,
                    offset_user=types.InputUserEmpty(),
                    limit=FETCH_LIMIT,
                )
            )

            importers = getattr(
                result,
                "importers",
                None,
            ) or []

            users = getattr(
                result,
                "users",
                None,
            ) or []

            if not importers:
                return []

            users_by_id = {
                user.id: user
                for user in users
                if getattr(user, "id", None) is not None
            }

            pending = []

            for importer in importers:

                if not getattr(
                    importer,
                    "requested",
                    False,
                ):
                    continue

                user = users_by_id.get(
                    importer.user_id
                )

                if user is None:
                    logger.warning(
                        "Kullanıcı bilgisi bulunamadı | user_id=%s",
                        importer.user_id,
                    )
                    continue

                access_hash = getattr(
                    user,
                    "access_hash",
                    None,
                )

                if access_hash is None:
                    logger.warning(
                        "Access hash yok | user_id=%s",
                        user.id,
                    )
                    continue

                pending.append(
                    types.InputUser(
                        user_id=user.id,
                        access_hash=access_hash,
                    )
                )

            # Aynı kullanıcı iki kez gelirse tek tut.
            unique = {}

            for user in pending:
                unique[user.user_id] = user

            pending = list(
                unique.values()
            )

            logger.info(
                "BEKLEYEN SAYFASI | Telegram=%s | işlenecek=%s",
                len(importers),
                len(pending),
            )

            return pending

        except FloodWaitError as e:

            wait_time = max(
                int(e.seconds),
                1,
            )

            logger.warning(
                "GET FLOOD WAIT | %s saniye bekleniyor.",
                wait_time,
            )

            await asyncio.sleep(
                wait_time
            )

        except RPCError as e:

            logger.error(
                "GET RPC HATASI | deneme=%s/%s | %s",
                attempt,
                MAX_RETRIES,
                e,
            )

            if attempt >= MAX_RETRIES:
                raise

            await asyncio.sleep(
                min(attempt * 2, 10)
            )

        except Exception as e:

            logger.exception(
                "BEKLEYENLER ALINIRKEN HATA | deneme=%s/%s",
                attempt,
                MAX_RETRIES,
            )

            if attempt >= MAX_RETRIES:
                raise

            await asyncio.sleep(
                min(attempt * 2, 10)
            )

    return []


# =========================================================
# TEK KULLANICIYI ONAYLA
# =========================================================

async def approve_one(
    peer,
    user,
):

    if telethon_client is None:
        return False

    user_id = getattr(
        user,
        "user_id",
        None,
    )

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            await telethon_client(
                functions.messages.HideChatJoinRequestRequest(
                    peer=peer,
                    user_id=user,
                    approved=True,
                )
            )

            logger.info(
                "ONAYLANDI | user_id=%s",
                user_id,
            )

            return True

        except FloodWaitError as e:

            wait_time = max(
                int(e.seconds),
                1,
            )

            logger.warning(
                "FLOOD WAIT | user_id=%s | %s saniye",
                user_id,
                wait_time,
            )

            await asyncio.sleep(
                wait_time
            )

        except RPCError as e:

            error_text = str(e)

            # İstek zaten işlenmişse bunu başarısız
            # saymıyoruz.
            if (
                "HIDE_REQUESTER_MISSING"
                in error_text
                or "USER_ALREADY_PARTICIPANT"
                in error_text
            ):

                logger.info(
                    "İSTEK ZATEN İŞLENMİŞ | user_id=%s",
                    user_id,
                )

                return True

            logger.warning(
                "ONAY RPC HATASI | user_id=%s | deneme=%s/%s | %s",
                user_id,
                attempt,
                MAX_RETRIES,
                e,
            )

            if attempt >= MAX_RETRIES:
                return False

            await asyncio.sleep(
                min(attempt * 2, 10)
            )

        except Exception as e:

            logger.warning(
                "ONAY HATASI | user_id=%s | deneme=%s/%s | %s",
                user_id,
                attempt,
                MAX_RETRIES,
                e,
            )

            if attempt >= MAX_RETRIES:
                return False

            await asyncio.sleep(
                min(attempt * 2, 10)
            )

    return False


# =========================================================
# BİR BATCH'İ ONAYLA
# =========================================================

async def approve_batch(
    peer,
    users,
):

    if not users:
        return 0, 0

    semaphore = asyncio.Semaphore(
        CONCURRENCY
    )

    success = 0
    failed = 0

    counter_lock = asyncio.Lock()

    async def worker(user):

        nonlocal success
        nonlocal failed

        async with semaphore:

            result = await approve_one(
                peer,
                user,
            )

            async with counter_lock:

                if result:
                    success += 1
                else:
                    failed += 1

            await asyncio.sleep(
                REQUEST_DELAY
            )

    tasks = [
        asyncio.create_task(
            worker(user)
        )
        for user in users
    ]

    await asyncio.gather(
        *tasks,
        return_exceptions=False,
    )

    return success, failed


# =========================================================
# TÜM BEKLEYENLERİ ONAYLA
#
# BURASI ASIL ÇÖZÜM.
#
# 199 / 200 / 100 sınırında durmaz.
#
# Her tur:
#
# 1. İlk 100 bekleyeni al
# 2. Onayla
# 3. Tekrar ilk 100'ü al
# 4. Liste boşalana kadar devam et
#
# =========================================================

async def approve_all_pending():

    global bulk_running

    async with bulk_lock:

        if bulk_running:

            return {
                "total": 0,
                "success": 0,
                "failed": 0,
                "already_running": True,
            }

        bulk_running = True

        try:

            peer = await get_target_peer()

            total_success = 0
            total_failed = 0
            total_seen = 0

            batch_number = 0

            while True:

                batch_number += 1

                logger.info(
                    "========================================"
                )

                logger.info(
                    "BATCH #%s | BEKLEYENLER ALINIYOR",
                    batch_number,
                )

                users = await get_pending_batch(
                    peer
                )

                # Hiç istek kalmadı.
                if not users:

                    logger.info(
                        "BEKLEYEN İSTEK KALMADI."
                    )

                    break

                batch_size = len(users)

                total_seen += batch_size

                logger.info(
                    "BATCH #%s | %s kullanıcı işlenecek",
                    batch_number,
                    batch_size,
                )

                success, failed = (
                    await approve_batch(
                        peer,
                        users,
                    )
                )

                total_success += success
                total_failed += failed

                logger.info(
                    "BATCH #%s BİTTİ | başarılı=%s | başarısız=%s | toplam başarılı=%s | toplam başarısız=%s",
                    batch_number,
                    success,
                    failed,
                    total_success,
                    total_failed,
                )

                # Eğer batch'in tamamı başarısız olduysa
                # sonsuz döngüye girmemek için tekrar
                # kontrol ediyoruz.
                if success == 0:

                    logger.warning(
                        "Bu batch'te hiçbir istek onaylanamadı."
                    )

                    # Birkaç saniye bekle.
                    await asyncio.sleep(5)

                    # Tekrar kontrol et.
                    retry_users = (
                        await get_pending_batch(
                            peer
                        )
                    )

                    if not retry_users:

                        break

                    # Hâlâ aynı kullanıcılar varsa
                    # Telegram tarafında kalıcı bir hata
                    # vardır.
                    if (
                        len(retry_users)
                        >= batch_size
                    ):

                        raise RuntimeError(
                            "Bekleyen istekler alınabiliyor fakat "
                            "Telegram hiçbirini onaylamaya izin vermiyor. "
                            "Hesabın grup yöneticisi olduğundan ve "
                            "üyeleri onaylama yetkisine sahip olduğundan emin ol."
                        )

            return {
                "total": total_seen,
                "success": total_success,
                "failed": total_failed,
                "already_running": False,
            }

        finally:

            bulk_running = False


# =========================================================
# YENİ GELEN İSTEK
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

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

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

            wait_time = float(
                e.retry_after
            )

            logger.warning(
                "BOT RATE LIMIT | %.2f saniye",
                wait_time,
            )

            await asyncio.sleep(
                wait_time
            )

        except TelegramError as e:

            logger.warning(
                "YENİ İSTEK ONAY HATASI | deneme=%s/%s | user_id=%s | %s",
                attempt,
                MAX_RETRIES,
                user.id,
                e,
            )

            if attempt >= MAX_RETRIES:
                return

            await asyncio.sleep(
                min(attempt * 2, 10)
            )

        except Exception as e:

            logger.exception(
                "YENİ İSTEK BEKLENMEYEN HATA | %s",
                e,
            )

            return


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
            "⏳ Toplu onay zaten çalışıyor."
        )

        return

    # Yeni gelenleri hemen otomatik onayla.
    approval_enabled = True

    status = await update.effective_message.reply_text(
        "⏳ TOPLU ONAY BAŞLADI\n\n"
        "Bekleyen istekler parça parça işleniyor.\n"
        "🟢 Yeni gelenler de otomatik onaylanacak."
    )

    try:

        result = await approve_all_pending()

        if result.get("already_running"):

            await status.edit_text(
                "⏳ Toplu onay zaten çalışıyor.\n\n"
                "🟢 Otomatik onay aktif."
            )

            return

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

        if failed == 0:

            await status.edit_text(
                "✅ ONAYLAMA TAMAMLANDI\n\n"
                f"👥 İşlenen: {total:,}\n"
                f"✅ Onaylanan: {success:,}\n"
                f"❌ Başarısız: 0\n\n"
                "🟢 Otomatik onay AKTİF.\n"
                "Yeni gelenler otomatik olarak onaylanacak."
            )

        else:

            await status.edit_text(
                "⚠️ ONAYLAMA TAMAMLANDI\n\n"
                f"👥 İşlenen: {total:,}\n"
                f"✅ Onaylanan: {success:,}\n"
                f"❌ Başarısız: {failed:,}\n\n"
                "🟢 Otomatik onay AKTİF."
            )

    except Exception as e:

        logger.exception(
            "TOPLU ONAY KOMUT HATASI"
        )

        # Hata olsa bile yeni gelen istekler
        # otomatik onaylanmaya devam etsin.
        approval_enabled = True

        await status.edit_text(
            "❌ TOPLU ONAY DURDU\n\n"
            f"Hata: {str(e)[:700]}\n\n"
            "🟢 Yeni gelen isteklerin otomatik onayı AKTİF."
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
        f"📦 Batch boyutu: {FETCH_LIMIT}\n"
        f"⚡ Paralel işlem: {CONCURRENCY}\n"
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
            "Bekleyenlerin tamamını onayla",
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
        "BOT HAZIR | hedef=@%s | batch=%s | concurrency=%s",
        TARGET_GROUP,
        FETCH_LIMIT,
        CONCURRENCY,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info(
        "BOT BAŞLIYOR | hedef=@%s",
        TARGET_GROUP,
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


# =========================================================
# ÇALIŞTIR
# =========================================================

if __name__ == "__main__":
    main()
