"""English strings. Keys must stay in sync with fa.py."""

# ── general ───────────────────────────────────────────────────────────
WELCOME_MESSAGE = (
    "{i_brand} <b>Media Downloader</b>\n"
    "{div}\n"
    "Hi <b>{user_name}</b> 👋\n\n"
    "Send me any link and I'll grab it — fast and in high quality. 🚀\n"
    "Just paste the link, that's it!\n\n"
    "📋 <b>Supported platforms</b>\n"
    "{platforms}\n\n"
    "<blockquote>💡 Send several links in one message. Repeat links come straight from cache.</blockquote>"
)
LANG_SELECT_MESSAGE = "🌐 Please choose your language:\n\nزبان خودت رو انتخاب کن:"
LANG_SELECTED_FA = "✅ زبان فارسی انتخاب شد!"
LANG_SELECTED_EN = "✅ English selected!"

SUPPORTED_LIST = (
    "📸 Instagram — reels, posts, stories, profile, carousels\n"
    "🎬 YouTube — video, Shorts, quality picker, MP3\n"
    "📱 TikTok — no watermark\n"
    "🐦 Twitter / X — video, all photos, tweet text\n"
    "🎵 Spotify — track with cover art\n"
    "🎧 SoundCloud — tracks and podcasts\n"
    "📌 Pinterest — images and video\n"
    "🔗 Direct link — any file"
)

HELP_MESSAGE = (
    "{i_help} <b>Bot help</b>\n"
    "{div}\n"
    "<b>How it works</b>\n"
    "1. Send a link — that's it!\n"
    "2. For YouTube, pick a quality\n"
    "3. Multiple links in one message work too\n\n"
    "⌨️ <b>Commands</b>\n"
    "/start — start & main menu\n"
    "/me — my stats\n"
    "/settings — personal settings\n"
    "/favorites — saved links\n"
    "/invite — invite friends\n"
    "/lang — change language\n"
    "/cancel — cancel current operation\n\n"
    "📋 <b>Platforms</b>\n{platforms}\n\n"
    "<blockquote>💡 Repeat links are served from cache instantly.</blockquote>"
)

# ── download flow ─────────────────────────────────────────────────────
PROCESSING = "{i_search} <b>Checking link</b>\n<code>{bar_2}</code>"
QUEUED = "{i_queue} <b>Queued</b>\n<code>{bar_1}</code>"
DOWNLOADING = "{i_download} <b>Downloading</b>\n<code>{bar_6}</code>"
UPLOADING = "{i_upload} <b>Uploading to Telegram</b>\n<code>{bar_9}</code>"
FROM_CACHE = "⚡️ from cache"

DOWNLOAD_SUCCESS = "✅ Download complete!"
DOWNLOAD_FAILED = "❌ Download failed\n\n<code>{error}</code>\n\n💡 {hint}"
UNSUPPORTED_PLATFORM = (
    "❌ This link can't be downloaded.\n\n<b>Supported platforms:</b>\n{platforms}"
)
NO_LINK_FOUND = "🔗 Send me a link and I'll download it."
FILE_TOO_LARGE = (
    "⚠️ The file is {size}, above Telegram's limit.\n\n🔗 <a href=\"{url}\">Direct download</a>"
)
TEXT_ONLY_POST = "📝 This post is text only:\n\n{text}"

CAPTION_TEMPLATE = "{title}\n\n{meta}"
BATCH_PROGRESS = "📦 Link {current} of {total}"
BATCH_SUMMARY = "📦 <b>Done:</b> {ok} succeeded, {failed} failed out of {total}"

PLAYLIST_FOUND = "🎼 <b>{title}</b>\n\nFound {count} items. Download all?"
PLAYLIST_STARTED = "🎼 Starting {count} items..."
PLAYLIST_ITEM = "🎼 {current}/{total} — {title}"

QUALITY_PROMPT = (
    "🎬 <b>{title}</b>\n"
    "<b>━━━━━━━━━━━━━━━━━</b>\n"
    "👤 {uploader}\n"
    "⏱ {duration}   👁 {views}\n"
    "<b>━━━━━━━━━━━━━━━━━</b>\n"
    "Choose a quality 👇"
)

# ── user stats ────────────────────────────────────────────────────────
USER_STATS = (
    "{i_stats} <b>Your stats</b>\n"
    "{div}\n"
    "⬇️ Downloads: <b>{downloads}</b>\n"
    "📅 Joined: <b>{joined}</b>\n"
    "🌐 Language: <b>{ulang}</b>"
)

