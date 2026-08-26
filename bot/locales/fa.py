"""Persian strings. Keys must stay in sync with en.py."""

# ── general ───────────────────────────────────────────────────────────
WELCOME_MESSAGE = (
    "{i_brand} <b>مدیا دانلودر</b>\n"
    "{div}\n"
    "سلام <b>{user_name}</b> عزیز 👋\n\n"
    "هر لینکی بدی، سریع و باکیفیت برات دانلود می‌کنم. 🚀\n"
    "فقط کافیه لینکشو بفرستی — همین!\n\n"
    "📋 <b>پلتفرم‌های پشتیبانی‌شده</b>\n"
    "{platforms}\n\n"
    "<blockquote>💡 چند لینک رو می‌تونی توی یک پیام با هم بفرستی. لینک تکراری فوری از حافظه میاد.</blockquote>"
)
LANG_SELECT_MESSAGE = "🌐 زبان خودت رو انتخاب کن:\n\nPlease choose your language:"
LANG_SELECTED_FA = "✅ زبان فارسی انتخاب شد!"
LANG_SELECTED_EN = "✅ English selected!"

SUPPORTED_LIST = (
    "📸 اینستاگرام — ریلز، پست، استوری، پروفایل، کروسل\n"
    "🎬 یوتیوب — ویدیو، شورتز، انتخاب کیفیت، MP3\n"
    "📱 تیک‌تاک — بدون واترمارک\n"
    "🐦 توییتر / X — ویدیو، همه‌ی عکس‌ها، متن توییت\n"
    "🎵 اسپاتیفای — آهنگ با کاور\n"
    "🎧 ساندکلاد — آهنگ و پادکست\n"
    "📌 پینترست — عکس و ویدیو\n"
    "🔗 لینک مستقیم — هر فایلی"
)

HELP_MESSAGE = (
    "{i_help} <b>راهنمای ربات</b>\n"
    "{div}\n"
    "<b>چطور کار می‌کنه؟</b>\n"
    "۱. لینک رو بفرست — همین!\n"
    "۲. برای یوتیوب کیفیت رو انتخاب کن\n"
    "۳. چند لینک در یک پیام هم قبوله\n\n"
    "⌨️ <b>دستورها</b>\n"
    "/start — شروع و منوی اصلی\n"
    "/me — آمار من\n"
    "/settings — تنظیمات شخصی\n"
    "/favorites — علاقه‌مندی‌ها\n"
    "/invite — دعوت دوستان\n"
    "/lang — تغییر زبان\n"
    "/cancel — لغو عملیات جاری\n\n"
    "📋 <b>پلتفرم‌ها</b>\n{platforms}\n\n"
    "<blockquote>💡 لینک تکراری از حافظه فرستاده می‌شه و فوری می‌رسه.</blockquote>"
)

# ── download flow ─────────────────────────────────────────────────────
PROCESSING = "{i_search} <b>در حال بررسی لینک</b>\n<code>{bar_2}</code>"
QUEUED = "{i_queue} <b>در صف دانلود</b>\n<code>{bar_1}</code>"
DOWNLOADING = "{i_download} <b>در حال دانلود</b>\n<code>{bar_6}</code>"
UPLOADING = "{i_upload} <b>در حال ارسال به تلگرام</b>\n<code>{bar_9}</code>"
FROM_CACHE = "⚡️ از حافظه"

DOWNLOAD_SUCCESS = "✅ دانلود با موفقیت انجام شد!"
DOWNLOAD_FAILED = "❌ خطا در دانلود\n\n<code>{error}</code>\n\n💡 {hint}"
UNSUPPORTED_PLATFORM = (
    "❌ این لینک قابل دانلود نیست.\n\n<b>پلتفرم‌های پشتیبانی‌شده:</b>\n{platforms}"
)
NO_LINK_FOUND = "🔗 لینک بفرست تا دانلودش کنم."
FILE_TOO_LARGE = (
    "⚠️ حجم فایل {size} است و از سقف تلگرام بیشتره.\n\n🔗 <a href=\"{url}\">دانلود مستقیم</a>"
)
TEXT_ONLY_POST = "📝 این پست فقط متن داره:\n\n{text}"

CAPTION_TEMPLATE = "{title}\n\n{meta}"
BATCH_PROGRESS = "📦 لینک {current} از {total}"
BATCH_SUMMARY = "📦 <b>تمام شد:</b> {ok} موفق، {failed} ناموفق از {total} لینک"

PLAYLIST_FOUND = (
    "🎼 <b>{title}</b>\n\n{count} آیتم پیدا شد. همه رو دانلود کنم؟"
)
PLAYLIST_STARTED = "🎼 شروع دانلود {count} آیتم..."
PLAYLIST_ITEM = "🎼 {current}/{total} — {title}"

QUALITY_PROMPT = (
    "🎬 <b>{title}</b>\n"
    "<b>━━━━━━━━━━━━━━━━━</b>\n"
    "👤 {uploader}\n"
    "⏱ {duration}   👁 {views}\n"
    "<b>━━━━━━━━━━━━━━━━━</b>\n"
    "کیفیت مورد نظرت رو انتخاب کن 👇"
)

