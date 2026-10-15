"""
Telegram Ovoz Bot
1) Matn -> Ovoz (TTS: fish-audio/s2.1-pro-free via OpenRouter)
2) Ovoz -> Matn (STT: lokal Whisper / faster-whisper)
Thinking: Llama 3.3 70B (OpenRouter)
"""

import os
import asyncio
import logging
import re
import uuid
import html
from pathlib import Path
from dotenv import load_dotenv
import imageio_ffmpeg

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BufferedInputFile,
)
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiohttp

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_THINKING_KEY = os.getenv("OPENROUTER_THINKING_KEY")
GEMINI_API_KEY = (
    os.getenv("MIYYA_API_KEY")
    or os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
)

# --- Konfiguratsiya ---
TTS_API_URL = "https://openrouter.ai/api/v1/audio/speech"
TTS_MODEL = "fish-audio/s2.1-pro-free:free"
THINKING_API_URL = "https://openrouter.ai/api/v1/chat/completions"
THINKING_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODEL = "gemini-3.5-flash-lite"
MAX_VOICE_SECONDS = 300


class Flow(StatesGroup):
    choosing_mode = State()
    waiting_tts_text = State()
    waiting_grandpa_text = State()
    waiting_stt = State()
    waiting_script = State()
    ai_chat = State()


UZBEK_VOICES = {
    "🧔 Erkak (Rasmiy)": "44ad146afad04e4e99a7b98b8f97106f",
    "👨 Erkak (Tabiiy)": "fef226d2978a42d5b542e7577134f7b2",
}

FEMALE_VOICES = {
    "👩 Sarah (Yosh, jonli)": "933563129e564b19a115bedd57b7406a",
    "👩‍🦰 Laura (Ishonchli)": "e3cd384158934cc9a01029cd7d278634",
    "🧘 Selene (Sokin, meditativ)": "b347db033a6549378b48d00acb0d06cd",
    "👩 Hannah (Suhbatdosh)": "412a7bebb63345b6becf50f9da63ed5b",
}

MALE_VOICES = {
    "🧑 Ethan (Tushuntiruvchi)": "2ce3de29febb43d7a4a85a63085e1da1",
    "🧔 Adrian (Barqaror)": "7d44c98f619c4e98823a84ed60122f9f",
}

CHILD_VOICES = {
    "🧒 Bolacha (Neutal)": "4e407b98cb034f74b3863ec33c567c83",
    "👶 Kichkina bola": "051eccd6d8894155a0c544b8e5c0fd72",
    "👦 O'g'il bola": "501f126749464eef92d30fa913da1e8e",
    "🧒 Yosh bola (Playful)": "c0d6e010f2bb4198b98b78fb796e1b36",
}

TREND_VOICES = {
    "👴 Grandpa Spencer (Trend)": "245aac5cae7640d88a0e81da89c1e0f3",
    "👴 Grandpa (Hikoyachi)": "b0f79e4bac084142a2ca8266bd959ae5",
    "👴 Grandpa (Faktlar, PSA)": "697a707402bf47e9940b95a5f85fbe94",
}

ALL_VOICES = {}
ALL_VOICES.update(UZBEK_VOICES)
ALL_VOICES.update(FEMALE_VOICES)
ALL_VOICES.update(MALE_VOICES)
ALL_VOICES.update(CHILD_VOICES)
ALL_VOICES.update(TREND_VOICES)

VOICE_CATEGORIES = {
    "🇺🇿 O'zbek ovozlari": UZBEK_VOICES,
    "👴 Trend ovozlari": TREND_VOICES,
    "👩 Ayol ovozlari": FEMALE_VOICES,
    "🧔 Erkak ovozlari": MALE_VOICES,
    "🧒 Bola ovozlari": CHILD_VOICES,
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
user_data = {}

DEFAULT_VOICE = UZBEK_VOICES["👨 Erkak (Tabiiy)"]
NATURAL_UZBEK_VOICE_NAME = "👨 Erkak (Tabiiy)"

# Natural Uz TTS config
NATURAL_TTS_ENABLED = os.getenv("NATURAL_TTS_ENABLED", "1") == "1"
NATURAL_TTS_SPEED = float(os.getenv("NATURAL_TTS_SPEED", "0.97"))
NATURAL_TTS_PITCH = float(os.getenv("NATURAL_TTS_PITCH", "0.98"))


def mode_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="1️⃣ Matn → Ovoz"),
                KeyboardButton(text="2️⃣ Ovoz → Matn"),
            ],
            [
                KeyboardButton(text="👴 Grandpa effekti"),
                KeyboardButton(text="📜 Senariy"),
            ],
            [
                KeyboardButton(text="🤖 AI Yordamchi"),
                KeyboardButton(text="⚙️ Ovozlar"),
            ],
            [
                KeyboardButton(text="ℹ️ Yordam"),
            ],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Birini tanlang...",
    )