# ── errors / hints ────────────────────────────────────────────────────
ERROR_GENERIC = "❌ Something went wrong. Please try again in a moment."
HINT_PRIVATE = "This post is private or has been deleted."
HINT_GEO = "This content is geo-restricted for the server's region."
HINT_RETRY = "Try again in a moment."
HINT_LIVE = "Live streams can't be downloaded."
HINT_LOGIN = "This content requires a login."
HINT_RESTRICTED = "This post is age- or audience-restricted, so it isn't visible to everyone."
HINT_BLOCKED = "This platform is currently refusing requests from the server. Try again later."

BANNED_MESSAGE = "🚫 Your access to this bot has been restricted."
JOIN_CHANNEL = (
    "<b>🔒 Required membership</b>\n\n"
    "To use the bot, first join the channel(s) below,\n"
    "then tap <b>“✅ I joined; check”</b>:\n\n"
    "{channel}"
)
JOIN_CHECK_FAILED = "❌ You haven't joined yet. Tap the button after joining."
JOIN_OK = "✅ Membership confirmed! Send your link now 🎬"

# ── admin panel ───────────────────────────────────────────────────────
ADMIN_PANEL = (
    "{i_admin} <b>Admin panel</b>\n"
    "{div}\n"
    "{pulse}\n\n"
    "{i_users} Users: <b>{users}</b>  ({i_new} {new_today} today)\n"
    "{i_download} Downloads: <b>{downloads}</b>  ({i_date} {today} today)\n"
    "{i_hot} Active 24h: <b>{active}</b>\n"
    "{i_cache} Cache: <b>{cache_rows}</b> items / {cache_hits} hits\n"
    "{i_queue} Running: <b>{running}</b>  |  Queued: <b>{waiting}</b>\n"
    "{i_disk} Free disk: <b>{disk}</b>\n"
    "{i_clock} Uptime: <b>{uptime}</b>"
)
STATS_TITLE = "📊 <b>Full statistics</b>"
STATS_USERS = "👥 Users: {count}"
STATS_DOWNLOADS = "⬇️ Downloads: {count}"
STATS_TODAY = "📅 Today: {count}"
STATS_WEEK = "📅 This week: {count}"
STATS_TOP = "🔥 Most popular: {platform}"
STATS_ACTIVE = "👤 Most active: {name}"
STATS_FAILED = "❌ Failed: {count}"
STATS_TRAFFIC = "📦 Total traffic: {size}"
STATS_SUCCESS_RATE = "✅ Success rate: {rate}%"

TOP_TITLE = "🔥 <b>Top platforms</b>"
TOP_FORMAT = "{rank}. {platform} — {count} downloads"
HEALTH_TITLE = "🩺 <b>Platform health (24h)</b>"
HEALTH_FORMAT = "{emoji} {platform}: {rate}%  ({ok}/{total})"
HEALTH_EMPTY = "No downloads recorded in the last 24 hours."
ERRORS_TITLE = "🐞 <b>Recent errors</b>"
ERRORS_EMPTY = "✅ No errors recorded."

BROADCAST_PROMPT = "📢 Send the message (text, photo, video or a forward):"
BROADCAST_CONFIRM = "⚠️ Send this message to <b>{count}</b> users?"
BROADCAST_SENDING = "📤 Sending... {current}/{total}"
BROADCAST_SENT = "✅ Sent to {count} users."
BROADCAST_FAILED = "❌ {count} failed (blocked the bot)."
BROADCAST_CANCELLED = "✖️ Broadcast cancelled."

BAN_PROMPT = "🚫 Send the numeric user ID:"
BAN_SUCCESS = "✅ User <code>{id}</code> has been banned."
BAN_SELF = "❌ You can't ban yourself."
UNBAN_PROMPT = "🔓 Send the numeric user ID:"
UNBAN_SUCCESS = "✅ User <code>{id}</code> has been unbanned."
BAN_LIST_TITLE = "🚫 <b>Banned users</b>"
BAN_LIST_EMPTY = "✅ Nobody is banned."

USER_LOOKUP = (
    "👤 <b>User info</b>\n\n"
    "🆔 <code>{id}</code>\n"
    "📛 {name} {username}\n"
    "🌐 Language: {ulang}\n"
    "⬇️ Downloads: {downloads}\n"
    "📅 Joined: {joined}\n"
    "🕐 Last active: {last}\n"
    "🚫 Banned: {banned}"
)
USER_NOT_FOUND = "❌ No user found with that ID."
INVALID_ID = "❌ Invalid ID. Send a number."