# ── user stats ────────────────────────────────────────────────────────
USER_STATS = (
    "{i_stats} <b>آمار شما</b>\n"
    "{div}\n"
    "⬇️ دانلودها: <b>{downloads}</b>\n"
    "📅 عضویت: <b>{joined}</b>\n"
    "🌐 زبان: <b>{ulang}</b>"
)

# ── errors / hints ────────────────────────────────────────────────────
ERROR_GENERIC = "❌ یه مشکلی پیش آمد. چند لحظه بعد دوباره امتحان کن."
HINT_PRIVATE = "این پست خصوصی یا حذف شده است."
HINT_GEO = "این محتوا در منطقه‌ی سرور محدود شده."
HINT_RETRY = "چند لحظه بعد دوباره تلاش کن."
HINT_LIVE = "پخش زنده قابل دانلود نیست."
HINT_LOGIN = "این محتوا نیاز به لاگین دارد."
HINT_RESTRICTED = "این پست محدودیت سنی یا محدودیت مخاطب دارد و برای همه قابل دیدن نیست."
HINT_BLOCKED = "این پلتفرم موقتاً درخواست‌های سرور را رد می‌کند. کمی بعد دوباره امتحان کن."

BANNED_MESSAGE = "🚫 دسترسی شما به این ربات محدود شده است."
JOIN_CHANNEL = (
    "<b>🔒 عضویت اجباری</b>\n\n"
    "برای استفاده از ربات، اول توی کانال‌(های) زیر عضو شو،\n"
    "بعد دکمه‌ی <b>«✅ عضو شدم؛ بررسی کن»</b> رو بزن:\n\n"
    "{channel}"
)
JOIN_CHECK_FAILED = "❌ هنوز عضو نشدی. بعد از عضویت دکمه رو بزن."
JOIN_OK = "✅ عضویتت تأیید شد! حالا لینکت رو بفرست 🎬"

# ── admin panel ───────────────────────────────────────────────────────
ADMIN_PANEL = (
    "{i_admin} <b>پنل مدیریت</b>\n"
    "{div}\n"
    "{pulse}\n\n"
    "{i_users} کاربران: <b>{users}</b>  ({i_new} {new_today} امروز)\n"
    "{i_download} دانلودها: <b>{downloads}</b>  ({i_date} {today} امروز)\n"
    "{i_hot} فعال ۲۴ ساعت: <b>{active}</b>\n"
    "{i_cache} کش: <b>{cache_rows}</b> آیتم / {cache_hits} بازدید\n"
    "{i_queue} در حال اجرا: <b>{running}</b>  |  در صف: <b>{waiting}</b>\n"
    "{i_disk} دیسک آزاد: <b>{disk}</b>\n"
    "{i_clock} آپ‌تایم: <b>{uptime}</b>"
)
STATS_TITLE = "📊 <b>آمار کامل</b>"
STATS_USERS = "👥 کاربران: {count}"
STATS_DOWNLOADS = "⬇️ دانلودها: {count}"
STATS_TODAY = "📅 امروز: {count}"
STATS_WEEK = "📅 این هفته: {count}"
STATS_TOP = "🔥 محبوب‌ترین: {platform}"
STATS_ACTIVE = "👤 فعال‌ترین: {name}"
STATS_FAILED = "❌ ناموفق: {count}"
STATS_TRAFFIC = "📦 حجم کل: {size}"
STATS_SUCCESS_RATE = "✅ نرخ موفقیت: {rate}%"

TOP_TITLE = "🔥 <b>پلتفرم‌های محبوب</b>"
TOP_FORMAT = "{rank}. {platform} — {count} دانلود"
HEALTH_TITLE = "🩺 <b>سلامت پلتفرم‌ها (۲۴ ساعت)</b>"
HEALTH_FORMAT = "{emoji} {platform}: {rate}%  ({ok}/{total})"
HEALTH_EMPTY = "هنوز دانلودی در ۲۴ ساعت گذشته ثبت نشده."
ERRORS_TITLE = "🐞 <b>آخرین خطاها</b>"
ERRORS_EMPTY = "✅ خطایی ثبت نشده."

BROADCAST_PROMPT = "📢 پیام رو بفرست (متن، عکس، ویدیو یا فوروارد):"
BROADCAST_CONFIRM = "⚠️ این پیام به <b>{count}</b> کاربر ارسال شود؟"
BROADCAST_SENDING = "📤 در حال ارسال... {current}/{total}"
BROADCAST_SENT = "✅ به {count} کاربر ارسال شد."
BROADCAST_FAILED = "❌ {count} ارسال ناموفق (بلاک کرده‌اند)."
BROADCAST_CANCELLED = "✖️ ارسال همگانی لغو شد."

BAN_PROMPT = "🚫 آیدی عددی کاربر رو بفرست:"
BAN_SUCCESS = "✅ کاربر <code>{id}</code> بن شد."
BAN_SELF = "❌ نمی‌تونی خودت رو بن کنی."
UNBAN_PROMPT = "🔓 آیدی عددی کاربر رو بفرست:"
UNBAN_SUCCESS = "✅ بن کاربر <code>{id}</code> رفع شد."
BAN_LIST_TITLE = "🚫 <b>کاربران بن‌شده</b>"
BAN_LIST_EMPTY = "✅ هیچ کاربری بن نشده."