def voice_category_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for cat_name in VOICE_CATEGORIES:
        buttons.append([InlineKeyboardButton(text=cat_name, callback_data=f"cat:{cat_name}")])
    buttons.append([InlineKeyboardButton(text="🔙 Bosh menyu", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def voice_list_keyboard(category: str) -> InlineKeyboardMarkup:
    voices = VOICE_CATEGORIES.get(category, {})
    buttons = []
    for name, voice_id in voices.items():
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"vsel:{voice_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_cats")])
    buttons.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def restart_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔄 Boshidan boshlash"), KeyboardButton(text="🏠 Bosh menyu")]],
        resize_keyboard=True,
    )


NATURAL_UZBEK_TTS_GUIDE = (
    "Siz O'zbekistonlik ravon nutq va tabiiy talaffuz yordamchisisiz.\n"
    "Vazifangiz: matnni uzoq davr O'zbek tilida, tabiiy, yengil va yaxshi talaffuz bilan yozish.\n"
    "Qoidalar:\n"
    "1. O'zbekcha so'zlar va iboralarni tabiiy gapga keltiring.\n"
    "2. Pauzalar va intonatsiya bilan ravon o'qish uchun gaplarni ko'proq nutqga yaqin qilib yozing.\n"
    "3. Qisqartmalarni oching va sonlarni o'qiladigan holatga keltiring: 2025 -> 'ikki ming yigirma besh'.\n"
    "4. Shaxsiy va rasmiy nutq orasida muvozanat qiling, lekin ishlatayotgan matnni qiyin emas, ravon va oson aytiladigan qilib qiling.\n"
    "5. Barcha so'zlar O'zbek tilida bo'lsin, xar-xor rasmiy tilga emas.\n"
    "6. Faqat tozalangan, tabiiy nutq matnini qaytaring."
)