CACHE_STATS = (
    "⚡️ <b>Cache status</b>\n\n"
    "📦 Items: {rows}\n"
    "🎯 Hits: {hits}\n"
    "💾 Bandwidth saved: {saved}"
)
CACHE_CLEARED = "🗑 Cleared {count} cached items."
SETTINGS_TITLE = "⚙️ <b>Settings</b>"
JOIN_STILL_MISSING = "❌ Not joined yet: {channels}"
CHANNEL_NOT_FOUND = "That channel was not in the list: {channel}"
CHANNEL_REMOVED = "🗑 Channel removed: {channel}"
CHANNEL_EXISTS = "That channel is already in the list: {channel}"
CHANNEL_STATUS_OFF = (
    "🔓 <b>Forced membership is off</b>\n\n"
    "To enable it:\n"
    "<code>/setchannel @channel</code>\n\n"
    "⚠️ The bot must be an <b>administrator</b> of the channel, otherwise "
    "Telegram will not let it read who is a member."
)
CHANNEL_STATUS_TITLE = "🔒 <b>Forced membership</b>"
CHANNEL_STATUS_BROKEN = "⚠️ not working — promote the bot to admin"
CHANNEL_STATUS_HELP = (
    "<code>/setchannel @channel</code> add\n"
    "<code>/setchannel -@channel</code> remove\n"
    "<code>/setchannel off</code> disable"
)
CHANNEL_ERR_NOT_FOUND = (
    "❌ Channel not found: <code>{channel}</code>\n\n"
    "Send a valid handle, e.g. <code>@mychannel</code>"
)
CHANNEL_ERR_NOT_ADMIN = (
    "❌ <b>The bot is not an admin of this channel</b>\n"
    "<code>{channel}</code>\n\n"
    "Telegram only exposes a channel's member list to admins, so without it "
    "there is no way to tell whether a user joined.\n\n"
    "🔹 Promote the bot to admin in the channel\n"
    "🔹 No permissions needed — leave every toggle off\n"
    "🔹 Then run this command again"
)
CHANNEL_ERR_NO_INVITE = (
    "❌ The channel is private and has no invite link.\n"
    "Give the bot the \"invite users via link\" permission, then try again."
)
CHANNEL_ERR_GENERIC = "❌ Error for <code>{channel}</code>\n\n<code>{error}</code>"
CHANNEL_SET = "✅ Required channel set to: {channel}"
CHANNEL_OFF = "✅ Forced membership disabled."
WELCOME_SET = "✅ Welcome message updated."
EXPORT_READY = "📄 CSV export ready ({count} users)."
NO_ACCESS = "❌ You don't have access."
CANCELLED = "✖️ Cancelled."
NOTHING_TO_CANCEL = "Nothing to cancel."
MAINTENANCE_ON = "🔧 Maintenance mode ON — only admins can download."
MAINTENANCE_OFF = "✅ Maintenance mode OFF."
MAINTENANCE_NOTICE = "🔧 The bot is under maintenance. Please try again shortly."

# ── buttons ───────────────────────────────────────────────────────────
BTN_ADMIN = "⚙️ Admin panel"
BTN_HELP = "📖 Help"
BTN_LANGUAGE = "🌐 Language"
BTN_STATS = "📊 My stats"
GROUP_WELCOME = (
    "{i_brand} <b>Hi! I'm a media downloader</b>\n"
    "{div}\n"
    "Send a link and I'll send the file back.\n\n"
    "{i_link} <b>Platforms:</b> YouTube, Instagram, TikTok, Twitter/X, Spotify, SoundCloud, Pinterest, Google Play, Telegram\n"
    "{i_settings} Group admins can tune me with /gsettings.\n\n"
    "{i_help} Default: I only reply when you tag @{bot}."
)
GROUP_PM_ONLY = "That page is personal — open it in the bot's DM."
GROUP_START = (
    "{i_brand} <b>Downloader bot</b>\n"
    "{div}\n"
    "{i_link} Send a link and I'll fetch the file.\n"
    "{i_download} Files too: tag me in the file's caption.\n"
    "{i_help} Current mode: <b>{mode}</b>\n\n"
    "{i_settings} Personal pages (history, favourites, stats) live in DM."
)
GROUP_MODE_TAG = "only when you tag me"
GROUP_MODE_AUTO = "I grab any link"
GROUP_MODE_ADMINS = " · admins only"
GBTN_OPEN_PM = "Open in DM"
GBTN_SETTINGS = "Group settings"