USER_LOOKUP = (
    "👤 <b>اطلاعات کاربر</b>\n\n"
    "🆔 <code>{id}</code>\n"
    "📛 {name} {username}\n"
    "🌐 زبان: {ulang}\n"
    "⬇️ دانلودها: {downloads}\n"
    "📅 عضویت: {joined}\n"
    "🕐 آخرین فعالیت: {last}\n"
    "🚫 بن: {banned}"
)
USER_NOT_FOUND = "❌ کاربری با این آیدی پیدا نشد."
INVALID_ID = "❌ آیدی نامعتبره. یک عدد بفرست."

CACHE_STATS = (
    "⚡️ <b>وضعیت کش</b>\n\n"
    "📦 آیتم‌ها: {rows}\n"
    "🎯 بازدیدها: {hits}\n"
    "💾 حجم صرفه‌جویی‌شده: {saved}"
)
CACHE_CLEARED = "🗑 {count} آیتم از کش پاک شد."
SETTINGS_TITLE = "⚙️ <b>تنظیمات</b>"
JOIN_STILL_MISSING = "❌ هنوز عضو این‌ها نشدی: {channels}"
CHANNEL_NOT_FOUND = "این کانال در لیست نبود: {channel}"
CHANNEL_REMOVED = "🗑 کانال حذف شد: {channel}"
CHANNEL_EXISTS = "این کانال از قبل در لیست است: {channel}"
CHANNEL_STATUS_OFF = (
    "🔓 <b>عضویت اجباری خاموش است</b>\n\n"
    "برای روشن کردن:\n"
    "<code>/setchannel @channel</code>\n\n"
    "⚠️ ربات باید در کانال <b>ادمین</b> باشد، وگرنه تلگرام اجازه نمی‌دهد "
    "عضویت کاربران خوانده شود."
)
CHANNEL_STATUS_TITLE = "🔒 <b>عضویت اجباری</b>"
CHANNEL_STATUS_BROKEN = "⚠️ کار نمی‌کند — ربات را ادمین کن"
CHANNEL_STATUS_HELP = (
    "<code>/setchannel @channel</code> افزودن\n"
    "<code>/setchannel -@channel</code> حذف\n"
    "<code>/setchannel off</code> خاموش کردن"
)
CHANNEL_ERR_NOT_FOUND = (
    "❌ کانال پیدا نشد: <code>{channel}</code>\n\n"
    "آیدی را درست بفرست، مثل <code>@mychannel</code>"
)
CHANNEL_ERR_NOT_ADMIN = (
    "❌ <b>ربات در این کانال ادمین نیست</b>\n"
    "<code>{channel}</code>\n\n"
    "تلگرام لیست اعضای کانال را فقط به ادمین‌ها می‌دهد، پس بدون ادمین بودن "
    "نمی‌شود فهمید کاربر عضو شده یا نه.\n\n"
    "🔹 ربات را در کانال ادمین کن\n"
    "🔹 لازم نیست هیچ دسترسی‌ای بدهی — همه را خاموش بگذار\n"
    "🔹 بعد دوباره همین دستور را بزن"
)
CHANNEL_ERR_NO_INVITE = (
    "❌ کانال خصوصی است و لینک دعوت ندارد.\n"
    "به ربات دسترسی «دعوت کاربران با لینک» بده، بعد دوباره امتحان کن."
)
CHANNEL_ERR_GENERIC = "❌ خطا برای <code>{channel}</code>\n\n<code>{error}</code>"
CHANNEL_SET = "✅ کانال اجباری تنظیم شد: {channel}"
CHANNEL_OFF = "✅ عضویت اجباری خاموش شد."
WELCOME_SET = "✅ پیام خوشامد تغییر کرد."
EXPORT_READY = "📄 خروجی CSV آماده است ({count} کاربر)."
NO_ACCESS = "❌ شما دسترسی ندارید."
CANCELLED = "✖️ لغو شد."
NOTHING_TO_CANCEL = "چیزی برای لغو نیست."
MAINTENANCE_ON = "🔧 حالت تعمیر روشن شد — فقط ادمین‌ها می‌توانند دانلود کنند."
MAINTENANCE_OFF = "✅ حالت تعمیر خاموش شد."
MAINTENANCE_NOTICE = "🔧 ربات موقتاً در حال تعمیره. چند دقیقه بعد امتحان کن."

