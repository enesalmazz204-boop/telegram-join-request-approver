import os
import json
import re
import html
import logging
import asyncio
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatJoinRequestHandler,
    ContextTypes, filters
)

# ============================================================
# AYARLAR
# ============================================================
BOT_TOKEN = '8206822443:AAFttaam4zgd5n9q44byqR9mqJkpipoYHUw'

# Bu hesap yönetici olarak sabit kabul edilir:
# @heroprimemarketing
_admin_usernames_raw = os.getenv("ADMIN_USERNAMES", "").strip()
ADMIN_USERNAMES = {
    x.strip().lstrip("@").lower()
    for x in (_admin_usernames_raw.split(",") if _admin_usernames_raw else ["heroprimemarketing"])
    if x.strip()
}
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

DATA_FILE = Path(os.getenv("DATA_FILE", "bot_data.json"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============================================================
# VERİ
# Her site kendi komutu + görsel + metin + buton + URL bilgisine sahiptir.
# ============================================================
DEFAULT_SITES = [
    {"name": "JASİNO 2.000 TL", "command": "jasino", "image_id": None,
     "text": "JASİNO", "button_text": "JASİNO Kayıt Ol", "url": "https://jasino.to/4ZD8"},
    {"name": "GALYABET 2.000 TL", "command": "galyabet", "image_id": None,
     "text": "GALYABET", "button_text": "GALYABET Kayıt Ol", "url": "https://t.ly/4HEqX"},
    {"name": "MİLANBAHİS 500 TL", "command": "milanbahis", "image_id": None,
     "text": "MİLANBAHİS", "button_text": "MİLANBAHİS Kayıt Ol", "url": "https://kisal.site/heroprime"},
    {"name": "BETWINNER", "command": "betwinner", "image_id": None,
     "text": "BETWINNER", "button_text": "BETWINNER Kayıt Ol", "url": "https://bwref-l4ftkntp.com/1Px4?p=%2Fregistration%2F"},
    {"name": "BİZBET", "command": "bizbet", "image_id": None,
     "text": "BİZBET", "button_text": "BİZBET Kayıt Ol", "url": "https://refpa-0768.com/L?tag=d_2106249m_62079c_&site=2106249&ad=62079&r=registration/"},
    {"name": "HEROPRIME WEB", "command": "heroprime", "image_id": None,
     "text": "HEROPRIME WEB", "button_text": "HeroPrime Web", "url": "https://heroprime68.com/"},
]

DEFAULT_DATA = {"sites": DEFAULT_SITES, "commands": {}}


def load_data():
    if not DATA_FILE.exists():
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA, ensure_ascii=False))

    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        logger.exception("Veri dosyası okunamadı.")
        data = json.loads(json.dumps(DEFAULT_DATA, ensure_ascii=False))

    data.setdefault("sites", [])
    data.setdefault("commands", {})

    # Eski sürümdeki siteleri yeni yapıya otomatik tamamla.
    for site in data["sites"]:
        site.setdefault("command", clean_command_name(site.get("name", "")) or "site")
        site.setdefault("image_id", None)
        site.setdefault("text", site.get("name", ""))
        site.setdefault("button_text", site.get("name", "Kayıt Ol"))
        site.setdefault("url", "")

    return data