GROUP_HELP = (
    "{i_help} <b>Group help</b>\n"
    "{div}\n"
    "{i_link} Send a link, or reply to a message and tag me.\n"
    "{i_download} Files work too: tag me in the file's caption.\n"
    "{i_settings} Group admins: /gsettings"
)
GSETTINGS_TITLE = "{i_settings} <b>Group settings</b>"
GSETTINGS_BODY = (
    "{i_admin} Group: <b>{title}</b>\n"
    "{i_users} Members: <b>{members}</b>\n"
    "{i_download} Downloads: <b>{downloads}</b>\n"
    "{div}\n"
    "{i_link} Link mode: <b>{mode}</b>\n"
    "{i_admin} Admins only: <b>{admins_only}</b>\n"
    "{i_cache} Clean messages: <b>{clean}</b>\n"
    "{i_quality} Default quality: <b>{quality}</b>\n"
    "{i_lang} Language: <b>{lang_name}</b>"
)
GSETTINGS_ONLY_ADMIN = "This command is for group admins only."
GSETTINGS_ONLY_GROUP = "This command only works inside a group."
GMODE_AUTO = "any link"
GMODE_TAG = "tag only"
GBTN_MODE = "Link mode"
GBTN_ADMINS = "Admins only"
GBTN_CLEAN = "Clean messages"
GBTN_QUALITY = "Default quality"
GBTN_LANG = "Group language"
GQUALITY_ASK = "ask"
GLANG_USER = "user's language"
GBTN_ADD = "Add to a group"
GBTN_HELP = "Help"
ADMIN_GROUPS_TITLE = "{i_users} <b>Groups</b>"
ADMIN_GROUPS_BODY = (
    "{i_admin} Total: <b>{total}</b> · active: <b>{active}</b>\n"
    "{i_users} Members combined: <b>{members}</b>\n"
    "{i_download} Downloads from groups: <b>{downloads}</b>"
)
ADMIN_GROUPS_EMPTY = "The bot is not in any group yet."
BTN_ADMIN_GROUPS = "Groups"
NOT_YOUR_BUTTON = "This request isn't yours — send your own link."
YES = "yes"
NO = "no"
INBOX_CARD = (
    "🆔 ID: <code>{id}</code>\n"
    "📆 Joined: {joined}\n"
    "🌐 Language: {lang_code}\n"
    "⬇️ Downloads: <b>{downloads}</b>\n"
    "💬 Messages: <b>{messages}</b>\n"
    "🚫 Banned: {banned}"
)
INBOX_CARD_RECENT = "<b>Recent links:</b>"
BTN_PREV = "⬅️ Prev"
BTN_NEXT = "➡️ Next"
BTN_REFRESH = "🔄 Refresh"
INBOX_TITLE = "📬 <b>Inbox</b>"
INBOX_EMPTY = "No messages recorded yet."
INBOX_COUNT = "👥 Conversations: <b>{count}</b>"
INBOX_THREAD_TITLE = "💬 <b>{who}</b>"
INBOX_THREAD_META = "🆔 <code>{id}</code> · 📊 {count} messages"
INBOX_BTN_REPLY = "✍️ Reply"
INBOX_BTN_OLDER = "⬆️ Older"
INBOX_BTN_NEWER = "⬇️ Newer"
INBOX_BTN_STATS = "📊 User stats"
INBOX_BTN_BAN = "🚫 Ban"
INBOX_BTN_SEARCH = "🔍 Search messages"
INBOX_BTN_ADMIN = "📬 Inbox"
INBOX_REPLY_PROMPT = "✍️ Send your reply (it goes to <b>{who}</b>):"
INBOX_REPLY_OK = "✅ Reply sent."
INBOX_REPLY_FAIL = "❌ Could not send: {error}"
INBOX_SEARCH_PROMPT = "🔍 Send the term to search for:"
INBOX_SEARCH_EMPTY = "Nothing found."
INBOX_SEARCH_TITLE = "🔍 <b>Search results</b>"