# ── buttons ───────────────────────────────────────────────────────────
BTN_ADMIN = "⚙️ پنل ادمین"
BTN_HELP = "📖 راهنما"
BTN_LANGUAGE = "🌐 زبان"
BTN_STATS = "📊 آمار من"
GROUP_WELCOME = (
    "{i_brand} <b>سلام! من ربات دانلودم</b>\n"
    "{div}\n"
    "لینک بفرست، فایلشو می‌فرستم.\n\n"
    "{i_link} <b>پلتفرم‌ها:</b> یوتیوب، اینستاگرام، تیک‌تاک، توییتر/X، اسپاتیفای، ساندکلاد، پینترست، گوگل‌پلی، تلگرام\n"
    "{i_settings} ادمین گروه با /gsettings تنظیمات رو عوض می‌کنه.\n\n"
    "{i_help} پیش‌فرض: فقط وقتی @{bot} رو تگ کنی جواب می‌دم."
)
GROUP_PM_ONLY = "این صفحه شخصیه — توی پیوی ربات بازش کن."
GROUP_START = (
    "{i_brand} <b>ربات دانلود</b>\n"
    "{div}\n"
    "{i_link} لینک بفرست تا فایلشو بدم.\n"
    "{i_download} فایل هم می‌گیرم: کپشن فایل رو تگ کن.\n"
    "{i_help} حالت فعلی: <b>{mode}</b>\n\n"
    "{i_settings} صفحه‌های شخصی (تاریخچه، علاقه‌مندی، آمار) توی پیوی‌اند."
)
GROUP_MODE_TAG = "فقط با تگ کردن من"
GROUP_MODE_AUTO = "هر لینکی رو خودم می‌گیرم"
GROUP_MODE_ADMINS = " · فقط ادمین‌ها"
GBTN_OPEN_PM = "شروع در پیوی"
GBTN_SETTINGS = "تنظیمات گروه"

GROUP_HELP = (
    "{i_help} <b>راهنمای گروه</b>\n"
    "{div}\n"
    "{i_link} لینک بفرست یا رو یه پیام ریپلای کن و منو تگ کن.\n"
    "{i_download} فایل هم می‌گیرم: کپشن فایل رو تگ کن.\n"
    "{i_settings} ادمین گروه: /gsettings"
)
GSETTINGS_TITLE = "{i_settings} <b>تنظیمات گروه</b>"
GSETTINGS_BODY = (
    "{i_admin} گروه: <b>{title}</b>\n"
    "{i_users} اعضا: <b>{members}</b>\n"
    "{i_download} دانلودها: <b>{downloads}</b>\n"
    "{div}\n"
    "{i_link} حالت لینک: <b>{mode}</b>\n"
    "{i_admin} فقط ادمین‌ها: <b>{admins_only}</b>\n"
    "{i_cache} پاکسازی پیام‌ها: <b>{clean}</b>\n"
    "{i_quality} کیفیت پیش‌فرض: <b>{quality}</b>\n"
    "{i_lang} زبان: <b>{lang_name}</b>"
)
GSETTINGS_ONLY_ADMIN = "این دستور فقط برای ادمین‌های گروه است."
GSETTINGS_ONLY_GROUP = "این دستور فقط داخل گروه کار می‌کند."
GMODE_AUTO = "هر لینکی"
GMODE_TAG = "فقط با تگ"
GBTN_MODE = "حالت لینک"
GBTN_ADMINS = "فقط ادمین‌ها"
GBTN_CLEAN = "پاکسازی پیام‌ها"
GBTN_QUALITY = "کیفیت پیش‌فرض"
GBTN_LANG = "زبان گروه"
GQUALITY_ASK = "پرسیدن"
GLANG_USER = "زبان کاربر"
GBTN_ADD = "افزودن به گروه"
GBTN_HELP = "راهنما"
ADMIN_GROUPS_TITLE = "{i_users} <b>گروه‌ها</b>"
ADMIN_GROUPS_BODY = (
    "{i_admin} کل: <b>{total}</b> · فعال: <b>{active}</b>\n"
    "{i_users} مجموع اعضا: <b>{members}</b>\n"
    "{i_download} دانلود از گروه‌ها: <b>{downloads}</b>"
)
ADMIN_GROUPS_EMPTY = "ربات هنوز در هیچ گروهی نیست."
BTN_ADMIN_GROUPS = "گروه‌ها"
NOT_YOUR_BUTTON = "این درخواست مال شما نیست — خودت لینک رو بفرست."
YES = "بله"
NO = "خیر"
INBOX_CARD = (
    "🆔 آیدی: <code>{id}</code>\n"
    "📆 عضویت: {joined}\n"
    "🌐 زبان: {lang_code}\n"
    "⬇️ دانلودها: <b>{downloads}</b>\n"
    "💬 پیام‌ها: <b>{messages}</b>\n"
    "🚫 بن: {banned}"
)
INBOX_CARD_RECENT = "<b>آخرین لینک‌ها:</b>"
BTN_PREV = "⬅️ قبلی"
BTN_NEXT = "➡️ بعدی"
BTN_REFRESH = "🔄 بروزرسانی"
INBOX_TITLE = "📬 <b>صندوق پیام‌ها</b>"
INBOX_EMPTY = "هنوز پیامی ثبت نشده."
INBOX_COUNT = "👥 گفتگوها: <b>{count}</b>"
INBOX_THREAD_TITLE = "💬 <b>{who}</b>"
INBOX_THREAD_META = "🆔 <code>{id}</code> · 📊 {count} پیام"
INBOX_BTN_REPLY = "✍️ پاسخ"
INBOX_BTN_OLDER = "⬆️ قدیمی‌تر"
INBOX_BTN_NEWER = "⬇️ جدیدتر"
INBOX_BTN_STATS = "📊 آمار کاربر"
INBOX_BTN_BAN = "🚫 بن"
INBOX_BTN_SEARCH = "🔍 جستجو در پیام‌ها"
INBOX_BTN_ADMIN = "📬 صندوق پیام‌ها"
INBOX_REPLY_PROMPT = "✍️ پاسخت رو بفرست (به <b>{who}</b> ارسال می‌شه):"
INBOX_REPLY_OK = "✅ پاسخ ارسال شد."
INBOX_REPLY_FAIL = "❌ ارسال نشد: {error}"
INBOX_SEARCH_PROMPT = "🔍 عبارت مورد نظر رو بفرست:"
INBOX_SEARCH_EMPTY = "چیزی پیدا نشد."
INBOX_SEARCH_TITLE = "🔍 <b>نتایج جستجو</b>"
BTN_BACK = "🔙 بازگشت"
BTN_CLOSE = "✖️ بستن"
BTN_CONFIRM_YES = "✅ بله"
BTN_CONFIRM_NO = "❌ انصراف"
BTN_REDOWNLOAD = "🔄 دانلود مجدد"
BTN_MP3 = "🎵 دانلود فایل صوتی/آهنگ"
BTN_LINK = "🔗 لینک مستقیم"
BTN_JOINED = "✅ عضو شدم؛ بررسی کن"
BTN_JOIN = "📢 عضویت در کانال"