def normalize_uzbek_text_for_tts(text: str) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    cleaned = cleaned.replace("```", " ")
    cleaned = re.sub(r"\*\*|__|#", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.replace("\n", " ")
    return cleaned.strip()


def ssml_wrap_uz(text: str) -> str:
    t = html.escape(normalize_uzbek_text_for_tts(text))
    return (
        f"<speak>"
        f"<lang xml:lang='uz-UZ'>"
        f"<prosody rate='{NATURAL_TTS_SPEED*100:.0f}%' pitch='{(NATURAL_TTS_PITCH-1)*100:.0f}%'>"
        f"{t}"
        f"</prosody>"
        f"</lang>"
        f"</speak>"
    )


def build_tts_payload(text: str, voice_id: str | None = None) -> dict:
    input_value = normalize_uzbek_text_for_tts(text)
    if NATURAL_TTS_ENABLED:
        input_value = ssml_wrap_uz(text)
    payload = {
        "model": TTS_MODEL,
        "input": input_value,
        "response_format": "mp3",
        "provider": {
            "options": {
                "fish-audio": {
                    "normalize": True,
                    "latency": "normal",
                    "prosody": {
                        "speed": NATURAL_TTS_SPEED,
                        "pitch": NATURAL_TTS_PITCH,
                        "normalize_loudness": True,
                    },
                }
            }
        },
    }
    if NATURAL_TTS_ENABLED:
        payload["input_format"] = "ssml"
    if voice_id and voice_id != "default":
        payload["voice"] = voice_id
    return payload


ROLE_LINE_REGEX = re.compile(r'^\s*([A-Za-zА-Яа-я0-9_ \-\.]+)\s*[:\-]\s*(.+)$')


def looks_like_scenario(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    if any(k in low for k in ("senariy", "scenario", "scene", "dialog", "sahna")):
        return True
    for line in text.splitlines():
        if ROLE_LINE_REGEX.match(line):
            return True
    if "\n" in text and len(text.splitlines()) >= 2:
        return True
    return False


def parse_scenario_roles(text: str) -> dict:
    roles = {}
    for line in text.splitlines():
        m = ROLE_LINE_REGEX.match(line)
        if m:
            role = m.group(1).strip()
            utter = m.group(2).strip()
            roles.setdefault(role, []).append(utter)
    if roles:
        return roles

    blocks = re.split(r'\n{2,}', text.strip())
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        if len(lines) >= 2 and len(lines[0].split()) <= 4:
            role = lines[0]
            utter = " ".join(lines[1:])
            roles.setdefault(role, []).append(utter)
        else:
            roles.setdefault("Narrator", []).append(" ".join(lines))
    return roles


def choose_voice_for_role(role: str) -> tuple[str, str]:
    r = (role or "").lower()
    male_keywords = ("ota", "dad", "dada", "aka", "uk", "erkak", "bobo", "otam", "o'g'il")
    female_keywords = ("ona", "mama", "qiz", "ayol", "opa", "opam", "qizim")
    child_keywords = ("bola", "bolacha", "kichkina", "yosh")

    if any(k in r for k in male_keywords) and MALE_VOICES:
        name, vid = next(iter(MALE_VOICES.items()))
        return vid, name
    if any(k in r for k in female_keywords) and FEMALE_VOICES:
        name, vid = next(iter(FEMALE_VOICES.items()))
        return vid, name
    if any(k in r for k in child_keywords) and CHILD_VOICES:
        name, vid = next(iter(CHILD_VOICES.items()))
        return vid, name

    for src in (UZBEK_VOICES, FEMALE_VOICES, MALE_VOICES, TREND_VOICES, CHILD_VOICES):
        if src:
            name, vid = next(iter(src.items()))
            return vid, name
    if ALL_VOICES:
        name, vid = next(iter(ALL_VOICES.items()))
        return vid, name
    return DEFAULT_VOICE, NATURAL_UZBEK_VOICE_NAME


async def generate_scenario_voice_files(script_text: str, user_id: int) -> list | None:
    roles = parse_scenario_roles(script_text)
    if not roles:
        return None

    temp_dir = Path(__file__).parent / "tmp" / f"scenario_{user_id}_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    created = []

    for role, speeches in roles.items():
        combined = " ".join(speeches)
        safe_role = re.sub(r'[^A-Za-z0-9_\-]', '_', role)[:32]
        filename = temp_dir / f"{safe_role}.mp3"

        try:
            improved = await improve_prompt(combined, mode="tts")
            voice_id, voice_name = choose_voice_for_role(role)
            audio_bytes = await text_to_speech(improved, voice_id)
            if audio_bytes:
                filename.write_bytes(audio_bytes)
                created.append({"role": role, "file": str(filename), "voice_name": voice_name})
        except Exception as e:
            logger.error(f"Scenario TTS error for {role}: {e}")
            continue

    return created


async def text_to_speech(text: str, voice_id: str | None = None) -> bytes | None:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = build_tts_payload(text, voice_id)

    async with aiohttp.ClientSession() as session:
        async with session.post(
            TTS_API_URL, headers=headers, json=payload,
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"TTS API xatosi {resp.status}: {error_text}")
                if "provider" in error_text or "voice" in error_text:
                    payload.pop("provider", None)
                    payload.pop("voice", None)
                    async with session.post(
                        TTS_API_URL, headers=headers, json=payload,
                        timeout=aiohttp.ClientTimeout(total=180),
                    ) as retry_resp:
                        if retry_resp.status != 200:
                            return None
                        return await retry_resp.read()
                return None
            return await resp.read()


FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
TMP_DIR = Path(__file__).parent / "tmp"
TMP_DIR.mkdir(exist_ok=True)

GRANDPA_SETTINGS = {
    "pitch": 0.82,
    "tempo": 0.97,
    "low_boost_db": 3,
    "high_cut_db": -3,
    "clip_threshold": 0.8,
    "denoise_strength": 10,
    "loudnorm_I": -16,
}


async def apply_grandpa_effect(audio_bytes: bytes) -> bytes | None:
    s = GRANDPA_SETTINGS
    filter_str = (
        f"rubberband=pitch={s['pitch']}:tempo={s['tempo']},"
        f"equalizer=f=180:t=q:w=1.5:g={s['low_boost_db']},"
        f"equalizer=f=3500:t=q:w=2:g={s['high_cut_db']},"
        f"asoftclip=type=tanh:threshold={s['clip_threshold']},"
        f"afftdn=nr={s['denoise_strength']}:nf=-30,"
        f"loudnorm=I={s['loudnorm_I']}:TP=-1.5:LRA=11"
    )
    uid = uuid.uuid4().hex[:12]
    in_path = TMP_DIR / f"gp_in_{uid}.mp3"
    out_path = TMP_DIR / f"gp_out_{uid}.mp3"
    try:
        in_path.write_bytes(audio_bytes)
        cmd = [
            FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(in_path),
            "-af", filter_str,
            "-b:a", "128k",
            str(out_path),
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(f"Grandpa FFmpeg xatosi: {stderr.decode(errors='replace')[:300]}")
            return None
        if not out_path.exists() or out_path.stat().st_size == 0:
            logger.error("Grandpa effekti bo'sh fayl qaytardi")
            return None
        return out_path.read_bytes()
    except Exception as e:
        logger.error(f"Grandpa effekti xatosi: {e}")
        return None
    finally:
        for p in (in_path, out_path):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass


async def improve_prompt(text: str, mode: str = "tts") -> str:
    if mode == "stt":
        system_msg = (
            "Siz ovozdan matnga aylantirilgan matnni tozalaysiz.\n"
            "Vazifangiz:\n"
            "1. Punktuatsiyani qo'shing (nuqta, vergul, savol belgisi).\n"
            "2. O'zbekcha matn bo'lsa, imlosini tuzating: o', g', so'ngra, nima uchun, ndefa, xuddi, albatta.\n"
            "3. Transkripsiya xato so'zlarini kontekstdan kelib chiqib tuzating.\n"
            "4. Ma'noni hech qanday o'zgartirmang, qo'shimcha so'z qo'shmang.\n"
            "5. Matn boshqa tilda bo'lsa, o'sha tilda tozalang.\n\n"
            "Faqat tozalangan matnni qaytaring, boshqa hech narsa yozmang."
        )
    else:
        system_msg = (
            "Siz matnni ravon, tabiiy va O'zbek tilida yaxshi talaffuz bo'ladigan ovozli nutqqa tayyorlash yordamchisisiz.\n"
            "Vazifangiz:\n"
            "1. O'zbekcha matn bo'lsa, uni tabiiy, 30 yillik O'zbekistonlik nutqiga yaqin ravonlikda yozing.\n"
            "2. Qisqartmalarni oching, sonlarni gapdagi nutqqa mos ravishda ayting.\n"
            "3. So'zlarni juda ham tez yoki qiyin qilib emas, tabiiy va ravon qilib qiling.\n"
            "4. Punktuatsiyani to'g'rilang: nuqta, vergul, savol belgisi, pauza uchun.\n"
            "5. O'zbekcha iboralardan o'rta tezlikda, oson va ravon nutq hosil qilishni maqsad qiling.\n"
            "6. Yangi matnni hayotiy, natural, ijtimoiy va ravon qilib yozing.\n\n"
            "Ma'noni o'zgartirmang. Faqat tayyor matnni qaytaring."
        )

    headers = {"Authorization": f"Bearer {OPENROUTER_THINKING_KEY}", "Content-Type": "application/json"}
    payload = {"model": THINKING_MODEL, "messages": [{"role": "system", "content": system_msg}, {"role": "user", "content": text}], "max_tokens": 1000, "reasoning": {"enabled": False}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(THINKING_API_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    return text
                data = await resp.json()
                content = data["choices"][0]["message"].get("content")
                if not content:
                    logger.warning("Thinking model bo'sh javob qaytardi")
                    return text
                for junk in ("```", "`", "**", "__", "#"):
                    content = content.replace(junk, "")
                return content.strip() if content.strip() else text
    except Exception as e:
        logger.error(f"Thinking xatosi: {e}")
        return text


async def gemini_chat(user_message: str, system_prompt: str | None = None) -> str | None:
    if not GEMINI_API_KEY:
        logger.warning("GEMINI API kaliti topilmadi (MIYYA/GEMINI/GOOGLE).")
        return None
    url = f"{GEMINI_BASE_URL}/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": user_message}]}]}
    if system_prompt:
        payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Gemini API xatosi {resp.status}: {error_text[:200]}")
                    return None
                data = await resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return None
                parts = candidates[0].get("content", {}).get("parts", [])
                for p in parts:
                    if "text" in p:
                        return p["text"].strip()
                return None
    except Exception as e:
        logger.error(f"Gemini chat xatosi: {e}")
        return None