# ── inbox messenger: unread, media gallery, starred ───────────────────
INBOX_UNREAD_LINE = "🔴 Unread: <b>{count}</b>"
INBOX_TAP_MEDIA = "has a file →"
INBOX_BTN_STARRED = "⭐ Starred"
INBOX_BTN_READALL = "✅ Mark all read"
INBOX_BTN_BROADCAST = "📣 Message everyone"
INBOX_BTN_GALLERY = "🗂 Files"
INBOX_READALL_OK = "✅ Every conversation marked read"
INBOX_GALLERY_TITLE = "🗂 <b>Files from {who}</b>"
INBOX_GALLERY_EMPTY = "This user hasn't sent any files yet."
INBOX_GALLERY_HINT = "Tap a button to have that file sent to you."
INBOX_STARRED_TITLE = "⭐ <b>Starred messages</b>"
INBOX_STARRED_EMPTY = "You haven't starred anything yet."
INBOX_MEDIA_SENT = "📤 Sent"
INBOX_MEDIA_MISSING = "❌ That message no longer exists."
INBOX_MEDIA_NONE = "❌ That message has no file."
INBOX_MEDIA_FAIL = "❌ Could not send the file: {error}"
INBOX_MEDIA_CAPTION = "from <b>{who}</b> · {when}"
INBOX_STAR_ON = "⭐ Starred"
INBOX_STAR_OFF = "☆ Star removed"
INBOX_NO_HISTORY = "no logged messages"
INBOX_COUNT = "👥 Users: <b>{count}</b>"

# ── live activity feed ────────────────────────────────────────────────
FEED_TITLE = "🔔 <b>Live activity</b>"
FEED_DROPPED = "<i>… and {count} more events (busy)</i>"
FEED_BTN_OPEN = "📂 Open chat"
FEED_BTN_MUTE = "🔕 Mute"
FEED_BTN_TOGGLE_ON = "🔔 Live activity: on"
FEED_BTN_TOGGLE_OFF = "🔕 Live activity: off"
FEED_ENABLED = "🔔 Live activity on — you'll get every user action as it happens."
FEED_DISABLED = "🔕 Live activity off."
BTN_BACK = "🔙 Back"
BTN_CLOSE = "✖️ Close"
BTN_CONFIRM_YES = "✅ Yes"
BTN_CONFIRM_NO = "❌ Cancel"
BTN_REDOWNLOAD = "🔄 Download again"
BTN_MP3 = "🎵 Download audio / music"
BTN_LINK = "🔗 Direct link"
BTN_JOINED = "✅ I joined; check"
BTN_JOIN = "📢 Join channel"

QUALITY_360 = "🟢 360p"
QUALITY_480 = "🟡 480p"
QUALITY_720 = "🔵 720p"
QUALITY_1080 = "🟣 1080p"
QUALITY_BEST = "⭐️ Best quality"
QUALITY_MP3 = "🎵 MP3 (320kbps)"

BTN_ADMIN_STATS = "📊 Bot stats"
BTN_ADMIN_TOP = "📈 Top platforms"
BTN_ADMIN_HEALTH = "🩺 Platform health"
BTN_ADMIN_ERRORS = "🐞 Recent errors"
BTN_ADMIN_BROADCAST = "📢 Broadcast"
BTN_ADMIN_BAN = "🚫 Ban user"
BTN_ADMIN_UNBAN = "🔓 Unban user"
BTN_ADMIN_BANLIST = "📋 Ban list"
BTN_ADMIN_CACHE = "⚡️ Cache"
BTN_ADMIN_CACHE_CLEAR = "🗑 Clear cache"
BTN_ADMIN_EXPORT = "📄 Export CSV"
BTN_ADMIN_USERS = "👥 Top users"
BTN_ADMIN_SETTINGS = "⚙️ Settings"
BTN_ADMIN_MAINTENANCE = "🔧 Maintenance"
BTN_ADMIN_STORAGE = "🧹 Storage"
BTN_ADMIN_CLEAN = "🗑 Clean up"
BTN_ADMIN_REFRESH = "🔄 Refresh"
BTN_ADMIN_CHARTS = "📈 Charts"
BTN_ADMIN_SERVICES = "🔧 Services"
BTN_ADMIN_FINDUSER = "🔍 Find user"
BTN_ADMIN_PLATFORMS = "🎛 Platforms"
BTN_ADMIN_LIMITS = "⚙️ Limits"
BTN_ADMIN_BACKUP = "🗄 DB backup"
BTN_ADMIN_RESTART = "🔁 Restart"
BTN_BC_ALL = "📢 All users"
BTN_BC_ACTIVE = "🔥 Active users only (7d)"
BTN_ALL_ITEMS = "📥 All ({count})"
BTN_FIRST_ONLY = "1️⃣ First only"