QUALITY_360 = "🟢 360p"
QUALITY_480 = "🟡 480p"
QUALITY_720 = "🔵 720p"
QUALITY_1080 = "🟣 1080p"
QUALITY_BEST = "⭐️ بهترین کیفیت"
QUALITY_MP3 = "🎵 MP3 (320kbps)"

BTN_ADMIN_STATS = "📊 آمار ربات"
BTN_ADMIN_TOP = "📈 پلتفرم‌های محبوب"
BTN_ADMIN_HEALTH = "🩺 سلامت پلتفرم‌ها"
BTN_ADMIN_ERRORS = "🐞 آخرین خطاها"
BTN_ADMIN_BROADCAST = "📢 ارسال همگانی"
BTN_ADMIN_BAN = "🚫 بن کاربر"
BTN_ADMIN_UNBAN = "🔓 رفع بن"
BTN_ADMIN_BANLIST = "📋 لیست بن‌ها"
BTN_ADMIN_CACHE = "⚡️ کش"
BTN_ADMIN_CACHE_CLEAR = "🗑 پاک‌کردن کش"
BTN_ADMIN_EXPORT = "📄 خروجی CSV"
BTN_ADMIN_USERS = "👥 کاربران برتر"
BTN_ADMIN_SETTINGS = "⚙️ تنظیمات"
BTN_ADMIN_MAINTENANCE = "🔧 حالت تعمیر"
BTN_ADMIN_STORAGE = "🧹 وضعیت فضا"
BTN_ADMIN_CLEAN = "🗑 پاک‌سازی فضا"
BTN_ADMIN_REFRESH = "🔄 بروزرسانی"
BTN_ADMIN_CHARTS = "📈 نمودارها"
BTN_ADMIN_SERVICES = "🔧 سرویس‌ها"
BTN_ADMIN_FINDUSER = "🔍 جستجوی کاربر"
BTN_ADMIN_PLATFORMS = "🎛 پلتفرم‌ها"
BTN_ADMIN_LIMITS = "⚙️ محدودیت‌ها"
BTN_ADMIN_BACKUP = "🗄 بکاپ دیتابیس"
BTN_ADMIN_RESTART = "🔁 ری‌استارت"
BTN_BC_ALL = "📢 همه کاربران"
BTN_BC_ACTIVE = "🔥 فقط کاربران فعال (۷ روز)"
BTN_ALL_ITEMS = "📥 همه ({count})"
BTN_FIRST_ONLY = "1️⃣ فقط اولی"

# ── admin panel: new sections ─────────────────────────────────────────
CHART_TITLE = "📈 <b>نمودار ۷ روز اخیر</b>"
CHART_DOWNLOADS = "⬇️ <b>دانلودهای روزانه</b>"
CHART_NEWUSERS = "🆕 <b>کاربران جدید روزانه</b>"
CHART_PEAK = "🕐 <b>ساعات اوج</b>"
SERVICES_TITLE = "🔧 <b>وضعیت سرویس‌ها</b>"
SERVICES_COOKIES = "🍪 <b>کوکی‌ها</b>"
PLATFORMS_TITLE = "🎛 <b>کنترل پلتفرم‌ها</b>"
PLATFORMS_HELP = "برای روشن/خاموش‌کردن هر پلتفرم روی دکمه‌اش بزن."
PLATFORM_ON = "روشن"
PLATFORM_OFF = "خاموش"
PLATFORM_DISABLED = "⛔️ این سرویس موقتاً غیرفعاله. بعداً امتحان کن."
LIMITS_TITLE = "⚙️ <b>محدودیت‌ها</b>"
LIMITS_BODY = (
    "🔢 دانلود همزمان کل: <b>{concurrent}</b>\n"
    "👤 همزمان هر کاربر: <b>{per_user}</b>\n"
    "🐌 آستانه کندسازی ساعتی: <b>{soft}</b>\n\n"
    "<i>این‌ها با متغیرهای محیطی تنظیم می‌شن.</i>"
)
FINDUSER_PROMPT = "🔍 آیدی عددی کاربر رو بفرست:"
BACKUP_READY = "🗄 بکاپ دیتابیس آماده‌ست ({size})"
BACKUP_FAILED = "❌ بکاپ ناموفق: {error}"
RESTART_CONFIRM = "🔁 <b>مطمئنی می‌خوای ربات ری‌استارت بشه؟</b>\n\nربات چند ثانیه قطع می‌شه و دوباره بالا میاد."
RESTART_NOW = "🔁 در حال ری‌استارت... چند ثانیه صبر کن."