async def gemini_analyze_script(script_text: str) -> str | None:
    system_prompt = (
        "Sen professional senariy tahlilchisisiz.\n"
        "Foydalanuvchi senariy yuboradi. Sen quyidagilarni qil:\n"
        "1. Senariy tuzilishini ajrat (boshlash, rivojlanish, yakun).\n"
        "2. Qahramonlarni sanab chiq (ism, xarakter).\n"
        "3. Asosiy g'oyani bir jumla bilan ayting.\n"
        "4. Ovoz yozish uchun tavsiyalar bering (ohang, temp).\n\n"
        "Juda qisqa va aniq javob bering (500 belgidan kam)."
    )
    return await gemini_chat(script_text, system_prompt)


async def gemini_bot_help(user_message: str) -> str | None:
    system_prompt = (
        "Sen Telegram ovoz boti yordamchisisiz.\n"
        "Bot imkoniyatlari:\n"
        "1. Matn → Ovoz: matnni ovozga aylantiradi (Fish Audio TTS)\n"
        "2. Ovoz → Matn: ovozli xabarni matnga aylantiradi (Gemini STT)\n"
        "3. Grandpa effekti: matnni keksa odam ovozida aylantiradi\n"
        "4. Turli ovozlar: o'zbek, ayol, erkak, bola, trend ovozlari\n"
        "5. AI Yordamchi: sizga savollarga javob beradi\n\n"
        "O'zbek tilida qisqa va tushunarli javob bering.\n"
        "Agar foydalanuvchi ovoz yaratish haqida so'rasa, /start buyrug'ini maslahat bering."
    )
    return await gemini_chat(user_message, system_prompt)