# ── admin panel: new sections ─────────────────────────────────────────
CHART_TITLE = "📈 <b>Last 7 days</b>"
CHART_DOWNLOADS = "⬇️ <b>Daily downloads</b>"
CHART_NEWUSERS = "🆕 <b>Daily new users</b>"
CHART_PEAK = "🕐 <b>Peak hours</b>"
SERVICES_TITLE = "🔧 <b>Service status</b>"
SERVICES_COOKIES = "🍪 <b>Cookies</b>"
PLATFORMS_TITLE = "🎛 <b>Platform control</b>"
PLATFORMS_HELP = "Tap a platform to turn it on/off."
PLATFORM_ON = "on"
PLATFORM_OFF = "off"
PLATFORM_DISABLED = "⛔️ This service is temporarily disabled. Try later."
LIMITS_TITLE = "⚙️ <b>Limits</b>"
LIMITS_BODY = (
    "🔢 Global concurrent: <b>{concurrent}</b>\n"
    "👤 Per-user concurrent: <b>{per_user}</b>\n"
    "🐌 Soft hourly threshold: <b>{soft}</b>\n\n"
    "<i>These are set via environment variables.</i>"
)
FINDUSER_PROMPT = "🔍 Send the numeric user ID:"
BACKUP_READY = "🗄 Database backup ready ({size})"
BACKUP_FAILED = "❌ Backup failed: {error}"
RESTART_CONFIRM = "🔁 <b>Restart the bot?</b>\n\nIt will drop for a few seconds and come back."
RESTART_NOW = "🔁 Restarting... give it a few seconds."

# ── cookies (admin only) ──────────────────────────────────────────────
COOKIE_TITLE = "🍪 <b>Cookie status</b>\n"
COOKIE_ROW_NONE = "⚪️ {platform}: not configured"
COOKIE_ROW_OK = "🟢 {platform}: healthy — {count} cookies, {days} days left (age: {age}d)"
COOKIE_ROW_BAD = "🔴 {platform}: problem — {reason}"
COOKIE_HELP = (
    "To enable, send the <code>cookies.txt</code> file here.\n"
    "The filename or caption must name the platform (e.g. <code>youtube.txt</code>).\n"
    "The \"Get cookies.txt LOCALLY\" browser extension produces this file.\n"
    "Remove with: <code>/cookies del youtube</code>"
)
COOKIE_SAVED = "✅ Cookies saved for {platform} — {count} cookies, {days} days left."
COOKIE_REMOVED = "🗑 Cookies for {platform} removed."
COOKIE_NONE = "No cookies were stored for {platform}."
COOKIE_USAGE = "Platform unclear. Use one of: {platforms}"
COOKIE_ERR_FORMAT = "❌ Unreadable file. It must be Netscape format (cookies.txt)."
COOKIE_ERR_DOMAIN = "❌ This file is not for {platform}"
COOKIE_ERR_SESSION = "❌ No session cookie in the file; log in before exporting"
COOKIE_ERR_EXPIRED = "❌ These cookies have expired; export them again."
COOKIE_ERR_BIG = "❌ File is too large."

# ── file to link (public) ─────────────────────────────────────────────
F2L_WORKING = "⏳ Uploading your file and creating a link..."
F2L_DONE = "✅ File uploaded\n\n📄 <b>{name}</b>\n📦 {size}\n🔗 {url}"
F2L_BUTTON = "⬇️ Download file"
F2L_FAILED = "❌ Upload failed. Please try again in a moment."
F2L_TOO_BIG = "❌ File is too large (max {limit} MB)."

# ── inline mode ───────────────────────────────────────────────────────
INLINE_HELP_TITLE = "Send a link to download"
INLINE_HELP_DESC = "Paste a YouTube/Instagram/... link after the bot name"
INLINE_HELP_MSG = "To download, put a link after @{username}."
INLINE_FETCH_TITLE = "⬇️ Download from {platform}"
INLINE_FETCH_DESC = "Tap to fetch the file at your chosen quality"
INLINE_FETCH_MSG = "🔗 {url}\n\nTap @{username} to download this link 👇"

# ── inline live media ─────────────────────────────────────────────────
INLINE_LIVE_DESC = "Downloads and posts right here in the chat"
INLINE_CACHED_DESC = "⚡ Ready — sends instantly, no wait"
INLINE_LIVE_MSG = "⏳ Downloading from {platform}... one moment"
INLINE_WORKING_BTN = "⏳ Downloading..."
INLINE_LIVE_FAIL = "❌ Download failed. Send the link to the bot directly."
INLINE_TOO_BIG = "📦 File is larger than the inline limit. Open the bot to get it 👇"
INLINE_OPEN_BTN = "⬇️ Get it from the bot"