def save_data(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(DATA_FILE)


def normalize_text(value):
    value = (value or "").strip().lower()
    replacements = {"ı":"i","ş":"s","ğ":"g","ü":"u","ö":"o","ç":"c"}
    for a, b in replacements.items():
        value = value.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", value)


def clean_command_name(value):
    value = (value or "").strip().lower()
    if value.startswith(("!", ".")):
        value = value[1:]
    value = value.split()[0] if value.split() else ""
    value = normalize_text(value)
    if not value or not re.fullmatch(r"[a-z0-9_]+", value):
        return None
    return value[:50]


def clean_site_name(value):
    return re.sub(r"\s+", " ", (value or "").strip())[:100]


def is_admin(update):
    user = update.effective_user
    if not user:
        return False
    if user.id in ADMIN_IDS:
        return True
    return (user.username or "").lower() in ADMIN_USERNAMES


async def require_admin(update):
    if is_admin(update):
        return True
    if update.message:
        await update.message.reply_text("❌ Bu panel sadece bot yöneticilerine açıktır.")
    elif update.callback_query:
        await update.callback_query.answer("❌ Yetkin yok.", show_alert=True)
    return False


DATA = load_data()


def site_by_name(name):
    wanted = normalize_text(name)
    return next((s for s in DATA["sites"] if normalize_text(s["name"]) == wanted), None)


def site_by_command(command):
    wanted = clean_command_name(command)
    return next((s for s in DATA["sites"] if s.get("command") == wanted), None)


def command_by_name(name):
    return DATA["commands"].get(clean_command_name(name) or "")


# ============================================================
# ADMIN PANEL
# ============================================================
def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Site Ekle", callback_data="admin:add_site"),
         InlineKeyboardButton("🗑️ Site Sil", callback_data="admin:delete_site")],
        [InlineKeyboardButton("✏️ Site Düzenle", callback_data="admin:edit_site")],
        [InlineKeyboardButton("📝 Metin Düzenle", callback_data="admin:edit_text"),
         InlineKeyboardButton("🖼️ Görsel Düzenle", callback_data="admin:edit_image")],
        [InlineKeyboardButton("🔘 Buton Düzenle", callback_data="admin:edit_button")],
        [InlineKeyboardButton("➕ Komut Ekle", callback_data="admin:add_command"),
         InlineKeyboardButton("🗑️ Komut Sil", callback_data="admin:delete_command")],
        [InlineKeyboardButton("📋 Mevcut Siteleri Gör", callback_data="admin:list_sites")],
        [InlineKeyboardButton("📋 Mevcut Komutları Gör", callback_data="admin:list_commands")],
    ])


def cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal", callback_data="admin:cancel")]])


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Yönetim Paneli", callback_data="admin:home")]])


async def start_command(update, context):
    if update.effective_chat.type != "private":
        return
    if not await require_admin(update):
        return
    context.user_data.clear()
    await update.message.reply_text(
        "🎛️ <b>HEROPRIME YÖNETİM PANELİ</b>\n\n"
        "➕ Site Ekle ile tek akışta site adı → komut → görsel → metin → buton adı → buton URL'si ekleyebilirsin.\n\n"
        "Grup kullanımı: <code>!raconbet</code> direkt Raconbet içeriğini açar.\n<code>!site</code> site butonlarını gösterir; butona basınca aynı mesaj seçilen siteye dönüşür.",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )


