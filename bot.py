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

from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError


# =========================================================
# SADECE BU ALANI DOLDUR
# =========================================================

BOT_TOKEN = "8206822443:AAE3zOkzOpU3pD6KpI_0EnjY4B73wSZDsmc"

API_ID = 31895413

API_HASH = "2d3a1e09a65f33ed4c54483dcb4a28bb"

SESSION_STRING = "1BJWap1wBu63pJfv6cV_i8BlVZ3BpV5J1Yjl183tFwenBkDU7koOW9Mp8kbTDPMeE2woc_SWB6wShop5W9siIiv6FFKKJp8y1E_MKpJ9t0MoMHb-t0Q_sVPtxe0qjityOAg_lovAtH1jfA4gmumvuEGwcfLibjkFncp_t97_w0Jlvo81M1JJpP6drv8K1KdFQXowRD3z6iKEKc-jVRI6knIyT8hzBAMwj3mY-9LheQdt_FCKNlySsWheeM4DMiRQlu0LoenXo7U4aPqBcJRPUPYYY_R9-OgzwnD-oO_YWrz79b3nt8JDn4TZiDvZFxlT-XEeD4Ru8cxEO0-EBBy4Puli3GqS6nCc="


# =========================================================
# SABİT AYARLAR
# =========================================================

TARGET_GROUP = "novaprimesohbet"


# =========================================================
# KONTROLLER
# =========================================================

if not BOT_TOKEN or BOT_TOKEN == "BURAYA_YENI_BOT_TOKEN":
    raise RuntimeError(
        "BOT_TOKEN değerini bot.py içindeki ayar alanına gir."
    )

if not isinstance(API_ID, int) or API_ID <= 0:
    raise RuntimeError(
        "API_ID sayı olmalıdır."
    )

if not API_HASH or API_HASH == "BURAYA_YENI_API_HASH":
    raise RuntimeError(
        "API_HASH değerini bot.py içindeki ayar alanına gir."
    )

if not SESSION_STRING or SESSION_STRING == "BURAYA_YENI_SESSION_STRING":
    raise RuntimeError(
        "SESSION_STRING değerini bot.py içindeki ayar alanına gir."
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

    username = (
        getattr(chat, "username", None)
        or ""
    ).lower()

    target = TARGET_GROUP.lower().lstrip("@")

    return username == target


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
            "SESSION_STRING geçersiz veya Telegram hesabı yetkisiz."
        )

    me = await telethon_client.get_me()

    logger.info(
        "TELETHON HAZIR | user_id=%s | isim=%s",
        me.id,
        me.first_name or "",
    )


# =========================================================
# BEKLEYEN İSTEK SAYISINI BUL
# =========================================================

async def get_pending_count():

    if telethon_client is None:
        raise RuntimeError(
            "Telethon bağlantısı başlatılmamış."
        )

    entity = await telethon_client.get_entity(
        TARGET_GROUP
    )

    while True:

        try:

            result = await telethon_client(
                functions.messages.GetChatInviteImportersRequest(
                    peer=entity,
                    requested=True,
                    offset_date=0,
                    offset_user=None,
                    limit=1,
                )
            )

            count = getattr(
                result,
                "count",
                None,
            )

            if count is None:
                count = len(
                    getattr(
                        result,
                        "importers",
                        [],
                    )
                )

            return entity, int(count)

        except FloodWaitError as e:

            logger.warning(
                "SAYIM FLOOD WAIT | %s saniye bekleniyor.",
                e.seconds,
            )

            await asyncio.sleep(
                e.seconds
            )

        except RPCError as e:

            logger.error(
                "BEKLEYENLER SAYILAMADI | %s",
                e,
            )

            raise

        except Exception as e:

            logger.exception(
                "SAYIM HATASI"
            )

            raise