# ── history / re-download ─────────────────────────────────────────────
BTN_HISTORY = "🕓 My history"
HISTORY_TITLE = "🕓 <b>Your recent downloads</b> ({count})\n\nTap any to fetch again:"
HISTORY_EMPTY = "You haven't downloaded anything yet. Send a link to start!"

# ── user features: favorites / settings / mystats / invite ────────────
BTN_FAVORITES = "⭐️ Favorites"
BTN_SETTINGS_USER = "⚙️ Settings"
BTN_INVITE = "🎁 Invite"
BTN_FAV_ON = "⭐️ Saved"
BTN_FAV_OFF = "☆ Add to favorites"
FAV_ADDED = "⭐️ Added to favorites"
FAV_REMOVED = "☆ Removed from favorites"
FAV_TITLE = "⭐️ <b>Your favorites</b> ({count})\n\nTap any to fetch again:"
FAV_EMPTY = "Nothing saved yet. Tap “☆ Add to favorites” under any download."

SETTINGS_USER_TITLE = (
    "{i_settings} <b>Personal settings</b>\n"
    "{div}\n"
    "Pick your default download quality and format.\n"
    "🟢 = your current choice\n\n"
    "<blockquote>💡 Set a default quality and the bot stops asking every time — downloads arrive faster.</blockquote>"
)
SET_QUALITY_LABEL = "— Default quality —"
SET_Q_ASK = "Ask each time"
SET_Q_BEST = "Best"
SET_FORMAT_LABEL = "— Default format —"
SET_F_VIDEO = "🎬 Video"
SET_F_AUDIO = "🎵 Audio"
SET_NOTIFY = "Result alerts"
SET_SAVED = "✅ Saved"

# Sent as a fresh reply when a queued job finishes, but only if the user waited
# in a queue AND has result alerts on.
NOTIFY_READY = "🔔 Your download is ready 👆"

MYSTATS = (
    "{i_stats} <b>My stats</b>\n"
    "{div}\n"
    "{i_download} Total downloads: <b>{total}</b>\n"
    "{i_date} This week: <b>{week}</b>\n"
    "{i_disk} Total size: <b>{bytes}</b>\n"
    "{i_star} Top platform: <b>{top}</b>\n"
    "{i_invite} Invited: <b>{refs}</b>\n"
    "{i_clock} Joined: <b>{joined}</b>\n\n"
    "{i_chart} <b>Last 7 days</b>\n{chart}"
)

INVITE_TEXT = (
    "{i_invite} <b>Invite friends</b>\n"
    "{div}\n"
    "This is your personal link — anyone who starts the bot with it is credited to you:\n\n"
    "<code>{link}</code>\n\n"
    "👥 You've invited <b>{count}</b> people so far."
)
# ── subtitles ─────────────────────────────────────────────────────────
BTN_SUBTITLE = "📝 Subtitle"
SUB_NONE = "No subtitles found for this video."
SUB_READY = "📝 Subtitle ready"
SUB_FETCHING = "⏳ Fetching subtitle..."

# ── instagram story / profile pic ─────────────────────────────────────
HINT_STORY_COOKIE = "To download Instagram stories, the admin must install an instagram.com cookie."