async def admin_callback(update, context):
    q = update.callback_query
    if not is_admin(update):
        await q.answer("❌ Yetkin yok.", show_alert=True)
        return
    await q.answer()
    action = q.data

    if action == "admin:home":
        context.user_data.clear()
        await q.edit_message_text(
            "🎛️ <b>HEROPRIME YÖNETİM PANELİ</b>",
            parse_mode="HTML", reply_markup=admin_keyboard()
        )
        return

    if action == "admin:cancel":
        context.user_data.clear()
        await q.edit_message_text("❌ İşlem iptal edildi.", reply_markup=admin_keyboard())
        return

    if action == "admin:add_site":
        context.user_data.clear()
        context.user_data["admin_action"] = "add_site_name"
        await q.edit_message_text(
            "➕ <b>Site Ekle</b>\n\n1️⃣ Site ismini gönder.\nÖrnek: <code>Raconbet</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "admin:delete_site":
        context.user_data.clear()
        context.user_data["admin_action"] = "delete_site"
        await q.edit_message_text(
            "🗑️ <b>Site Sil</b>\n\nSilmek istediğin sitenin adını gönder.",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "admin:edit_site":
        context.user_data.clear()
        context.user_data["admin_action"] = "edit_site_select"
        await q.edit_message_text(
            "✏️ <b>Site Düzenle</b>\n\nDüzenlemek istediğin sitenin adını gönder.",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action in ("admin:edit_text", "admin:edit_image", "admin:edit_button"):
        context.user_data.clear()
        context.user_data["admin_action"] = {
            "admin:edit_text": "edit_text_select",
            "admin:edit_image": "edit_image_select",
            "admin:edit_button": "edit_button_select",
        }[action]
        prompts = {
            "admin:edit_text": "📝 Metnini değiştirmek istediğin site adını gönder.",
            "admin:edit_image": "🖼️ Görselini değiştirmek istediğin site adını gönder.",
            "admin:edit_button": "🔘 Butonunu değiştirmek istediğin site adını gönder.",
        }
        await q.edit_message_text(
            prompts[action],
            reply_markup=cancel_keyboard()
        )
        return

    if action == "admin:add_command":
        context.user_data.clear()
        context.user_data["admin_action"] = "add_command_name"
        await q.edit_message_text(
            "➕ <b>Bağımsız Komut Ekle</b>\n\nKomutu gönder. Örnek: <code>!kampanya</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "admin:delete_command":
        context.user_data.clear()
        context.user_data["admin_action"] = "delete_command"
        await q.edit_message_text(
            "🗑️ Silinecek bağımsız komutu gönder. Örnek: <code>!kampanya</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "admin:list_sites":
        if not DATA["sites"]:
            text = "📭 Site yok."
        else:
            lines = ["📋 <b>SİTELER</b>\n"]
            for i, s in enumerate(DATA["sites"], 1):
                lines.append(
                    f"{i}. <b>{html.escape(s['name'])}</b>\n"
                    f"   Komut: <code>!{html.escape(s.get('command',''))}</code>\n"
                    f"   Görsel: {'✅' if s.get('image_id') else '❌'}\n"
                    f"   Buton: <b>{html.escape(s.get('button_text',''))}</b>"
                )
            text = "\n\n".join(lines)
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=back_keyboard())
        return

    if action == "admin:list_commands":
        if not DATA["commands"]:
            text = "📭 Bağımsız komut yok."
        else:
            text = "📋 <b>BAĞIMSIZ KOMUTLAR</b>\n\n" + "\n\n".join(
                f"• <code>!{html.escape(k)}</code>\n{html.escape(v.get('text','')[:150])}"
                for k, v in DATA["commands"].items()
            )
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=back_keyboard())


# ============================================================
# SITE EKLE: TEK AKIŞ
# ============================================================
async def admin_private_text(update, context):
    if update.effective_chat.type != "private" or not is_admin(update):
        return

    action = context.user_data.get("admin_action")
    if not action:
        return

    value = (update.message.text or "").strip()

    if action == "add_site_name":
        name = clean_site_name(value)
        if not name:
            await update.message.reply_text("❌ Geçerli bir site adı gönder.")
            return
        if site_by_name(name):
            await update.message.reply_text("❌ Bu site zaten var. Farklı bir isim gönder.")
            return
        context.user_data.update({"new_site_name": name, "admin_action": "add_site_command"})
        await update.message.reply_text(
            f"✅ Site: <b>{html.escape(name)}</b>\n\n"
            "2️⃣ Şimdi komutu gönder.\nÖrnek: <code>!raconbet</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "add_site_command":
        cmd = clean_command_name(value)
        if not cmd:
            await update.message.reply_text("❌ Geçerli bir komut gönder. Örnek: !raconbet")
            return
        if site_by_command(cmd) or cmd in DATA["commands"]:
            await update.message.reply_text("❌ Bu komut zaten kullanılıyor. Başka bir komut gönder.")
            return
        context.user_data.update({"new_site_command": cmd, "admin_action": "add_site_image"})
        await update.message.reply_text(
            f"✅ Komut: <code>!{cmd}</code>\n\n"
            "3️⃣ Şimdi site görselini gönder.\n"
            "Fotoğrafı doğrudan buraya gönder.",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "add_site_text":
        context.user_data["new_site_text"] = value
        context.user_data["admin_action"] = "add_site_button_text"
        await update.message.reply_text(
            "4️⃣ Metin kaydedildi.\n\n"
            "5️⃣ Şimdi buton üzerinde yazacak adı gönder.\n"
            "Örnek: <code>Raconbet Kayıt Ol</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "add_site_button_text":
        if not value:
            await update.message.reply_text("❌ Buton adı boş olamaz.")
            return
        context.user_data["new_site_button_text"] = value[:100]
        context.user_data["admin_action"] = "add_site_url"
        await update.message.reply_text(
            "6️⃣ Son adım: Butonun gideceği URL'yi gönder.\n"
            "Örnek: <code>https://ornek.com/kayit</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "add_site_url":
        if not re.match(r"^https?://", value, re.I):
            await update.message.reply_text("❌ URL http:// veya https:// ile başlamalı.")
            return

        site = {
            "name": context.user_data["new_site_name"],
            "command": context.user_data["new_site_command"],
            "image_id": context.user_data.get("new_site_image"),
            "text": context.user_data.get("new_site_text", ""),
            "button_text": context.user_data["new_site_button_text"],
            "url": value,
        }
        DATA["sites"].append(site)
        save_data(DATA)
        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>Site tamamen kaydedildi!</b>\n\n"
            f"🌐 {html.escape(site['name'])}\n"
            f"⌨️ <code>!{html.escape(site['command'])}</code>\n"
            f"🖼️ Görsel: {'✅' if site['image_id'] else '❌'}\n"
            f"🔘 {html.escape(site['button_text'])}\n"
            f"🔗 {html.escape(site['url'])}\n\n"
            "Grupta bu komut kullanıldığında sadece bu site içeriği gönderilecek.",
            parse_mode="HTML", reply_markup=admin_keyboard()
        )
        return

    # --------------------------------------------------------
    # SİTE SİL
    # --------------------------------------------------------
    if action == "delete_site":
        site = site_by_name(value)
        if not site:
            await update.message.reply_text("❌ Bu isimde site bulunamadı.")
            return
        DATA["sites"].remove(site)
        save_data(DATA)
        context.user_data.clear()
        await update.message.reply_text(
            f"🗑️ <b>{html.escape(site['name'])}</b> silindi.",
            parse_mode="HTML", reply_markup=admin_keyboard()
        )
        return

    # --------------------------------------------------------
    # SİTE DÜZENLE
    # --------------------------------------------------------
    if action == "edit_site_select":
        site = site_by_name(value)
        if not site:
            await update.message.reply_text("❌ Site bulunamadı.")
            return
        context.user_data["edit_site_command"] = site["command"]
        context.user_data["admin_action"] = "edit_site_name"
        await update.message.reply_text(
            f"✏️ Mevcut: <b>{html.escape(site['name'])}</b>\n\nYeni site adını gönder.",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "edit_site_name":
        site = site_by_command(context.user_data["edit_site_command"])
        name = clean_site_name(value)
        if not site or not name:
            await update.message.reply_text("❌ Geçersiz bilgi.")
            return
        site["name"] = name
        save_data(DATA)
        context.user_data.clear()
        await update.message.reply_text("✅ Site adı güncellendi.", reply_markup=admin_keyboard())
        return

    # --------------------------------------------------------
    # GÖRSEL DÜZENLE
    # --------------------------------------------------------
    if action == "edit_image_select":
        site = site_by_name(value)
        if not site:
            await update.message.reply_text("❌ Site bulunamadı.")
            return
        context.user_data["edit_site_command"] = site["command"]
        context.user_data["admin_action"] = "edit_image_value"
        await update.message.reply_text(
            f"🖼️ <b>{html.escape(site['name'])}</b> için yeni görseli gönder.",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        return

    # --------------------------------------------------------
    # METİN DÜZENLE
    # --------------------------------------------------------
    if action == "edit_text_select":
        site = site_by_name(value)
        if not site:
            await update.message.reply_text("❌ Site bulunamadı.")
            return
        context.user_data["edit_site_command"] = site["command"]
        context.user_data["admin_action"] = "edit_text_value"
        await update.message.reply_text(
            "Yeni metni gönder. Bu metin görselin altında görünecek.",
            reply_markup=cancel_keyboard()
        )
        return

    if action == "edit_text_value":
        site = site_by_command(context.user_data["edit_site_command"])
        if not site:
            await update.message.reply_text("❌ Site bulunamadı.")
            return
        site["text"] = value
        save_data(DATA)
        context.user_data.clear()
        await update.message.reply_text("✅ Metin güncellendi.", reply_markup=admin_keyboard())
        return

    # --------------------------------------------------------
    # BUTON DÜZENLE
    # --------------------------------------------------------
    if action == "edit_button_select":
        site = site_by_name(value)
        if not site:
            await update.message.reply_text("❌ Site bulunamadı.")
            return
        context.user_data["edit_site_command"] = site["command"]
        context.user_data["admin_action"] = "edit_button_name"
        await update.message.reply_text(
            f"Mevcut buton: <b>{html.escape(site.get('button_text',''))}</b>\n\n"
            "Yeni buton adını gönder.",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "edit_button_name":
        context.user_data["edit_button_text"] = value[:100]
        context.user_data["admin_action"] = "edit_button_url"
        await update.message.reply_text("Yeni buton URL'sini gönder.", reply_markup=cancel_keyboard())
        return

    if action == "edit_button_url":
        if not re.match(r"^https?://", value, re.I):
            await update.message.reply_text("❌ URL http:// veya https:// ile başlamalı.")
            return
        site = site_by_command(context.user_data["edit_site_command"])
        if not site:
            await update.message.reply_text("❌ Site bulunamadı.")
            return
        site["button_text"] = context.user_data["edit_button_text"]
        site["url"] = value
        save_data(DATA)
        context.user_data.clear()
        await update.message.reply_text("✅ Buton ve URL güncellendi.", reply_markup=admin_keyboard())
        return

    # --------------------------------------------------------
    # BAĞIMSIZ KOMUT EKLE / SİL
    # --------------------------------------------------------
    if action == "add_command_name":
        name = clean_command_name(value)
        if not name:
            await update.message.reply_text("❌ Geçerli bir komut gönder.")
            return
        if name in DATA["commands"] or site_by_command(name):
            await update.message.reply_text("❌ Bu komut zaten kullanılıyor.")
            return
        context.user_data.update({"new_command": name, "admin_action": "add_command_text"})
        await update.message.reply_text(
            f"✅ <code>!{name}</code>\n\nŞimdi bu komutun metnini gönder.",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    if action == "add_command_text":
        name = context.user_data["new_command"]
        DATA["commands"][name] = {"text": value, "image_id": None}
        save_data(DATA)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ <code>!{name}</code> oluşturuldu.",
            parse_mode="HTML", reply_markup=admin_keyboard()
        )
        return

    if action == "delete_command":
        name = clean_command_name(value)
        if not name or name not in DATA["commands"]:
            await update.message.reply_text("❌ Böyle bir bağımsız komut yok.")
            return
        del DATA["commands"][name]
        save_data(DATA)
        context.user_data.clear()
        await update.message.reply_text(
            f"🗑️ <code>!{name}</code> silindi.",
            parse_mode="HTML", reply_markup=admin_keyboard()
        )
        return


# ============================================================
# SITE EKLE - GÖRSEL ADIMI
# ============================================================
async def admin_receive_photo(update, context):
    if update.effective_chat.type != "private" or not is_admin(update):
        return
    if context.user_data.get("admin_action") != "add_site_image":
        return
    if not update.message or not update.message.photo:
        return

    photo = update.message.photo[-1]

    if context.user_data.get("admin_action") == "edit_image_value":
        site = site_by_command(context.user_data.get("edit_site_command", ""))
        if not site:
            await update.message.reply_text("❌ Site bulunamadı.")
            context.user_data.clear()
            return

        site["image_id"] = photo.file_id
        save_data(DATA)
        context.user_data.clear()

        await update.message.reply_text(
            f"✅ <b>{html.escape(site['name'])}</b> görseli güncellendi.",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
        return

    context.user_data["new_site_image"] = photo.file_id
    context.user_data["admin_action"] = "add_site_text"

    await update.message.reply_text(
        "🖼️ Görsel kaydedildi.\n\n"
        "4️⃣ Şimdi görselin altında çıkacak metni gönder.\n"
        "Örneğin kampanya açıklaması, bonus bilgisi vb.",
        reply_markup=cancel_keyboard()
    )


# ============================================================
# PUBLIC SITE GÖNDERİMİ
# ============================================================
def site_markup(site):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🔘 {site.get('button_text') or 'Kayıt Ol'}",
            url=site["url"]
        )]
    ])


async def send_single_site(message, site):
    text = site.get("text") or site.get("name", "")
    text = html.escape(text).replace("\n", "\n")
    markup = site_markup(site)

    if site.get("image_id"):
        try:
            await message.reply_photo(
                photo=site["image_id"],
                caption=text,
                parse_mode="HTML",
                reply_markup=markup
            )
            return
        except Exception:
            logger.exception("Site görseli gönderilemedi; metin olarak devam ediliyor.")

    await message.reply_text(text, parse_mode="HTML", reply_markup=markup)


def all_sites_keyboard():
    rows = []
    for site in DATA["sites"]:
        rows.append([
            InlineKeyboardButton(
                f"💎 {site['name']}",
                callback_data=f"site:{site['command']}"
            )
        ])
    return InlineKeyboardMarkup(rows) if rows else None


async def send_site_menu(message):
    if not DATA["sites"]:
        await message.reply_text("📭 Henüz site eklenmemiş.")
        return
    await message.reply_text(
        "🌐 <b>HEROPRIME SİTELER</b>\n\n"
        "Aşağıdaki butonlardan istediğin siteyi seç:",
        parse_mode="HTML",
        reply_markup=all_sites_keyboard()
    )


async def edit_site_menu_to_site(query, site):
    """
    !site menüsünde bir siteye basıldığında SADECE bağlantı onayı gösterir.
    Görsel/metin burada gösterilmez.

    Örnek:
      !site -> site butonları
      Raconbet -> "Bu bağlantıyı açmak ister misin?" + Raconbet bağlantı butonu

    Direkt !raconbet komutu ise public_text_commands() içinden
    send_single_site() çağırdığı için görsel + metin + linki göstermeye devam eder.
    """
    button_text = site.get("button_text") or f"{site.get('name', 'Site')} bağlantısını aç"

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"🔗 {button_text}",
                url=site.get("url", "")
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Site Listesine Dön",
                callback_data="site_menu:back"
            )
        ]
    ])

    site_name = html.escape(site.get("name", "Site"))

    await query.edit_message_text(
        f"🔗 <b>{site_name}</b>\n\n"
        f"Bu bağlantıyı açmak ister misin?",
        parse_mode="HTML",
        reply_markup=markup
    )


# ============================================================
# PUBLIC KOMUTLAR
# !raconbet -> direkt Raconbet içeriği
# !site -> sitelerin buton menüsü; tıklanınca sadece bağlantı onayı
# !site Raconbet -> isimli kullanım desteklenir ve direkt Raconbet içeriği
# ============================================================
async def public_text_commands(update, context):
    if not update.message or not update.message.text:
        return
    if update.effective_chat.type not in ("group", "supergroup"):
        return

    raw = update.message.text.strip()

    m = re.fullmatch(r"[!.]site(?:\s+(.+))?", raw, re.I)
    if m:
        name = (m.group(1) or "").strip()
        if name:
            site = site_by_name(name)
            if site:
                await send_single_site(update.message, site)
            else:
                await update.message.reply_text("❌ Bu isimde site bulunamadı.")
        else:
            await send_site_menu(update.message)
        return

    m = re.fullmatch(r"[!.]([A-Za-z0-9_]+)", raw)
    if not m:
        return

    command = clean_command_name(m.group(1))
    if not command:
        return

    # Önce site komutlarına bak.
    site = site_by_command(command)
    if site:
        await send_single_site(update.message, site)
        return

    # Sonra bağımsız komutlara bak.
    item = DATA["commands"].get(command)
    if item:
        text = html.escape(item.get("text", ""))
        if item.get("image_id"):
            try:
                await update.message.reply_photo(
                    photo=item["image_id"], caption=text,
                    parse_mode="HTML"
                )
                return
            except Exception:
                logger.exception("Komut görseli gönderilemedi.")
        await update.message.reply_text(text or "ℹ️ Bu komut için içerik yok.", parse_mode="HTML")


async def public_callback(update, context):
    q = update.callback_query
    await q.answer()

    if q.data == "site_menu:back":
        if DATA["sites"]:
            await q.edit_message_text(
                "🌐 <b>HEROPRIME SİTELER</b>\n\n"
                "Aşağıdaki butonlardan istediğin siteyi seç:",
                parse_mode="HTML",
                reply_markup=all_sites_keyboard()
            )
        else:
            await q.edit_message_text("📭 Henüz site eklenmemiş.")
        return

    if q.data.startswith("site:"):
        command = q.data.split(":", 1)[1]
        site = site_by_command(command)
        if site:
            await edit_site_menu_to_site(q, site)


# ============================================================
# /myid
# ============================================================
async def myid_command(update, context):
    if not update.message or not update.effective_user:
        return
    user = update.effective_user
    username = f"@{user.username}" if user.username else "(kullanıcı adı yok)"
    await update.message.reply_text(
        f"🆔 Telegram ID: <code>{user.id}</code>\n"
        f"👤 Kullanıcı adı: <code>{html.escape(username)}</code>",
        parse_mode="HTML"
    )


# ============================================================
# KATILMA İSTEKLERİ - OTOMATİK TOPLU ONAY
# ============================================================
# Telegram Bot API bekleyen istekleri tek bir "bulk approve" endpoint'i
# ile vermez. Ancak gelen join-request güncellemelerini aynı anda
# işleyerek kullanıcıların tek tek beklemesini engelleyebiliriz.
# Birden fazla istek aynı anda geldiğinde approvals paralel çalışır.
TARGET_GROUP_USERNAME = os.getenv("TARGET_GROUP_USERNAME", "novaprimesohbet").lstrip("@").lower()
JOIN_APPROVAL_CONCURRENCY = max(1, int(os.getenv("JOIN_APPROVAL_CONCURRENCY", "20")))
_join_approval_semaphore = asyncio.Semaphore(JOIN_APPROVAL_CONCURRENCY)


async def auto_approve_join_request(update, context):
    request = update.chat_join_request
    if not request:
        return

    chat = request.chat
    username = (chat.username or "").lstrip("@").lower()

    # Sadece hedef grubun katılma isteklerini otomatik onayla.
    if TARGET_GROUP_USERNAME and username != TARGET_GROUP_USERNAME:
        logger.info(
            "Katılma isteği hedef grup dışında geldi: @%s; işlem yapılmadı.",
            username or "username-yok"
        )
        return

    user = request.from_user
    display_name = user.full_name or user.username or str(user.id)

    async with _join_approval_semaphore:
        try:
            await context.bot.approve_chat_join_request(
                chat_id=chat.id,
                user_id=user.id,
            )
            logger.info(
                "KATILMA İSTEĞİ ONAYLANDI: %s (ID: %s) -> @%s",
                display_name, user.id, username or str(chat.id)
            )
        except Exception as exc:
            logger.error(
                "Katılma isteği onaylanamadı: %s (ID: %s), grup=%s, hata=%s",
                display_name, user.id, username or str(chat.id), exc,
                exc_info=True,
            )


# ============================================================
# MODERASYON
# ============================================================
BAD_WORDS = {"ornek1", "ornek2", "ornek3"}


def contains_bad_word(text):
    normalized = normalize_text(text)
    return any(normalize_text(w) in normalized for w in BAD_WORDS)


async def moderation_handler(update, context):
    if not update.message or update.effective_chat.type not in ("group", "supergroup"):
        return
    if not update.effective_user or update.effective_user.is_bot:
        return

    text = update.message.text or update.message.caption or ""
    if not text or not contains_bad_word(text):
        return

    try:
        member = await update.effective_chat.get_member(update.effective_user.id)
        if member.status in ("administrator", "creator"):
            return
    except Exception:
        return

    try:
        await update.message.delete()
    except Exception:
        return

    try:
        warning = await update.effective_chat.send_message(
            "⚠️ Uygunsuz/küfürlü mesaj silindi."
        )
        context.job_queue.run_once(
            lambda c: c.bot.delete_message(
                chat_id=warning.chat_id, message_id=warning.message_id
            ),
            10,
        )
    except Exception:
        pass


# ============================================================
# MAIN
# ============================================================
async def post_init(application):
    """Polling başlamadan önce varsa eski webhook'u temizler."""
    try:
        await application.bot.delete_webhook(drop_pending_updates=False)
        logger.info("Webhook temizlendi; polling başlatılıyor.")
    except Exception:
        logger.exception("Webhook temizlenirken hata oluştu.")

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN bulunamadı. Railway Variables içine BOT_TOKEN ekle."
        )

    logger.info("Admin kullanıcı adları: %s", sorted(ADMIN_USERNAMES))
    logger.info("Admin ID'leri: %s", sorted(ADMIN_IDS))

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("myid", myid_command))

    # Telegram'dan gelen tüm yeni grup katılma isteklerini otomatik onayla.
    # Semaphore sayesinde yoğun trafikte de kontrollü biçimde paralel çalışır.
    app.add_handler(
        ChatJoinRequestHandler(auto_approve_join_request)
    )

    app.add_handler(
        CallbackQueryHandler(admin_callback, pattern=r"^admin:")
    )
    app.add_handler(
        CallbackQueryHandler(public_callback, pattern=r"^site:")
    )

    # Admin özelden fotoğraf
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.PHOTO,
            admin_receive_photo
        )
    )

    # Admin özelden metin
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            admin_private_text
        )
    )

    # Gruptaki site/komutlar
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            public_text_commands
        )
    )

    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.Caption()),
            moderation_handler
        ),
        group=1
    )

    logger.info("HEROPRIME bot çalışıyor. Admin: @heroprimemarketing")
    app.run_polling(
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
