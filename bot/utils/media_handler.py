"""Media helpers: HTTP download, ffprobe metadata, thumbnails, mp3, splitting."""
import asyncio
import json
import logging
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import httpx

from bot import config
from bot.utils.antiblock import random_ua

logger = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


# ── HTTP ──────────────────────────────────────────────────────────────
async def download_file(
    url: str,
    dest: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    referer: str = "",
    max_size: int = 0,
    platform: str = "",
) -> str:
    """Stream a URL to disk. Streaming keeps memory flat on large files.

    ``platform`` routes the fetch through that platform's proxy when one is
    configured (e.g. an Instagram CDN download over the Instagram proxy).
    """
    from bot.utils import net

    hdrs = {"User-Agent": random_ua(), "Accept": "*/*"}
    if referer:
        hdrs["Referer"] = referer
    if headers:
        hdrs.update(headers)
    limit = max_size or config.MAX_DOWNLOAD
    written = 0
    # Overall wall-clock deadline so a server that dribbles bytes just under the
    # per-read timeout can't pin a queue worker forever. Scales with the size
    # cap (roughly assume >=1 Mbps) but never less than 2 minutes.
    overall_deadline = max(120.0, (limit / (1024 * 1024)) * 8.0)
    start = asyncio.get_event_loop().time()
    try:
        async with net.aclient(
            platform=platform, follow_redirects=True,
            timeout=httpx.Timeout(60.0, read=300.0), headers=hdrs
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async with aiofiles.open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(262_144):
                        written += len(chunk)
                        if written > limit:
                            raise ValueError(f"file exceeds {limit // 1024 // 1024}MB")
                        if asyncio.get_event_loop().time() - start > overall_deadline:
                            raise TimeoutError(
                                f"download exceeded {int(overall_deadline)}s wall-clock limit")
                        await f.write(chunk)
    except BaseException:
        # Any failure (size cap, timeout, connection reset mid-stream, cancel)
        # must not leave a partial file behind — disk is tight and orphaned
        # temp files only get cleaned on the age-based sweep much later.
        try:
            os.remove(dest)
        except OSError:
            pass
        raise
    return dest


async def head_info(url: str, *, platform: str = "") -> Dict[str, object]:
    """Content-Length / Content-Type without pulling the body.

    Routed through ``net.aclient`` so the configured proxy applies — a raw
    client would probe from the (often blocked) datacenter IP and 403/hang
    where the proxied download path succeeds.
    """
    from bot.utils import net

    hdrs = {"User-Agent": random_ua()}
    try:
        async with net.aclient(platform=platform, follow_redirects=True,
                               timeout=25.0, headers=hdrs) as c:
            r = await c.head(url)
            if r.status_code >= 400 or "content-length" not in r.headers:
                # Some CDNs reject HEAD; ask for one byte instead.
                r = await c.get(url, headers={**hdrs, "Range": "bytes=0-0"})
            size = 0
            if "content-range" in r.headers:
                m = re.search(r"/(\d+)$", r.headers["content-range"])
                if m:
                    size = int(m.group(1))
            if not size:
                size = int(r.headers.get("content-length") or 0)
            return {
                "size": size,
                "content_type": r.headers.get("content-type", ""),
                "filename": _filename_from_headers(r.headers, url),
                "final_url": str(r.url),
            }
    except Exception as exc:
        logger.debug("head_info failed for %s: %s", url, exc)
        return {"size": 0, "content_type": "", "filename": "", "final_url": url}


def _filename_from_headers(headers, url: str) -> str:
    cd = headers.get("content-disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    if m:
        return sanitize_filename(m.group(1))
    from urllib.parse import unquote, urlparse

    name = os.path.basename(urlparse(url).path)
    return sanitize_filename(unquote(name)) if name else ""


def sanitize_filename(name: str, fallback: str = "file") -> str:
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "_", name or "").strip(" ._")
    return (name[:120] or fallback)


# ── filesystem ────────────────────────────────────────────────────────
async def delete_file(*paths: str) -> None:
    for path in paths:
        if not path:
            continue
        try:
            if os.path.exists(path):
                await asyncio.to_thread(os.remove, path)
        except Exception as exc:
            logger.warning("Failed to delete %s: %s", path, exc)


def get_file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


# ── ffmpeg / ffprobe ──────────────────────────────────────────────────
async def _run(cmd: List[str], timeout: int = 300) -> Tuple[int, bytes, bytes]:
    # stdin MUST be /dev/null. ffmpeg treats an inherited stdin as an interactive
    # command channel; running under the supervisor (PPID=1, stdin wired to a
    # pipe) it reads stray bytes as keystrokes and a 'q' quits it the instant it
    # finishes printing the input header — the encode never starts, exit is
    # non-zero, and the original (unplayable) file is what ships. Manual runs in
    # a real shell have an idle tty for stdin, which is why this only bit in
    # production. See also the explicit -nostdin on the ffmpeg commands.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return proc.returncode or 0, out, err


async def probe_media(path: str) -> Dict[str, int]:
    """Return duration/width/height so Telegram renders a real video player."""
    result = {"duration": 0, "width": 0, "height": 0}
    try:
        code, out, _ = await _run(
            [
                FFPROBE, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "default=noprint_wrappers=1:nokey=0", path,
            ],
            timeout=60,
        )
        if code != 0:
            return result
        for line in out.decode(errors="ignore").splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if k == "width" and v.isdigit():
                result["width"] = int(v)
            elif k == "height" and v.isdigit():
                result["height"] = int(v)
            elif k == "duration":
                try:
                    result["duration"] = int(float(v))
                except ValueError:
                    pass
    except Exception as exc:
        logger.debug("probe failed for %s: %s", path, exc)
    return result


async def probe_codecs(path: str) -> Dict[str, Any]:
    """Return the actual codecs inside a container, not just its extension.

    A file called ``.mp4`` can legally carry VP9 video and Opus audio, which is
    exactly what YouTube serves above 1080p — and exactly what an iPhone refuses
    to play. Only ffprobe knows what is really inside.
    """
    info: Dict[str, Any] = {
        "vcodec": "", "acodec": "", "width": 0, "height": 0, "duration": 0,
        "pix_fmt": "", "sample_rate": 0,
    }
    try:
        code, out, _ = await _run(
            [
                FFPROBE, "-v", "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,pix_fmt,sample_rate"
                ":format=duration",
                "-of", "json", path,
            ],
            timeout=60,
        )
        if code != 0:
            return info
        data = json.loads(out.decode(errors="ignore") or "{}")
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video" and not info["vcodec"]:
                info["vcodec"] = (stream.get("codec_name") or "").lower()
                info["pix_fmt"] = (stream.get("pix_fmt") or "").lower()
                info["width"] = int(stream.get("width") or 0)
                info["height"] = int(stream.get("height") or 0)
            elif stream.get("codec_type") == "audio" and not info["acodec"]:
                info["acodec"] = (stream.get("codec_name") or "").lower()
                try:
                    info["sample_rate"] = int(stream.get("sample_rate") or 0)
                except (TypeError, ValueError):
                    pass
        try:
            info["duration"] = int(float(data.get("format", {}).get("duration") or 0))
        except (TypeError, ValueError):
            pass
    except Exception as exc:
        logger.debug("codec probe failed for %s: %s", path, exc)
    return info


async def ensure_ios_compatible(path: str) -> str:
    """Repair a video that an iPhone cannot open, and return the path to send.

    Background: yt-dlp is asked for H.264+AAC first, but some sources only
    publish VP9/AV1 video or Opus audio. Those files carry an ``.mp4`` extension
    and play fine on Android and Telegram Desktop, while on iOS they fail to
    open at all — the symptom that started this. Rather than warn the user, make
    the file playable.

    Three cases:

    * already H.264 + AAC — returned untouched, zero cost.
    * H.264 video with a non-AAC track — audio-only re-encode with ``-c:v copy``.
      Seconds, no quality loss on the video.
    * anything else — full video transcode to H.264.

    The full transcode is the expensive path, so it is bounded by
    ``IOS_COMPAT_MAX_PIXELS`` and ``IOS_COMPAT_MAX_DURATION``. Beyond those
    limits the original is returned as-is: delivering a large file that some
    phones can play beats making the user wait many minutes, or timing out and
    delivering nothing. ``+faststart`` is always applied on a rewrite so the
    video starts playing before it has fully downloaded.

    Returns the original path on any failure — never raises, never loses the file.
    """
    if not config.IOS_COMPAT:
        return path

    info = await probe_codecs(path)
    vcodec, acodec = info["vcodec"], info["acodec"]
    if not vcodec:
        return path  # not a video, or unreadable — leave it alone

    video_ok = vcodec in config.IOS_VIDEO_CODECS
    audio_ok = (not acodec) or acodec in config.IOS_AUDIO_CODECS
    # iOS decodes 8-bit 4:2:0 reliably. A 10-bit (yuv420p10le) or 4:2:2/4:4:4
    # H.264/HEVC stream is still "the right codec" but fails to open on many
    # iPhones, so treat an exotic pixel format as needing a re-encode too.
    pix_ok = (not info.get("pix_fmt")) or info["pix_fmt"] in config.IOS_PIX_FMTS
    # 44.1k and 48k both play; anything else (e.g. Opus at 24k) gets normalised.
    rate = info.get("sample_rate") or 0
    rate_ok = (not rate) or rate in config.IOS_AUDIO_RATES_OK

    if video_ok and audio_ok and pix_ok and rate_ok:
        # Codecs are already iOS-friendly. One thing can still keep it from
        # opening on iPhone: moov atom at the tail. Fix that with a cheap
        # stream-copy remux, no re-encode.
        if not await _has_faststart(path):
            return await _remux_faststart(path)
        return path

    audio_args = [
        "-c:a", "aac", "-profile:a", "aac_low",
        "-b:a", config.IOS_AUDIO_BITRATE,
        "-ar", str(config.IOS_AUDIO_RATE), "-ac", "2",
    ]
    # Colour metadata: deliberately NOT tagged. The reference file that plays on
    # both iPhone and Android carries color_space/transfer/primaries = unknown,
    # so forcing explicit bt709 tags would deviate from the confirmed-good spec.
    color_args: list = []

    pixels = (info["width"] or 0) * (info["height"] or 0)
    duration = info["duration"] or 0
    out = f"{os.path.splitext(path)[0]}_ios.mp4"

    if video_ok and pix_ok:
        # Only the audio is wrong. Copy the video stream through: cheap and lossless.
        # Audio is normalised to the iOS reference profile: AAC-LC, 48kHz, stereo.
        cmd = [
            FFMPEG, "-nostdin", "-y",
            "-threads", "1", "-i", path,
            "-c:v", "copy",
            *audio_args,
            "-movflags", "+faststart",
            "-max_muxing_queue_size", "64",
            "-threads", "1", out,
        ]
        budget = 600
        what = f"audio {acodec or 'none'}@{rate or '?'}->aac{config.IOS_AUDIO_RATE // 1000}k"
    else:
        if duration and duration > config.IOS_COMPAT_MAX_DURATION:
            logger.info(
                "iOS repair skipped for %s: %ds exceeds the duration budget",
                os.path.basename(path), duration,
            )
            return path
        # ALWAYS normalise the frame size on a re-encode, not just when over a
        # budget. The reference file that plays on both platforms is 720x1280 and
        # declares H.264 level 3.1, which only covers up to 720p — emitting a
        # 1080p or 1440p stream with that level tag is out of spec and is one of
        # the reasons iOS refused the file. The filter is expressed on the SHORT
        # side so portrait (1440x2560 -> 720x1280) and landscape both work, and
        # `-2` keeps the other dimension even, which H.264 requires. Videos
        # already at or below the cap are left at their native size (the scale
        # filter's min() keeps it from upscaling).
        cap = config.IOS_COMPAT_SCALE_SHORT
        vf = ["-vf", (
            f"scale="
            f"'if(gt(iw,ih),-2,min({cap},iw))'"
            f":'if(gt(iw,ih),min({cap},ih),-2)'"
        )]
        if pixels and pixels > cap * cap * 4:
            logger.info(
                "iOS repair downscaling %s: %dx%d -> short side %d",
                os.path.basename(path), info["width"], info["height"], cap,
            )
        # Thread discipline, learned the hard way: ffmpeg spawns SEPARATE thread
        # pools for decoding, filtering, and encoding. Capping only the encoder
        # (`-threads N` after the input) still let the vp9/h264 DECODER fan out
        # across all 48 cores, and on a 1440x2560 source that pushed the process
        # past the ~950MB cgroup limit — SIGKILL (exit -9) before a single frame
        # was written, logged as "iOS repair failed". Measured on E_big10bit:
        #   decode=auto encode=2 -> killed | decode=1 encode=1 filter=1 -> ok
        # `-threads 1` BEFORE -i caps the decoder; the pair after the input caps
        # the filter graph and the encoder.
        cmd = [
            FFMPEG, "-nostdin", "-y",
            "-threads", "1", "-i", path,
            "-filter_threads", "1",
            *vf,
            "-c:v", "libx264",
            "-profile:v", config.IOS_H264_PROFILE,
            "-level", config.IOS_H264_LEVEL,
            "-preset", config.IOS_COMPAT_PRESET,
            "-crf", str(config.IOS_COMPAT_CRF), "-pix_fmt", "yuv420p",
            *color_args,
            # Constant frame rate: a VFR source (common on Instagram) can make
            # iOS refuse to play or desync audio.
            "-fps_mode", "cfr",
            "-x264-params", config.IOS_X264_PARAMS,
            *audio_args,
            "-movflags", "+faststart",
            "-max_muxing_queue_size", "64",
            "-threads", str(config.IOS_COMPAT_THREADS), out,
        ]
        # Allow generous headroom but never unbounded: a hung ffmpeg would
        # otherwise pin a worker forever.
        budget = max(600, min(3600, (duration or 60) * 6))
        what = f"video {vcodec}->h264"

    started = time.monotonic()
    try:
        code, _, err = await _run(cmd, timeout=budget)
    except asyncio.TimeoutError:
        logger.warning("iOS repair timed out after %ds for %s", budget, path)
        await delete_file(out)
        return path
    except Exception as exc:
        logger.warning("iOS repair error for %s: %s", path, exc)
        await delete_file(out)
        return path

    if code != 0 or get_file_size(out) == 0:
        logger.warning(
            "iOS repair failed (%s): %s", what, err.decode(errors="ignore")[-300:]
        )
        await delete_file(out)
        return path

    logger.info(
        "iOS repair ok (%s) in %.1fs: %s -> %s",
        what, time.monotonic() - started,
        human_size(get_file_size(path)), human_size(get_file_size(out)),
    )
    await delete_file(path)
    return out


async def _has_faststart(path: str) -> bool:
    """True if the mp4 moov atom sits before mdat (progressive-play layout).

    The reference file that plays on iPhone has ``[ftyp, moov, ... mdat]``; a
    DASH merge often leaves ``moov`` at the very end, which streams badly and is
    the difference between an iOS-playable file and one that just spins. Reading
    only the top-level atom headers is cheap — we seek over each atom's payload,
    never read it.
    """
    def _scan() -> bool:
        try:
            with open(path, "rb") as f:
                moov = mdat = -1
                idx = 0
                while True:
                    hdr = f.read(8)
                    if len(hdr) < 8:
                        break
                    size = int.from_bytes(hdr[:4], "big")
                    typ = hdr[4:8].decode("latin1", "ignore")
                    if typ == "moov" and moov < 0:
                        moov = idx
                    elif typ == "mdat" and mdat < 0:
                        mdat = idx
                    if moov >= 0 and mdat >= 0:
                        break
                    if size == 1:  # 64-bit largesize
                        size = int.from_bytes(f.read(8), "big")
                        f.seek(max(0, size - 16), 1)
                    elif size == 0:  # extends to EOF
                        break
                    else:
                        f.seek(max(0, size - 8), 1)
                    idx += 1
                # No mdat seen (unusual) → treat as fine; only a moov that comes
                # AFTER mdat is the problem we fix.
                return mdat < 0 or (moov >= 0 and moov < mdat)
        except Exception:
            return True  # never block delivery on a probe hiccup

    return await asyncio.to_thread(_scan)


async def _remux_faststart(path: str) -> str:
    """Cheap stream-copy remux to pull moov to the front. No re-encode."""
    out = f"{os.path.splitext(path)[0]}_fs.mp4"
    cmd = [
        FFMPEG, "-nostdin", "-y", "-i", path,
        "-c", "copy", "-movflags", "+faststart", out,
    ]
    try:
        code, _, err = await _run(cmd, timeout=300)
    except Exception as exc:
        logger.warning("faststart remux error for %s: %s", path, exc)
        await delete_file(out)
        return path
    if code != 0 or get_file_size(out) == 0:
        logger.warning("faststart remux failed: %s", err.decode(errors="ignore")[-200:])
        await delete_file(out)
        return path
    logger.info("faststart remux ok: %s", os.path.basename(path))
    await delete_file(path)
    return out


async def make_thumbnail(video_path: str, out_path: str = "") -> Optional[str]:
    """Grab a frame for the video preview bubble.

    Telegram requires JPEG under 200KB with sides <= 320px.
    """
    if not config.SEND_THUMBNAILS:
        return None
    out_path = out_path or os.path.join(
        config.TEMP_DIR, f"thumb_{os.path.basename(video_path)}.jpg"
    )
    meta = await probe_media(video_path)
    seek = max(0, min(3, (meta.get("duration") or 0) // 10))
    try:
        code, _, err = await _run(
            [
                FFMPEG, "-y", "-ss", str(seek), "-i", video_path,
                "-frames:v", "1",
                "-vf", "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease",
                "-q:v", "6", out_path,
            ],
            timeout=90,
        )
        if code != 0 or not os.path.exists(out_path):
            logger.debug("thumbnail ffmpeg failed: %s", err.decode(errors="ignore")[-200:])
            return None
        if get_file_size(out_path) > 200 * 1024:
            await _run([FFMPEG, "-y", "-i", out_path, "-q:v", "12", out_path + ".s.jpg"], 60)
            if os.path.exists(out_path + ".s.jpg"):
                await delete_file(out_path)
                return out_path + ".s.jpg"
        return out_path
    except Exception as exc:
        logger.debug("thumbnail error: %s", exc)
        return None


async def convert_to_mp3(input_path: str, output_path: str = "", *, bitrate: str = "320k") -> Optional[str]:
    output_path = output_path or os.path.splitext(input_path)[0] + ".mp3"
    try:
        code, _, err = await _run(
            [
                FFMPEG, "-y", "-i", input_path, "-vn", "-ar", "44100",
                "-ac", "2", "-b:a", bitrate, output_path,
            ],
            timeout=900,
        )
        if code == 0 and os.path.exists(output_path):
            return output_path
        logger.error("mp3 convert failed: %s", err.decode(errors="ignore")[-300:])
    except Exception as exc:
        logger.error("mp3 convert error: %s", exc)
    return None


async def embed_cover(audio_path: str, cover_path: str) -> Optional[str]:
    """Attach album art to an mp3 so Telegram shows it in the player."""
    out = os.path.splitext(audio_path)[0] + "_art.mp3"
    try:
        code, _, _ = await _run(
            [
                FFMPEG, "-y", "-i", audio_path, "-i", cover_path,
                "-map", "0:a", "-map", "1:v", "-c:a", "copy", "-c:v", "mjpeg",
                "-id3v2_version", "3",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)", out,
            ],
            timeout=300,
        )
        if code == 0 and os.path.exists(out):
            return out
    except Exception as exc:
        logger.debug("cover embed failed: %s", exc)
    return None


async def split_file(path: str, part_size: int = 0) -> List[str]:
    """Split a file into <=part_size chunks as the last-resort upload path."""
    part_size = part_size or config.SPLIT_PART_SIZE
    size = get_file_size(path)
    if size <= part_size:
        return [path]
    parts: List[str] = []
    index = 1
    async with aiofiles.open(path, "rb") as src:
        while True:
            chunk = await src.read(part_size)
            if not chunk:
                break
            part_path = f"{path}.part{index:03d}"
            async with aiofiles.open(part_path, "wb") as dst:
                await dst.write(chunk)
            parts.append(part_path)
            index += 1
    return parts


def guess_kind(path: str, content_type: str = "") -> str:
    """Classify a file so we can pick send_video / send_audio / send_photo."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    ct = (content_type or "").lower()
    if ext in {"mp4", "mkv", "webm", "mov", "avi", "m4v", "flv", "ts"} or ct.startswith("video/"):
        return "video"
    if ext in {"mp3", "m4a", "aac", "ogg", "opus", "wav", "flac"} or ct.startswith("audio/"):
        return "audio"
    if ext in {"jpg", "jpeg", "png", "webp", "heic", "bmp"} or ct.startswith("image/"):
        return "photo"
    if ext == "gif":
        return "animation"
    return "document"


def human_size(num: int) -> str:
    if num <= 0:
        return "0B"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f}{unit}" if unit != "B" else f"{int(num)}B"
        num /= 1024.0
    return f"{num:.1f}GB"


def human_duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