# ── cookies (admin only) ──────────────────────────────────────────────
COOKIE_TITLE = "🍪 <b>وضعیت کوکی‌ها</b>\n"
COOKIE_ROW_NONE = "⚪️ {platform}: تنظیم نشده"
COOKIE_ROW_OK = "🟢 {platform}: سالم — {count} کوکی، {days} روز اعتبار (سن: {age} روز)"
COOKIE_ROW_BAD = "🔴 {platform}: مشکل — {reason}"
COOKIE_HELP = (
    "برای فعال‌سازی، فایل <code>cookies.txt</code> را همینجا بفرست.\n"
    "نام فایل یا کپشن باید نام پلتفرم را داشته باشد (مثلاً <code>youtube.txt</code>).\n"
    "افزونه‌ی «Get cookies.txt LOCALLY» در مرورگر، فایل را می‌سازد.\n"
    "حذف: <code>/cookies del youtube</code>"
)
COOKIE_SAVED = "✅ کوکی {platform} ذخیره شد — {count} کوکی، {days} روز اعتبار."
COOKIE_REMOVED = "🗑 کوکی {platform} حذف شد."
COOKIE_NONE = "کوکی‌ای برای {platform} ذخیره نشده بود."
COOKIE_USAGE = "نام پلتفرم مشخص نیست. یکی از این‌ها: {platforms}"
COOKIE_ERR_FORMAT = "❌ فایل قابل خواندن نیست. باید فرمت Netscape (cookies.txt) باشد."
COOKIE_ERR_DOMAIN = "❌ این فایل مربوط به {platform} نیست"
COOKIE_ERR_SESSION = "❌ کوکی نشست (لاگین) در فایل نیست؛ قبل از خروجی گرفتن وارد حساب شو"
COOKIE_ERR_EXPIRED = "❌ اعتبار این کوکی‌ها تمام شده؛ دوباره خروجی بگیر."
COOKIE_ERR_BIG = "❌ فایل خیلی بزرگ است."

# ── file to link (public) ─────────────────────────────────────────────
F2L_WORKING = "⏳ در حال آپلود فایل و ساخت لینک..."
F2L_DONE = "✅ فایل آپلود شد\n\n📄 <b>{name}</b>\n📦 {size}\n🔗 {url}"
F2L_BUTTON = "⬇️ دانلود فایل"
F2L_FAILED = "❌ آپلود فایل ناموفق بود. کمی بعد دوباره امتحان کن."
F2L_TOO_BIG = "❌ حجم فایل بیش از حد مجاز است (حداکثر {limit} مگابایت)."

# ── inline mode ───────────────────────────────────────────────────────
INLINE_HELP_TITLE = "لینک بفرست تا دانلود کنم"
INLINE_HELP_DESC = "بعد از @نام‌ربات لینک یوتیوب/اینستاگرام/... را بچسبان"
INLINE_HELP_MSG = "برای دانلود، بعد از @{username} یک لینک بگذار."
INLINE_FETCH_TITLE = "⬇️ دانلود از {platform}"
INLINE_FETCH_DESC = "برای گرفتن فایل با کیفیت دلخواه بزن"
INLINE_FETCH_MSG = "🔗 {url}\n\nبرای دانلود این لینک به @{username} بزن 👇"

# ── inline live media ─────────────────────────────────────────────────
INLINE_LIVE_DESC = "همین‌جا در چت دانلود و ارسال می‌شود"
INLINE_LIVE_MSG = "⏳ در حال دانلود از {platform}... چند لحظه صبر کن"
INLINE_WORKING_BTN = "⏳ در حال دانلود..."
INLINE_LIVE_FAIL = "❌ دانلود ناموفق بود. لینک را مستقیم برای ربات بفرست."
INLINE_TOO_BIG = "📦 فایل بزرگ‌تر از حد inline است. برای دریافت، ربات را باز کن 👇"
INLINE_OPEN_BTN = "⬇️ دریافت از ربات"

# ── history / re-download ─────────────────────────────────────────────
BTN_HISTORY = "🕓 تاریخچه من"
HISTORY_TITLE = "🕓 <b>آخرین دانلودهای شما</b> ({count})\n\nبرای دریافت مجدد، روی هرکدام بزن:"
HISTORY_EMPTY = "هنوز چیزی دانلود نکرده‌ای. یک لینک بفرست تا شروع کنیم!"