async def gemini_stt(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str | None:
    import base64
    if not GEMINI_API_KEY:
        logger.warning("GEMINI API kaliti topilmadi (MIYYA/GEMINI/GOOGLE).")
        return None
    url = f"{GEMINI_BASE_URL}/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"inline_data": {"mime_type": mime_type, "data": base64.b64encode(audio_bytes).decode("utf-8")}}, {"text": "Bu ovozli xabar. Undagi so'zlarni aniq transkripsiya qiling. Faqat transkripsiya matnini qaytaring."}]}]}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Gemini STT xatosi {resp.status}: {error_text[:200]}")
                    return None
                data = await resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return None
                parts = candidates[0].get("content", {}).get("parts", [])
                for p in parts:
                    if "text" in p:
                        return p["text"].strip()
                return None
    except Exception as e:
        logger.error(f"Gemini STT xatosi: {e}")
        return None


dp = Dispatcher(storage=MemoryStorage())


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    user_data[uid] = {"voice": DEFAULT_VOICE}
    await state.clear()
    await message.answer(
        "🎙️ <b>Ovoz Bot'ga xush kelibsiz!</b>\n\n"
        "Men siz bilan gaplashaman va yordam beraman.\n"
        "Sizga nima kerak? Iltimos qisqacha yozing yoki ssenariy yuboring.\n\n"
        "Agar ssenariy yuborsangiz, men uni tushunib, rollar bo'yicha alohida ovozlar tayyorlab beraman.",
        parse_mode=ParseMode.HTML,
    )
    await message.answer("Misol format:\nOta: Salom, qizim.\nQiz: Bugun yaxshi kun.\nYoki shunchaki ssenariy matnini yuboring.")
    await message.answer("Nima qilishingizni yozing yoki tugmalardan birini tanlang:", reply_markup=mode_keyboard())
    await state.set_state(Flow.choosing_mode)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "ℹ️ <b>Yo'riqnoma</b>\n\n"
        "1️⃣ /start — boshlash, asosiy menyu\n"
        "2️⃣ Rejimni tanlang:\n"
        "   • 1️⃣ Matn → Ovoz (matn yozasiz, ovoz olasiz)\n"
        "   • 2️⃣ Ovoz → Matn (ovoz yuborasiz, matn olasiz)\n"
        "   • 3️⃣ Senariy (har bir rol uchun alohida ovoz)\n\n"
        "⚠️ Maksimal matn: ~3000 belgi\n"
        "⚠️ Maksimal ovoz: 5 daqiqa",
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("cat:"))
async def category_selected(callback: CallbackQuery):
    category = callback.data[4:]
    await callback.message.edit_text(f"🎙️ <b>{category}</b> — ovozni tanlang:", parse_mode=ParseMode.HTML, reply_markup=voice_list_keyboard(category))
    await callback.answer()


