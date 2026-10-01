import os
import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError


# =========================================================
# AYARLAR
# =========================================================

BOT_TOKEN = '8206822443:AAGAYgUT5FJ85nPam-Mxoz6D1cBsFAkmyBY'

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

GROUP = "@novaprimesohbet"

# Railway üzerinde botun sürekli çalışması için
SESSION_NAME = "nova_join_approver"


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger("nova_join_approver")


# =========================================================
# PYROGRAM CLIENT
# =========================================================

app = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


# =========================================================
# YENİ GELEN İSTEKLERİ OTOMATİK ONAYLA
# =========================================================

@app.on_chat_join_request()
async def new_join_request(client, request):

    try:

        chat_id = request.chat.id
        user_id = request.from_user.id

        logger.info(
            "Yeni katılma isteği: %s | %s",
            user_id,
            request.from_user.username
        )

        await client.approve_chat_join_request(
            chat_id=chat_id,
            user_id=user_id
        )

        logger.info(
            "Katılma isteği onaylandı: %s",
            user_id
        )

    except FloodWait as e:

        logger.warning(
            "FloodWait: %s saniye bekleniyor.",
            e.value
        )

        await asyncio.sleep(e.value)

        try:
            await client.approve_chat_join_request(
                chat_id=request.chat.id,
                user_id=request.from_user.id
            )

            logger.info(
                "Bekleme sonrası istek onaylandı: %s",
                request.from_user.id
            )

        except Exception as retry_error:

            logger.error(
                "Tekrar denemede hata: %s",
                retry_error
            )

    except RPCError as e:

        logger.error(
            "Telegram RPC hatası: %s",
            e
        )

    except Exception as e:

        logger.exception(
            "Beklenmeyen hata: %s",
            e
        )


# =========================================================
# MEVCUT TÜM BEKLEYEN İSTEKLERİ ONAYLA
# =========================================================

async def approve_existing_requests():

    logger.info("=" * 60)
    logger.info("MEVCUT BEKLEYEN İSTEKLER KONTROL EDİLİYOR")
    logger.info("GRUP: %s", GROUP)
    logger.info("=" * 60)

    try:

        result = await app.approve_all_chat_join_requests(
            GROUP
        )

        if result:

            logger.info(
                "MEVCUT BEKLEYEN İSTEKLERİN TOPLU ONAYI BAŞLATILDI."
            )

        else:

            logger.info(
                "Toplu onay işlemi başarısız veya sonuç alınamadı."
            )

    except FloodWait as e:

        logger.warning(
            "Telegram FloodWait: %s saniye.",
            e.value
        )

        await asyncio.sleep(e.value)

        try:

            result = await app.approve_all_chat_join_requests(
                GROUP
            )

            logger.info(
                "FloodWait sonrası toplu onay sonucu: %s",
                result
            )

        except Exception as retry_error:

            logger.exception(
                "FloodWait sonrası hata: %s",
                retry_error
            )

    except RPCError as e:

        logger.error(
            "Telegram RPC hatası: %s",
            e
        )

    except Exception as e:

        logger.exception(
            "Mevcut istekler işlenirken hata oluştu: %s",
            e
        )


# =========================================================
# BAŞLANGIÇ
# =========================================================

async def main():

    logger.info("=" * 60)
    logger.info("NOVA PRIME KATILMA İSTEĞİ BOTU BAŞLIYOR")
    logger.info("GRUP: %s", GROUP)
    logger.info("=" * 60)

    if not BOT_TOKEN or BOT_TOKEN == "BURAYA_BOT_TOKENINI_YAZ":
        raise RuntimeError(
            "BOT_TOKEN bulunamadı. Railway Variables bölümüne BOT_TOKEN ekle."
        )

    if API_ID == 0:
        raise RuntimeError(
            "API_ID bulunamadı."
        )

    if not API_HASH:
        raise RuntimeError(
            "API_HASH bulunamadı."
        )

    await app.start()

    logger.info("Bot Telegram'a bağlandı.")

    # Botun grubu görebildiğini kontrol et
    try:

        chat = await app.get_chat(GROUP)

        logger.info(
            "Grup bulundu: %s | ID: %s",
            chat.title,
            chat.id
        )

    except Exception as e:

        logger.exception(
            "Gruba erişilemedi: %s",
            e
        )

        await app.stop()
        raise

    # Önceden bekleyen istekleri onayla
    await approve_existing_requests()

    logger.info("=" * 60)
    logger.info("BOT AKTİF.")
    logger.info("YENİ GELEN İSTEKLER OTOMATİK ONAYLANACAK.")
    logger.info("=" * 60)

    # Sonsuza kadar çalış
    await asyncio.Event().wait()


# =========================================================
# ÇALIŞTIR
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info("Bot durduruldu.")

    except Exception as e:

        logger.exception(
            "BOT DURDU: %s",
            e
        )