# ── user features: favorites / settings / mystats / invite ────────────
BTN_FAVORITES = "⭐️ علاقه‌مندی‌ها"
BTN_SETTINGS_USER = "⚙️ تنظیمات"
BTN_INVITE = "🎁 دعوت دوستان"
BTN_FAV_ON = "⭐️ ذخیره شد"
BTN_FAV_OFF = "☆ افزودن به علاقه‌مندی"
FAV_ADDED = "⭐️ به علاقه‌مندی‌ها اضافه شد"
FAV_REMOVED = "☆ از علاقه‌مندی‌ها حذف شد"
FAV_TITLE = "⭐️ <b>علاقه‌مندی‌های شما</b> ({count})\n\nبرای دریافت مجدد، روی هرکدام بزن:"
FAV_EMPTY = "هنوز چیزی ذخیره نکرده‌ای. زیر هر دانلود دکمه‌ی «☆ افزودن به علاقه‌مندی» رو بزن."

SETTINGS_USER_TITLE = (
    "{i_settings} <b>تنظیمات شخصی</b>\n"
    "{div}\n"
    "کیفیت و فرمت پیش‌فرض دانلودت رو انتخاب کن.\n"
    "🟢 = انتخاب فعلی تو\n\n"
    "<blockquote>💡 اگه کیفیت پیش‌فرض بذاری، دیگه هر بار ازت نمی‌پرسه و سریع‌تر می‌رسه.</blockquote>"
)
SET_QUALITY_LABEL = "— کیفیت پیش‌فرض —"
SET_Q_ASK = "هر بار بپرس"
SET_Q_BEST = "بهترین"
SET_FORMAT_LABEL = "— فرمت پیش‌فرض —"
SET_F_VIDEO = "🎬 ویدیو"
SET_F_AUDIO = "🎵 صوتی"
SET_NOTIFY = "اعلان نتیجه"
SET_SAVED = "✅ ذخیره شد"

# Sent as a fresh reply when a queued job finishes, but only if the user waited
# in a queue AND has result alerts on — so they get pinged instead of scrolling
# back to a silently-edited status message.
NOTIFY_READY = "🔔 دانلودت آماده شد 👆"

MYSTATS = (
    "{i_stats} <b>آمار من</b>\n"
    "{div}\n"
    "{i_download} کل دانلودها: <b>{total}</b>\n"
    "{i_date} این هفته: <b>{week}</b>\n"
    "{i_disk} حجم کل: <b>{bytes}</b>\n"
    "{i_star} پلتفرم برتر: <b>{top}</b>\n"
    "{i_invite} دعوت‌شده‌ها: <b>{refs}</b>\n"
    "{i_clock} عضویت: <b>{joined}</b>\n\n"
    "{i_chart} <b>۷ روز اخیر</b>\n{chart}"
)

INVITE_TEXT = (
    "{i_invite} <b>دعوت دوستان</b>\n"
    "{div}\n"
    "این لینک اختصاصی توئه — هرکی باهاش ربات رو استارت کنه به اسم تو ثبت می‌شه:\n\n"
    "<code>{link}</code>\n\n"
    "👥 تا حالا <b>{count}</b> نفر رو دعوت کردی."
)
# ── subtitles ─────────────────────────────────────────────────────────
BTN_SUBTITLE = "📝 زیرنویس"
SUB_NONE = "زیرنویسی برای این ویدیو پیدا نشد."
SUB_READY = "📝 زیرنویس آماده شد"
SUB_FETCHING = "⏳ در حال دریافت زیرنویس..."

# ── instagram story / profile pic ─────────────────────────────────────
HINT_STORY_COOKIE = "برای دانلود استوری اینستاگرام، ادمین باید کوکی instagram.com را نصب کند."

# ── theme / UI personalization (admin) ────────────────────────────────
BTN_ADMIN_THEME = "🎨 ظاهر ربات"
THEME_TITLE = "{i_brand} <b>شخصی‌سازی ظاهر</b>"
THEME_PREVIEW = "پیش‌نمایش زنده"
THEME_STATE = (
    "🎨 تم فعال: <b>{preset}</b>\n"
    "✏️ اسلات‌های دست‌کاری‌شده: <b>{edited}</b> از {total}\n"
    "💎 ایموجی پرمیوم در متن: <b>{premium}</b>\n"
    "🎨 رنگ دکمه‌ها: <b>{blue}</b>\n"
    "💠 ایموجی پرمیوم روی دکمه: <b>{icons}</b>\n"
    "🎯 رنگ‌های فعلی: {rolebar}"
)
THEME_HELP = (
    "<blockquote>روی هر اسلات بزن و ایموجی دلخواهت رو بفرست.\n"
    "اگه <b>ایموجی پرمیوم</b> بفرستی، خودش شناسه‌اش رو می‌خونه و ذخیره می‌کنه.\n"
    "برای برگرداندن یک اسلات به حالت اول، <code>-</code> بفرست.</blockquote>"
)
THEME_BTN_PREMIUM = "💎 ایموجی پرمیوم"
THEME_BTN_BLUE = "🔵 دکمه‌های آبی"
ON = "روشن"
OFF = "خاموش"
THEME_BTN_PACK = "📦 نصب پک ایموجی پرمیوم"
THEME_PACK_PROMPT = (
    "📦 <b>نصب پک ایموجی</b>\n"
    "{div}\n"
    "یکی از این‌ها رو بفرست:\n"
    "• لینک پک: <code>t.me/addemoji/NAME</code>\n"
    "• یا فقط یک <b>ایموجی پرمیوم</b> از اون پک\n"
    "• یا اسم پک: <code>NewsEmoji</code>\n\n"
    "چند تا هم بفرستی همه اضافه می‌شن."
)
THEME_PACK_OK = "✅ پک نصب شد — <b>{count}</b> ایموجی آماده\n📦 {packs}"
THEME_PACK_SKIPPED = "⚠️ اینا پیدا نشدن: {packs}"
THEME_PACK_FAIL = "❌ هیچ پکی از «{packs}» خونده نشد. پک‌های قبلی برگشتن."
THEME_PACK_BAD = "❌ نه لینک پکی پیدا کردم نه ایموجی پرمیومی. دوباره بفرست."
THEME_BTN_STYLES = "🎨 رنگ دکمه‌ها"
THEME_BTN_ICONS = "💠 ایموجی پرمیوم روی دکمه"
THEME_ROLE_MENU = "منو"
THEME_ROLE_ADMIN = "ادمین"
THEME_ROLE_QUALITY = "کیفیت"
THEME_ROLE_CONFIRM = "تأیید"
THEME_ROLE_CANCEL = "لغو"
THEME_ROLE_ACTION = "اکشن"
THEME_ROLE_NAV = "بازگشت"

