import asyncio
import logging
import os
from typing import Optional

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
from telethon.errors import (
    FloodWaitError,
    RPCError,
)


# =========================================================
# AYARLAR
# =========================================================
#
# Güvenlik nedeniyle bilgileri environment variable'dan alıyoruz.
#
# BOT_TOKEN
# API_ID
# API_HASH
# SESSION_STRING
#
# Örnek:
#
# BOT_TOKEN=xxxx
# API_ID=123456
# API_HASH=xxxx
# SESSION_STRING=xxxx
#
# =========================================================

BOT_TOKEN = '8206822443:AAE3zOkzOpU3pD6KpI_0EnjY4B73wSZDsmc'

API_ID_RAW = 31895413

API_HASH = '2d3a1e09a65f33ed4c54483dcb4a28bb'

SESSION_STRING = "1BJWap1wBu63pJfv6cV_i8BlVZ3BpV5J1Yjl183tFwenBkDU7koOW9Mp8kbTDPMeE2woc_SWB6wShop5W9siIiv6FFKKJp8y1E_MKpJ9t0MoMHb-t0Q_sVPtxe0qjityOAg_lovAtH1jfA4gmumvuEGwcfLibjkFncp_t97_w0Jlvo81M1JJpP6drv8K1KdFQXowRD3z6iKEKc-jVRI6knIyT8hzBAMwj3mY-9LheQdt_FCKNlySsWheeM4DMiRQlu0LoenXo7U4aPqBcJRPUPYYY_R9-OgzwnD-oO_YWrz79b3nt8JDn4TZiDvZFxlT-XEeD4Ru8cxEO0-EBBy4Puli3GqS6nCc="


# =========================================================
# HEDEF
# =========================================================

TARGET_GROUP = "novaprimesohbet"


# =========================================================
# PERFORMANS
# =========================================================

# Kullanıcılar 1000'erlik paketler halinde işlenir.
BATCH_SIZE = 1000

# Aynı anda Telegram'a gönderilecek onay isteği sayısı.
#
# 1000 yapmak doğru değildir.
# 1000 kişilik batch != 1000 eşzamanlı bağlantı.
#
# Bu değer Telegram rate-limitlerini gereksiz zorlamamak
# için kontrollü tutuluyor.
CONCURRENCY = 20

# Tek kullanıcı için geçici RPC hatalarında maksimum deneme.
MAX_RETRIES = 3

# FloodWait sonrasında ekstra güvenlik beklemesi.
FLOOD_EXTRA_DELAY = 1.0

# Batchler arasında küçük nefes aralığı.
BATCH_DELAY = 0.5

# İstekleri çekerken Telegram'a verilen sayfa boyutu.
#
# Telegram'ın GetChatInviteImporters metodunun döndürdüğü
# sayfa pratikte 100 civarında tutuluyor.
FETCH_PAGE_SIZE = 100


# =========================================================
# API_ID
# =========================================================

try:
    API_ID = int(API_ID_RAW)
except Exception:
    API_ID = 0


# =========================================================
# KONTROLLER
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable eksik."
    )

if API_ID <= 0:
    raise RuntimeError(
        "API_ID environment variable eksik veya geçersiz."
    )

if not API_HASH:
    raise RuntimeError(
        "API_HASH environment variable eksik."
    )