@dp.callback_query(F.data == "back_to_cats")
async def back_to_cats(callback: CallbackQuery):
    await callback.message.edit_text("🎙️ <b>Ovoz kategoriyasini tanlang:</b>", parse_mode=ParseMode.HTML, reply_markup=voice_category_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    try:
        await callback.message.edit_text("🏠 <b>Bosh menyu</b> — quyidagi tugmalardan birini tanlang:", parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.message.answer("🏠 Bosh menyu:", reply_markup=mode_keyboard())
    await callback.answer()


@dp.callback_query(F.data.startswith("vsel:"))
async def voice_selected(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    voice_id = callback.data.split(":", 1)[1]
    if uid not in user_data:
        user_data[uid] = {}
    user_data[uid]["voice"] = voice_id
    voice_name = "Default"
    for name, vid in ALL_VOICES.items():
        if vid == voice_id:
            voice_name = name
            break
    await callback.message.edit_text(f"✅ Ovoz tanlandi: <b>{voice_name}</b>\n\nEndi rejani tanlang:", parse_mode=ParseMode.HTML)
    await callback.answer()
    await callback.message.answer("📋 <b>Qaysi rejimda ishlaysiz?</b>\n\n1️⃣ <b>Matn → Ovoz</b>\n    Matn yozaman, ovozli xabar olaman\n\n2️⃣ <b>Ovoz → Matn</b>\n    Ovoz yuboraman, matn olaman", parse_mode=ParseMode.HTML, reply_markup=mode_keyboard())
    await state.set_state(Flow.choosing_mode)


@dp.message(F.text == "🔄 Boshidan boshlash")
async def restart(message: Message, state: FSMContext):
    uid = message.from_user.id
    user_data[uid] = {"voice": DEFAULT_VOICE}
    await state.clear()
    await message.answer("🔄 <b>Qaytadan boshlaymiz!</b>\n\nAvval <b>rejimni tanlang</b> yoki <b>ovozlarni sozlang</b>:", parse_mode=ParseMode.HTML, reply_markup=mode_keyboard())


@dp.message(F.text)
async def handle_free_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        return
    current = await state.get_state()
    if current in (Flow.waiting_tts_text.state, Flow.waiting_grandpa_text.state, Flow.waiting_stt.state, Flow.waiting_script.state, Flow.ai_chat.state, Flow.choosing_mode.state):
        return
    if looks_like_scenario(text):
        uid = message.from_user.id
        processing = await message.answer("📜 Senariy tahlil qilinmoqda...")
        try:
            files = await generate_scenario_voice_files(text, uid)
            if not files:
                await processing.edit_text("❌ Senariy aniqlanmadi. Iltimos, formatni tekshiring.")
                return
            await processing.edit_text(f"✅ Senariy tushunildi. {len(files)} ta ovoz fayli tayyorlandi.")
            for entry in files:
                role = entry["role"]
                path = entry["file"]
                voice_name = entry["voice_name"]
                await message.answer(f"🔊 Rol: <b>{role}</b> — Ovoz: <i>{voice_name}</i>", parse_mode=ParseMode.HTML)
                await message.answer_voice(voice=BufferedInputFile(Path(path).read_bytes(), filename=Path(path).name))
            await message.answer("Yana boshqa vazifa bormi?", reply_markup=restart_keyboard())
        except Exception as e:
            logger.error(f"Scenario error: {e}")
            await processing.edit_text(f"❌ Xatolik: {str(e)[:200]}")
        return
    await message.answer("Menga nima qilishni xohlaysiz? Masalan: 'matnni ovozga aylantir', 'ssenariy yuboraman', yoki 'bot haqida gapir'.", reply_markup=mode_keyboard())


@dp.message(Flow.choosing_mode)
async def mode_chosen(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if "Matn → Ovoz" in text or "1️⃣" in text:
        await message.answer("📝 <b>Matn → Ovoz rejimi</b>\n\nMatn yozing — men uni ovozga aylantirib beraman:", parse_mode=ParseMode.HTML)
        await state.set_state(Flow.waiting_tts_text)
    elif "Ovoz → Matn" in text or "2️⃣" in text:
        await message.answer("🎙️ <b>Ovoz → Matn rejimi</b>\n\nEndi <b>ovozli xabar</b> yuboring (mikrofon tugmasi) —\nmen uni matnga aylantirib beraman.\n\nTilni avtomatik aniqlayman 🌍", parse_mode=ParseMode.HTML)
        await state.set_state(Flow.waiting_stt)
    elif "Grandpa" in text or "👴" in text:
        await message.answer("👴 <b>Grandpa Effekti</b>\n\nMatn yozing — men uni <b>keksa odam ovozida</b> aylantirib beraman:\n• Pastroq va tabiiy tembr\n• Biroz dag'al, xirillagan\n• Nutq tushunarli va ravon\n\nBoshqa ovoz rejimi uchun /start bosing.", parse_mode=ParseMode.HTML)
        await state.set_state(Flow.waiting_grandpa_text)
    elif "AI Yordamchi" in text or "🤖" in text:
        if not GEMINI_API_KEY:
            await message.answer("⚠️ AI Yordamchi hozir faol emas — serverda Gemini kaliti topilmadi.")
            return
        await message.answer("🤖 <b>AI Yordamchi</b>\n\nMen sizga yordam bera olaman! Savolingizni yozing:\n• Bot haqida savollar\n• Ovoz yaratish bo'yicha maslahat\n• Har qanday umumiy savol\n\nChiqish uchun /start bosing.", parse_mode=ParseMode.HTML)
        await state.set_state(Flow.ai_chat)
    elif "Senariy" in text or "📜" in text:
        await message.answer("📜 <b>Senariy</b>\n\nSenariy matnini yuboring — men uni tahlil qilaman\nva har bir qahramon uchun alohida ovozlar tayyorlab beraman.\n\nMisol: Ota: ...\nQiz: ...", parse_mode=ParseMode.HTML)
        await state.set_state(Flow.waiting_script)
    elif text == "⚙️ Ovozlar":
        await message.answer("🎙️ Ovozlarni tanlang:", reply_markup=voice_category_keyboard())
    elif text == "ℹ️ Yordam":
        await cmd_help(message)
    else:
        await message.answer("⚠️ Iltimos, tugmalardan birini tanlang:", reply_markup=mode_keyboard())


@dp.message(Flow.waiting_tts_text)
async def tts_text_handler(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    uid = message.from_user.id
    if not text:
        await message.answer("⚠️ Iltimos, matn yozing.")
        return
    if len(text) > 3000:
        await message.answer("⚠️ Matn juda uzun! 3000 belgidan qisqaroq yozing.")
        return
    voice_id = user_data.get(uid, {}).get("voice", DEFAULT_VOICE)
    processing_msg = await message.answer("🧠 Matn tahlil qilinmoqda...")
    try:
        improved_text = await improve_prompt(text, mode="tts")
        logger.info(f"TTS matn: {improved_text[:80]}...")
        await processing_msg.edit_text("⏳ Ovoz yaratilmoqda...")
        audio_bytes = await text_to_speech(improved_text, voice_id)
        if audio_bytes is None:
            await processing_msg.edit_text("❌ Xatolik yuz berdi. Qayta urinib ko'ring.")
            return
        audio_file = BufferedInputFile(audio_bytes, filename="voice.mp3")
        await message.answer_voice(voice=audio_file, caption=f"🔊 {len(text)} belgi | Ovoz: {NATURAL_UZBEK_VOICE_NAME}")
        await processing_msg.delete()
        await message.answer("🔄 Yana ishlash uchun yoki boshqa rejimga o'tish:", reply_markup=restart_keyboard())
    except Exception as e:
        logger.error(f"TTS xatosi: {e}")
        await processing_msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")


@dp.message(Flow.waiting_grandpa_text)
async def grandpa_text_handler(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    uid = message.from_user.id
    if not text:
        await message.answer("⚠️ Iltimos, matn yozing.")
        return
    if len(text) > 2000:
        await message.answer("⚠️ Matn juda uzun! 2000 belgidan qisqaroq yozing.")
        return
    voice_id = user_data.get(uid, {}).get("voice", DEFAULT_VOICE)
    processing_msg = await message.answer("🧠 Matn tahlil qilinmoqda...")
    try:
        improved_text = await improve_prompt(text, mode="tts")
        logger.info(f"Grandpa matn: {improved_text[:80]}...")
        await processing_msg.edit_text("⏳ Ovoz yaratilmoqda...")
        audio_bytes = await text_to_speech(improved_text, voice_id)
        if audio_bytes is None:
            await processing_msg.edit_text("❌ Ovoz yaratishda xatolik. Qayta urinib ko'ring.")
            return
        await processing_msg.edit_text("👴 Grandpa effekti qo'llanmoqda...")
        processed_bytes = await apply_grandpa_effect(audio_bytes)
        if processed_bytes is None:
            logger.warning("Grandpa effekti ishlamadi, oddiy ovoz yuboriladi")
            processed_bytes = audio_bytes
        audio_file = BufferedInputFile(processed_bytes, filename="grandpa_voice.mp3")
        await message.answer_voice(voice=audio_file, caption=f"👴 Grandpa effekti | {len(text)} belgi")
        await processing_msg.delete()
        await message.answer("🔄 Yana ishlash uchun yoki boshqa rejimga o'tish:", reply_markup=restart_keyboard())
    except Exception as e:
        logger.error(f"Grandpa xatosi: {e}")
        await processing_msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")


@dp.message(Flow.ai_chat)
async def ai_chat_handler(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ Iltimos, savolingizni yozing.")
        return
    if not GEMINI_API_KEY:
        await message.answer("⚠️ AI Yordamchi hozir faol emas — serverda Gemini kaliti topilmadi.")
        return
    processing_msg = await message.answer("🤖 Javob tayyorlanmoqda...")
    try:
        response = await gemini_bot_help(text)
        if response:
            if len(response) > 4000:
                response = response[:4000] + "..."
            await processing_msg.edit_text(response)
        else:
            await processing_msg.edit_text("❌ Javob olishda xatolik. Qayta urinib ko'ring.")
    except Exception as e:
        logger.error(f"AI chat xatosi: {e}")
        await processing_msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")


@dp.message(Flow.waiting_script)
async def script_analysis_handler(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ Iltimos, senariy matnini yuboring.")
        return
    if len(text) < 50:
        await message.answer("⚠️ Senariy juda qisqa. Kamida 50 belgi yuboring.")
        return
    if len(text) > 10000:
        await message.answer("⚠️ Senariy juda uzun! 10000 belgidan qisqaroq yuboring.")
        return
    processing_msg = await message.answer("📜 Senariy tahlil qilinmoqda...")
    try:
        analysis = await gemini_analyze_script(text)
        if analysis:
            if len(analysis) > 4000:
                analysis = analysis[:4000] + "..."
            await processing_msg.edit_text(f"📜 <b>Senariy Tahlili:</b>\n\n{analysis}", parse_mode=ParseMode.HTML)
        else:
            await processing_msg.edit_text("❌ Tahlilda xatolik. Qayta urinib ko'ring.")
    except Exception as e:
        logger.error(f"Senariy tahlili xatosi: {e}")
        await processing_msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")


@dp.message(Flow.waiting_stt, F.voice | F.audio)
async def stt_voice_handler(message: Message, state: FSMContext):
    tg_file = message.voice or message.audio
    duration = tg_file.duration or 0
    if duration > MAX_VOICE_SECONDS:
        await message.answer(f"⚠️ Ovoz juda uzun ({duration}s). Maksimal {MAX_VOICE_SECONDS} soniya.")
        return
    processing_msg = await message.answer("🧠 Ovoz tahlil qilinmoqda...\n⏳ Bir necha soniya kuting.")
    tmp_path = None
    try:
        file = await message.bot.get_file(tg_file.file_id)
        tmp_dir = os.path.join(os.getcwd(), "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        ext = "ogg" if message.voice else "mp3"
        tmp_path = os.path.join(tmp_dir, f"{message.from_user.id}_{tg_file.file_id}.{ext}")
        await message.bot.download_file(file.file_path, destination=tmp_path)
        logger.info(f"STT: fayl yuklandi {tmp_path} ({duration}s)")
        await processing_msg.edit_text("⏳ Matnga aylantirilmoqda...")
        with open(tmp_path, "rb") as f:
            audio_bytes = f.read()
        mime_type = "audio/ogg" if message.voice else "audio/mpeg"
        raw_text = await gemini_stt(audio_bytes, mime_type)
        if not raw_text:
            await processing_msg.edit_text("❌ Ovozdan matn topilmadi.\nIltimos, aniqroq va yaqinroq gapiring.")
            return
        logger.info(f"STT natija: {raw_text[:100]}")
        await processing_msg.edit_text("🧹 Matn tozalanmoqda...")
        cleaned = await improve_prompt(raw_text, mode="stt")
        await processing_msg.edit_text(f"✅ <b>Tayyor!</b>\n\n📝 {cleaned}", parse_mode=ParseMode.HTML)
        await message.answer("🔄 Yana ishlash uchun yoki boshqa rejimga o'tish:", reply_markup=restart_keyboard())
    except Exception as e:
        logger.error(f"STT xatosi: {e}")
        await processing_msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@dp.message(Flow.waiting_stt)
async def stt_wrong_type(message: Message):
    await message.answer("⚠️ Iltimos, <b>ovozli xabar</b> yuboring (mikrofon tugmasi bosib ushlab turing).", parse_mode=ParseMode.HTML)


async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN topilmadi! .env faylni tekshiring.")
        return
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY topilmadi! .env faylni tekshiring.")
        return
    if not GEMINI_API_KEY:
        logger.warning("GEMINI API kaliti topilmadi — AI Yordamchi ishlamaydi.")
    bot = Bot(token=BOT_TOKEN)
    logger.info("Bot ishga tushdi!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