THEME_BTN_RESET = "♻️ بازگشت به پیش‌فرض"
THEME_SLOT_PROMPT = (
    "✏️ ایموجی جدید برای <code>{slot}</code> رو بفرست.\n\n"
    "<blockquote>ایموجی معمولی یا پرمیوم — هردو قبوله. "
    "برای حالت پیش‌فرض <code>-</code> بفرست.</blockquote>"
)
THEME_SLOT_SAVED = "✅ اسلات <code>{slot}</code> شد: {value}"
THEME_SLOT_RESET = "♻️ اسلات <code>{slot}</code> به پیش‌فرض برگشت."
THEME_SLOT_BAD = "❌ این اسلات وجود نداره."
THEME_RESET_DONE = "♻️ همه‌ی ظاهر به پیش‌فرض برگشت."

# Short labels reused by the theme preview.
PROCESSING_LABEL = "در حال بررسی"
DOWNLOADING_LABEL = "در حال دانلود"
DOWNLOAD_SUCCESS_LABEL = "انجام شد"
THEME_BTN_SEP_MINUS = "➖ خط کوتاه‌تر"
THEME_BTN_SEP_PLUS = "➕ خط بلندتر"
THEME_BTN_EXPORT = "📤 خروجی تم"
THEME_BTN_IMPORT = "📥 ورود تم"
THEME_EXPORT = (
    "📤 <b>تم فعلی</b>\n\n"
    "<pre>{json}</pre>\n"
    "این متن را ذخیره کن؛ با «📥 ورود تم» هر وقت خواستی برگردان."
)
THEME_IMPORT_PROMPT = (
    "📥 متن JSON تم را بفرست.\n\n"
    "<blockquote>همان چیزی که «📤 خروجی تم» داده بود.</blockquote>"
)
THEME_IMPORT_OK = "✅ تم با موفقیت وارد شد."
THEME_IMPORT_BAD = "❌ این JSON خوانده نشد. متن خروجی را کامل بفرست."
UPLOADING_LABEL = "در حال ارسال"
FROM_CACHE_LABEL = "از حافظه"


TELEGRAM_STORY_UNSUPPORTED = "استوری تلگرام با ربات قابل دانلود نیست (تلگرام دسترسی استوری را برای ربات‌ها بسته). فقط لینک پست‌های کانال/گروه (مثل t.me/channel/123) را بفرست."

TGLOGIN_ASK_PHONE = "برای فعال‌کردن دانلود استوری تلگرام، باید یک اکانت کاربری لاگین شود (توکن ربات اجازه‌ی استوری ندارد).\n\nشماره تلفن اکانت را با کد کشور بفرست، مثل <code>+989121234567</code>.\n\nبرای لغو: /cancel"
TGLOGIN_ASK_CODE = "کد ورودی که تلگرام فرستاد را بفرست.\n\n<b>مهم:</b> بین رقم‌ها فاصله بگذار تا تلگرام کد را باطل نکند، مثل <code>1 2 3 4 5</code>.\n\nبرای لغو: /cancel"
TGLOGIN_ASK_PASSWORD = "این اکانت تأیید دومرحله‌ای (رمز عبور) دارد. رمز را بفرست.\n\nبرای لغو: /cancel"
TGLOGIN_DONE = "اکانت <b>{who}</b> با موفقیت لاگین شد. حالا دانلود استوری تلگرام فعال است."
TGLOGIN_ALREADY = "یک اکانت (<b>{who}</b>) از قبل لاگین است. برای خروج: /tglogout"
TGLOGIN_NONE = "هیچ اکانتی لاگین نیست."
TGLOGOUT_DONE = "اکانت کاربری خارج شد و سشن پاک شد."
TGLOGIN_CANCELLED = "لغو شد."
TGLOGIN_ERROR = "خطا در ورود: <code>{error}</code>\nدوباره /tglogin را بزن."
STORY_NEEDS_LOGIN = "برای دانلود استوری تلگرام باید یک اکانت کاربری لاگین شود. در پیوی دستور /tglogin را بزن (فقط ادمین)."