# =========================================================
# TÜM BEKLEYENLERİ TOPLU ONAYLA
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

        logger.info(
            "BEKLEYEN İSTEK SAYISI ALINIYOR..."
        )

        entity, before_count = (
            await get_pending_count()
        )

        logger.info(
            "BEKLEYEN İSTEK: %s",
            before_count,
        )

        if before_count <= 0:

            return {
                "total": 0,
                "success": 0,
                "failed": 0,
                "already_running": False,
            }

        logger.info(
            "TOPLU ONAY BAŞLIYOR | toplam=%s",
            before_count,
        )

        while True:

            try:

                await telethon_client(
                    functions.messages.HideAllChatJoinRequestsRequest(
                        peer=entity,
                        approved=True,
                    )
                )

                logger.info(
                    "TOPLU ONAY İSTEĞİ TELEGRAM'A GÖNDERİLDİ."
                )

                break

            except FloodWaitError as e:

                logger.warning(
                    "TOPLU ONAY FLOOD WAIT | %s saniye bekleniyor.",
                    e.seconds,
                )

                await asyncio.sleep(
                    e.seconds
                )

            except RPCError as e:

                logger.error(
                    "TOPLU ONAY RPC HATASI | %s",
                    e,
                )

                raise

            except Exception as e:

                logger.exception(
                    "TOPLU ONAY HATASI"
                )

                raise

        # Telegram'ın işlemi tamamlaması için
        # kısa bir süre bekle.
        await asyncio.sleep(2)

        try:

            _, remaining = (
                await get_pending_count()
            )

        except Exception:

            remaining = None

        if remaining is not None:

            success = max(
                0,
                before_count - remaining,
            )

            failed = remaining

        else:

            success = before_count
            failed = 0

        logger.info(
            "TOPLU ONAY BİTTİ | önce=%s | kalan=%s | tahmini_onay=%s",
            before_count,
            remaining,
            success,
        )

        return {
            "total": before_count,
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

            wait_time = float(
                e.retry_after
            )

            logger.warning(
                "BOT RATE LIMIT | %.2f saniye bekleniyor.",
                wait_time,
            )

            await asyncio.sleep(
                wait_time
            )

        except TelegramError as e:

            logger.error(
                "YENİ İSTEK ONAYLANAMADI | user_id=%s | %s",
                user.id,
                e,
            )

            return

        except Exception as e:

            logger.exception(
                "YENİ İSTEK BEKLENMEYEN HATA | %s",
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
            "⏳ Toplu onay zaten çalışıyor."
        )

        return

    # Yeni gelenleri otomatik onaylamaya başla.
    approval_enabled = True

    status = await update.effective_message.reply_text(
        "⏳ Bekleyen katılma istekleri kontrol ediliyor...\n\n"
        "🟢 Yeni gelen istekler otomatik onaylanacak."
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
                "✅ TOPLU ONAY TAMAMLANDI\n\n"
                f"👥 Bulunan: {total:,}\n"
                f"✅ Onaylanan: {success:,}\n"
                f"❌ Kalan: 0\n\n"
                "🟢 Otomatik onay AKTİF.\n"
                "Yeni gelenler otomatik olarak onaylanacak."
            )

        else:

            await status.edit_text(
                "⚠️ TOPLU ONAY TAMAMLANDI\n\n"
                f"👥 Bulunan: {total:,}\n"
                f"✅ Onaylanan: {success:,}\n"
                f"❌ Kalan: {failed:,}\n\n"
                "🟢 Otomatik onay AKTİF."
            )

    except Exception as e:

        logger.exception(
            "TOPLU ONAY KOMUT HATASI"
        )

        approval_enabled = True

        await status.edit_text(
            "❌ TOPLU ONAY BAŞARISIZ\n\n"
            f"{str(e)[:700]}\n\n"
            "🟢 Otomatik yeni istek onayı yine AKTİF."
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
            "Tüm bekleyenleri onayla",
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
        "BOT HAZIR | hedef=@%s",
        TARGET_GROUP,
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