# ── theme / UI personalization (admin) ────────────────────────────────
BTN_ADMIN_THEME = "🎨 Appearance"
THEME_TITLE = "{i_brand} <b>Appearance</b>"
THEME_PREVIEW = "Live preview"
THEME_STATE = (
    "🎨 Active theme: <b>{preset}</b>\n"
    "✏️ Customized slots: <b>{edited}</b> of {total}\n"
    "💎 Premium emoji in text: <b>{premium}</b>\n"
    "🎨 Button colours: <b>{blue}</b>\n"
    "💠 Premium emoji on buttons: <b>{icons}</b>\n"
    "🎯 Current colours: {rolebar}"
)
THEME_HELP = (
    "<blockquote>Tap a slot and send the emoji you want.\n"
    "Send a <b>premium emoji</b> and the bot reads and stores its id automatically.\n"
    "Send <code>-</code> to restore a slot to its default.</blockquote>"
)
THEME_BTN_PREMIUM = "💎 Premium emoji"
THEME_BTN_BLUE = "🔵 Blue buttons"
ON = "on"
OFF = "off"
THEME_BTN_PACK = "📦 Install premium emoji pack"
THEME_PACK_PROMPT = (
    "📦 <b>Install emoji pack</b>\n"
    "{div}\n"
    "Send any of these:\n"
    "• pack link: <code>t.me/addemoji/NAME</code>\n"
    "• or just one <b>premium emoji</b> from that pack\n"
    "• or the pack name: <code>NewsEmoji</code>\n\n"
    "Send several and they all get added."
)
THEME_PACK_OK = "✅ Pack installed — <b>{count}</b> emoji ready\n📦 {packs}"
THEME_PACK_SKIPPED = "⚠️ Not found: {packs}"
THEME_PACK_FAIL = "❌ Could not read any pack from \"{packs}\". Previous packs restored."
THEME_PACK_BAD = "❌ No pack link or premium emoji found. Please send it again."
THEME_BTN_STYLES = "🎨 Button colours"
THEME_BTN_ICONS = "💠 Premium emoji on buttons"
THEME_ROLE_MENU = "Menu"
THEME_ROLE_ADMIN = "Admin"
THEME_ROLE_QUALITY = "Quality"
THEME_ROLE_CONFIRM = "Confirm"
THEME_ROLE_CANCEL = "Cancel"
THEME_ROLE_ACTION = "Action"
THEME_ROLE_NAV = "Back"

THEME_BTN_RESET = "♻️ Reset to defaults"
THEME_SLOT_PROMPT = (
    "✏️ Send the new emoji for <code>{slot}</code>.\n\n"
    "<blockquote>Plain or premium emoji both work. "
    "Send <code>-</code> for the default.</blockquote>"
)
THEME_SLOT_SAVED = "✅ Slot <code>{slot}</code> is now: {value}"
THEME_SLOT_RESET = "♻️ Slot <code>{slot}</code> restored to default."
THEME_SLOT_BAD = "❌ No such slot."
THEME_RESET_DONE = "♻️ Appearance reset to defaults."

# Short labels reused by the theme preview.
PROCESSING_LABEL = "Checking"
DOWNLOADING_LABEL = "Downloading"
DOWNLOAD_SUCCESS_LABEL = "Done"
THEME_BTN_SEP_MINUS = "➖ Shorter line"
THEME_BTN_SEP_PLUS = "➕ Longer line"
THEME_BTN_EXPORT = "📤 Export theme"
THEME_BTN_IMPORT = "📥 Import theme"
THEME_EXPORT = (
    "📤 <b>Current theme</b>\n\n"
    "<pre>{json}</pre>\n"
    "Save this text; restore it any time with “📥 Import theme”."
)
THEME_IMPORT_PROMPT = (
    "📥 Send the theme JSON.\n\n"
    "<blockquote>Exactly what “📤 Export theme” gave you.</blockquote>"
)
THEME_IMPORT_OK = "✅ Theme imported."
THEME_IMPORT_BAD = "❌ Could not read that JSON. Send the full exported text."
UPLOADING_LABEL = "Uploading"
FROM_CACHE_LABEL = "from cache"


TELEGRAM_STORY_UNSUPPORTED = "Telegram stories can't be downloaded by a bot (Telegram restricts story access for bots). Send a link to a channel/group post instead, e.g. t.me/channel/123."

TGLOGIN_ASK_PHONE = "To enable Telegram story downloads, a user account must be logged in (a bot token can't access stories).\n\nSend the account's phone number with country code, e.g. <code>+15551234567</code>.\n\nTo cancel: /cancel"
TGLOGIN_ASK_CODE = "Send the login code Telegram just sent you.\n\n<b>Important:</b> put spaces between the digits so Telegram doesn't invalidate the code, e.g. <code>1 2 3 4 5</code>.\n\nTo cancel: /cancel"
TGLOGIN_ASK_PASSWORD = "This account has two-step verification. Send the password.\n\nTo cancel: /cancel"
TGLOGIN_DONE = "Account <b>{who}</b> logged in. Telegram story downloads are now enabled."
TGLOGIN_ALREADY = "An account (<b>{who}</b>) is already logged in. To log out: /tglogout"
TGLOGIN_NONE = "No account is logged in."
TGLOGOUT_DONE = "User account logged out and session cleared."
TGLOGIN_CANCELLED = "Cancelled."
TGLOGIN_ERROR = "Login error: <code>{error}</code>\nRun /tglogin again."
STORY_NEEDS_LOGIN = "Downloading Telegram stories needs a logged-in user account. Run /tglogin in DM (admin only)."