if not SESSION_STRING:
    raise RuntimeError(
        "SESSION_STRING environment variable eksik."
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

telethon_client: Optional[TelegramClient] = None


# =========================================================
# İSTATİSTİK
# =========================================================

class Stats:
    def __init__(self):
        self.total = 0
        self.success = 0
        self.failed = 0
        self.blocked = 0
        self.already_done = 0
        self.retried = 0

    @property
    def handled(self):
        return (
            self.success
            + self.failed
            + self.blocked
            + self.already_done
        )


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

    target = TARGET_GROUP.lower().lstrip("@")

    return username == target


# =========================================================
# ADMIN KONTROLÜ - BOT
# =========================================================

async def is_bot_admin(
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
            "BOT ADMIN KONTROL HATASI | %s",
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
        request_retries=3,
        connection_retries=5,
        retry_delay=1,
        auto_reconnect=True,
        flood_sleep_threshold=60,
    )

    await telethon_client.connect()

    if not await telethon_client.is_user_authorized():

        raise RuntimeError(
            "SESSION_STRING geçersiz veya Telegram hesabı yetkisiz."
        )

    me = await telethon_client.get_me()

    logger.info(
        "TELETHON HAZIR | user_id=%s | isim=%s | username=%s",
        me.id,
        me.first_name or "",
        me.username or "",
    )


# =========================================================
# TELETHON ADMIN KONTROLÜ
# =========================================================

async def verify_telethon_admin(entity):

    if telethon_client is None:
        raise RuntimeError(
            "Telethon bağlantısı yok."
        )

    me = await telethon_client.get_me()

    try:

        result = await telethon_client(
            functions.channels.GetParticipantRequest(
                channel=entity,
                participant=me,
            )
        )

    except RPCError as e:

        raise RuntimeError(
            "Telethon hesabı grubun yöneticisi olarak doğrulanamadı. "
            "Telegram hesabının grupta yönetici olduğundan emin ol."
        ) from e

    participant = getattr(
        result,
        "participant",
        None,
    )

    if isinstance(
        participant,
        (
            types.ChannelParticipantAdmin,
            types.ChannelParticipantCreator,
        ),
    ):
        logger.info(
            "TELETHON ADMIN OK | user_id=%s",
            me.id,
        )
        return True

    raise RuntimeError(
        "Telethon hesabı grupta yönetici değil. "
        "Katılma isteklerini onaylayabilmesi için "
        "Telegram hesabını yönetici yap."
    )


# =========================================================
# RPC HATA SINIFLANDIRMA
# =========================================================

def error_text(error) -> str:

    return str(error or "").lower()


def is_user_channels_too_much(error) -> bool:

    text = error_text(error)

    return (
        "user_channels_too_much" in text
        or "already in too many channels/supergroups" in text
        or "too many channels/supergroups" in text
    )


def is_already_participant(error) -> bool:

    text = error_text(error)

    return (
        "user_already_participant" in text
        or "already a participant" in text
        or "already in the group" in text
    )


def is_request_missing(error) -> bool:

    text = error_text(error)

    return (
        "hide_requester_missing" in text
        or "request was missing" in text
        or "already been handled" in text
        or "already handled" in text
    )


def is_permission_error(error) -> bool:

    text = error_text(error)

    return (
        "chat_admin_required" in text
        or "chat_admin_invite_required" in text
        or "right_forbidden" in text
        or "chat_write_forbidden" in text
        or "channel_private" in text
        or "channel_invalid" in text
    )


# =========================================================
# BEKLEYENLERİ ÇEK
# =========================================================

async def get_all_pending_requests():

    if telethon_client is None:
        raise RuntimeError(
            "Telethon bağlantısı başlatılmamış."
        )

    entity = await telethon_client.get_entity(
        TARGET_GROUP
    )

    # Önce gerçek admin yetkisini doğrula.
    await verify_telethon_admin(entity)

    pending = []

    offset_date = 0

    offset_user = types.InputUserEmpty()

    seen_cursors = set()

    page_number = 0

    while True:

        page_number += 1

        try:

            result = await telethon_client(
                functions.messages.GetChatInviteImportersRequest(
                    peer=entity,
                    requested=True,
                    offset_date=offset_date,
                    offset_user=offset_user,
                    limit=FETCH_PAGE_SIZE,
                )
            )

        except FloodWaitError as e:

            wait_time = (
                float(e.seconds)
                + FLOOD_EXTRA_DELAY
            )

            logger.warning(
                "İSTEKLERİ ÇEKME FLOOD WAIT | "
                "%.1f saniye bekleniyor.",
                wait_time,
            )

            await asyncio.sleep(
                wait_time
            )

            page_number -= 1
            continue

        except RPCError as e:

            logger.exception(
                "BEKLEYENLERİ ÇEKME RPC HATASI | %s",
                e,
            )

            raise

        importers = list(
            getattr(
                result,
                "importers",
                [],
            )
        )

        users = list(
            getattr(
                result,
                "users",
                [],
            )
        )

        logger.info(
            "BEKLEYEN SAYFASI #%s | Telegram=%s",
            page_number,
            len(importers),
        )

        if not importers:
            break

        users_by_id = {
            user.id: user
            for user in users
        }

        page_added = 0

        for importer in importers:

            if not getattr(
                importer,
                "requested",
                False,
            ):
                continue

            user_id = getattr(
                importer,
                "user_id",
                None,
            )

            if not user_id:
                continue

            user = users_by_id.get(
                user_id
            )

            if not user:
                continue

            access_hash = getattr(
                user,
                "access_hash",
                None,
            )

            if access_hash is None:
                continue

            input_user = types.InputUser(
                user_id=user.id,
                access_hash=access_hash,
            )

            pending.append(
                input_user
            )

            page_added += 1

        logger.info(
            "SAYFA #%s | eklendi=%s | toplam=%s",
            page_number,
            page_added,
            len(pending),
        )

        # Son kullanıcı üzerinden pagination.
        last_importer = importers[-1]

        last_user_id = getattr(
            last_importer,
            "user_id",
            None,
        )

        last_user = users_by_id.get(
            last_user_id
        )

        if not last_user:
            logger.warning(
                "Pagination için son kullanıcı bulunamadı. "
                "Liste burada bitiriliyor."
            )
            break

        last_access_hash = getattr(
            last_user,
            "access_hash",
            None,
        )

        if last_access_hash is None:
            logger.warning(
                "Son kullanıcının access_hash değeri yok. "
                "Pagination durduruldu."
            )
            break

        new_offset_date = int(
            getattr(
                last_importer,
                "date",
                0,
            )
        )

        new_offset_user = types.InputUser(
            user_id=last_user.id,
            access_hash=last_access_hash,
        )

        cursor_key = (
            new_offset_date,
            last_user.id,
        )

        if cursor_key in seen_cursors:

            logger.warning(
                "Aynı pagination cursor tekrar geldi. "
                "Sonsuz döngü engellendi."
            )

            break

        seen_cursors.add(
            cursor_key
        )

        offset_date = new_offset_date
        offset_user = new_offset_user

        if len(importers) < FETCH_PAGE_SIZE:
            break

    # Aynı kullanıcıyı tekilleştir.
    unique = {}

    for user in pending:

        unique[
            user.user_id
        ] = user

    pending = list(
        unique.values()
    )

    logger.info(
        "TOPLAM BEKLEYEN İSTEK: %s",
        len(pending),
    )

    return entity, pending


# =========================================================
# TEK KULLANICI ONAYI
# =========================================================

async def approve_one(
    entity,
    user,
    stats: Stats,
    semaphore: asyncio.Semaphore,
):

    async with semaphore:

        user_id = getattr(
            user,
            "user_id",
            0,
        )

        attempt = 0

        while True:

            attempt += 1

            try:

                await telethon_client(
                    functions.messages.HideChatJoinRequestRequest(
                        peer=entity,
                        user_id=user,
                        approved=True,
                    )
                )

                stats.success += 1

                return "success"

            except FloodWaitError as e:

                # FloodWait geçici bir hatadır.
                # Kullanıcıyı başarısız saymıyoruz.
                wait_time = (
                    float(e.seconds)
                    + FLOOD_EXTRA_DELAY
                )

                stats.retried += 1

                logger.warning(
                    "FLOOD WAIT | user_id=%s | "
                    "%.1f saniye bekleniyor.",
                    user_id,
                    wait_time,
                )

                await asyncio.sleep(
                    wait_time
                )

                # FloodWait sonrası aynı kullanıcıyı
                # tekrar dene.
                continue

            except RPCError as e:

                # =================================================
                # BU HATA KALICI / KULLANICIYA ÖZEL
                # =================================================

                if is_user_channels_too_much(e):

                    stats.blocked += 1

                    logger.warning(
                        "KULLANICI ATLANDI | "
                        "user_id=%s | "
                        "USER_CHANNELS_TOO_MUCH",
                        user_id,
                    )

                    return "blocked"

                # =================================================
                # ZATEN GRUPTA
                # =================================================

                if is_already_participant(e):

                    stats.already_done += 1

                    logger.info(
                        "ZATEN ÜYE | user_id=%s",
                        user_id,
                    )

                    return "already_done"

                # =================================================
                # İSTEK ARTIK YOK
                # =================================================

                if is_request_missing(e):

                    stats.already_done += 1

                    logger.info(
                        "İSTEK ARTIK YOK / İŞLENMİŞ | "
                        "user_id=%s",
                        user_id,
                    )

                    return "already_done"

                # =================================================
                # YETKİ PROBLEMİ
                # =================================================

                if is_permission_error(e):

                    logger.error(
                        "YETKİ HATASI | "
                        "user_id=%s | %s",
                        user_id,
                        e,
                    )

                    raise RuntimeError(
                        "Telegram hesabının katılma "
                        "isteklerini onaylama yetkisi yok. "
                        f"Telegram: {e}"
                    )

                # =================================================
                # DİĞER RPC HATALARI
                # =================================================

                if attempt < MAX_RETRIES:

                    stats.retried += 1

                    delay = (
                        1.0 * attempt
                    )

                    logger.warning(
                        "GEÇİCİ RPC HATASI | "
                        "user_id=%s | "
                        "deneme=%s/%s | "
                        "%.1f saniye sonra tekrar | %s",
                        user_id,
                        attempt,
                        MAX_RETRIES,
                        delay,
                        e,
                    )

                    await asyncio.sleep(
                        delay
                    )

                    continue

                stats.failed += 1

                logger.error(
                    "ONAYLANAMADI | "
                    "user_id=%s | "
                    "deneme=%s/%s | %s",
                    user_id,
                    attempt,
                    MAX_RETRIES,
                    e,
                )

                return "failed"

            except Exception as e:

                if attempt < MAX_RETRIES:

                    stats.retried += 1

                    delay = (
                        1.0 * attempt
                    )

                    logger.warning(
                        "BEKLENMEYEN HATA | "
                        "user_id=%s | "
                        "deneme=%s/%s | "
                        "%.1f saniye sonra tekrar | %s",
                        user_id,
                        attempt,
                        MAX_RETRIES,
                        delay,
                        e,
                    )

                    await asyncio.sleep(
                        delay
                    )

                    continue

                stats.failed += 1

                logger.exception(
                    "ONAYLANAMADI | "
                    "user_id=%s | %s",
                    user_id,
                    e,
                )

                return "failed"


# =========================================================
# 1000'LİK BATCH İŞLE
# =========================================================

async def process_batch(
    entity,
    users,
    batch_number,
    total_batches,
    stats: Stats,
):

    batch_total = len(users)

    if batch_total == 0:
        return

    batch_success = 0
    batch_failed = 0
    batch_blocked = 0
    batch_already = 0

    logger.info(
        "=================================================="
    )

    logger.info(
        "BATCH #%s/%s BAŞLADI | %s kullanıcı",
        batch_number,
        total_batches,
        batch_total,
    )

    semaphore = asyncio.Semaphore(
        CONCURRENCY
    )

    tasks = [
        asyncio.create_task(
            approve_one(
                entity,
                user,
                stats,
                semaphore,
            )
        )
        for user in users
    ]

    for future in asyncio.as_completed(
        tasks
    ):

        try:

            result = await future

            if result == "success":
                batch_success += 1

            elif result == "failed":
                batch_failed += 1

            elif result == "blocked":
                batch_blocked += 1

            elif result == "already_done":
                batch_already += 1

        except Exception as e:

            # Bir kullanıcının problemi diğerlerini
            # durdurmasın.
            batch_failed += 1

            logger.exception(
                "BATCH WORKER HATASI | %s",
                e,
            )

    logger.info(
        "BATCH #%s BİTTİ | "
        "başarılı=%s | "
        "zaten_tamam=%s | "
        "kanal_limiti=%s | "
        "başarısız=%s | "
        "toplam başarılı=%s | "
        "toplam kanal_limiti=%s | "
        "toplam başarısız=%s",
        batch_number,
        batch_success,
        batch_already,
        batch_blocked,
        batch_failed,
        stats.success,
        stats.blocked,
        stats.failed,
    )

    if BATCH_DELAY > 0:

        await asyncio.sleep(
            BATCH_DELAY
        )


# =========================================================
# TÜM BEKLEYENLERİ ONAYLA
# =========================================================

async def approve_all_pending():

    global bulk_running

    if bulk_running:

        return {
            "already_running": True,
            "total": 0,
            "success": 0,
            "failed": 0,
            "blocked": 0,
            "already_done": 0,
        }

    bulk_running = True

    try:

        logger.info(
            "=================================================="
        )

        logger.info(
            "TOPLU ONAY SİSTEMİ BAŞLIYOR"
        )

        logger.info(
            "BATCH_SIZE=%s | CONCURRENCY=%s",
            BATCH_SIZE,
            CONCURRENCY,
        )

        # =====================================================
        # TÜM BEKLEYENLERİ AL
        # =====================================================

        entity, users = (
            await get_all_pending_requests()
        )

        total = len(users)

        stats = Stats()

        stats.total = total

        if total == 0:

            logger.info(
                "BEKLEYEN İSTEK YOK."
            )

            return {
                "already_running": False,
                "total": 0,
                "success": 0,
                "failed": 0,
                "blocked": 0,
                "already_done": 0,
            }

        total_batches = (
            (total + BATCH_SIZE - 1)
            // BATCH_SIZE
        )

        logger.info(
            "TOPLAM %s KULLANICI | %s BATCH",
            total,
            total_batches,
        )

        # =====================================================
        # 1000'LİK BATCHLER
        # =====================================================

        for start in range(
            0,
            total,
            BATCH_SIZE,
        ):

            end = min(
                start + BATCH_SIZE,
                total,
            )

            batch_users = users[
                start:end
            ]

            batch_number = (
                start // BATCH_SIZE
            ) + 1

            await process_batch(
                entity=entity,
                users=batch_users,
                batch_number=batch_number,
                total_batches=total_batches,
                stats=stats,
            )

        # =====================================================
        # SON KONTROL
        # =====================================================

        remaining = None

        try:

            check_result = await telethon_client(
                functions.messages.GetChatInviteImportersRequest(
                    peer=entity,
                    requested=True,
                    offset_date=0,
                    offset_user=types.InputUserEmpty(),
                    limit=1,
                )
            )

            remaining = int(
                getattr(
                    check_result,
                    "count",
                    0,
                )
            )

        except Exception as e:

            logger.warning(
                "SON KALAN SAYISI ALINAMADI | %s",
                e,
            )

        logger.info(
            "=================================================="
        )

        logger.info(
            "TOPLU ONAY TAMAMLANDI"
        )

        logger.info(
            "Toplam bulunan      : %s",
            total,
        )

        logger.info(
            "Onaylanan            : %s",
            stats.success,
        )

        logger.info(
            "Zaten tamamlanmış   : %s",
            stats.already_done,
        )

        logger.info(
            "Kullanıcı limiti    : %s",
            stats.blocked,
        )

        logger.info(
            "Diğer başarısız     : %s",
            stats.failed,
        )

        logger.info(
            "Tekrar deneme       : %s",
            stats.retried,
        )

        logger.info(
            "Telegram'da kalan   : %s",
            remaining,
        )

        logger.info(
            "=================================================="
        )

        return {
            "already_running": False,
            "total": total,
            "success": stats.success,
            "failed": stats.failed,
            "blocked": stats.blocked,
            "already_done": stats.already_done,
            "remaining": remaining,
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
                "BOT RATE LIMIT | %.1f saniye",
                wait_time,
            )

            await asyncio.sleep(
                wait_time
            )

        except TelegramError as e:

            text = error_text(e)

            # Kullanıcıya özel kalıcı problem.
            if (
                "too many channels" in text
                or "supergroups" in text
            ):

                logger.warning(
                    "YENİ İSTEK KULLANICI LİMİTİ "
                    "NEDENİYLE ONAYLANAMADI | "
                    "user_id=%s | %s",
                    user.id,
                    e,
                )

                return

            logger.error(
                "YENİ İSTEK ONAYLANAMADI | "
                "user_id=%s | %s",
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

    if not await is_bot_admin(
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

    # =====================================================
    # YENİ GELENLERİ HEMEN OTOMATİK ONAYLA
    # =====================================================

    approval_enabled = True

    status = await update.effective_message.reply_text(
        "🚀 TOPLU ONAY BAŞLADI\n\n"
        "⏳ Bekleyen istekler alınıyor...\n"
        "📦 İşlem paketleri: 1.000 kişi\n"
        "🟢 Yeni gelenler otomatik onaylanacak."
    )

    try:

        result = await approve_all_pending()

        if result.get(
            "already_running"
        ):

            await status.edit_text(
                "⏳ Toplu onay zaten çalışıyor.\n\n"
                "🟢 Otomatik onay aktif."
            )

            return

        total = result["total"]

        success = result["success"]

        failed = result["failed"]

        blocked = result["blocked"]

        already_done = result[
            "already_done"
        ]

        remaining = result.get(
            "remaining"
        )

        if total == 0:

            await status.edit_text(
                "🟢 OTOMATİK ONAY AKTİF\n\n"
                "📭 Bekleyen katılma isteği bulunamadı.\n\n"
                "Yeni gelenler otomatik olarak onaylanacak."
            )

            return

        remaining_text = (
            f"{remaining:,}"
            if remaining is not None
            else "kontrol edilemedi"
        )

        await status.edit_text(
            "✅ TOPLU ONAY İŞLEMİ TAMAMLANDI\n\n"
            f"👥 Bulunan: {total:,}\n"
            f"✅ Onaylanan: {success:,}\n"
            f"↪️ Zaten tamamlanan: {already_done:,}\n"
            f"⚠️ Kullanıcı kanal limiti: {blocked:,}\n"
            f"❌ Diğer başarısız: {failed:,}\n"
            f"📭 Telegram'da kalan: {remaining_text}\n\n"
            "📦 Paket boyutu: 1.000\n"
            f"⚡ Paralel işlem: {CONCURRENCY}\n\n"
            "🟢 Yeni gelen otomatik onay AKTİF."
        )

    except Exception as e:

        logger.exception(
            "TOPLU ONAY KOMUT HATASI"
        )

        # Hata olsa bile yeni gelenlerin otomatik
        # onaylanması açık kalır.
        approval_enabled = True

        await status.edit_text(
            "❌ TOPLU ONAY DURDU\n\n"
            f"{str(e)[:700]}\n\n"
            "🟢 Yeni gelen otomatik onay AKTİF."
        )


# =========================================================
# /DURDUR
# =========================================================

async def durdur(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global approval_enabled

    if not await is_bot_admin(
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

    if not await is_bot_admin(
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
        f"📦 Batch: {BATCH_SIZE:,}\n"
        f"🚀 Paralel: {CONCURRENCY}\n"
        f"🎯 Hedef: @{TARGET_GROUP}"
    )


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application: Application,
):

    await application.bot.set_my_commands([
        BotCommand(
            "onayla",
            "Bekleyenleri toplu onayla",
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
        BATCH_SIZE,
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
