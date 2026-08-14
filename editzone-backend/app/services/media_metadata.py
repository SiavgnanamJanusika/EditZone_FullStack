import asyncio
import json
import os
import tempfile
import boto3

from app.config import settings
from app.db.mongodb import uploads_bucket


class MediaMetadataError(RuntimeError):
    pass


async def gridfs_video_duration(upload_id) -> float:
    """Probe trusted server-side bytes; never accept duration supplied by React."""
    stream = await uploads_bucket.open_download_stream(upload_id)
    path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="editzone-status-", suffix=".media", delete=False) as temp:
            path = temp.name
            while chunk := await stream.readchunk():
                temp.write(chunk)
        try:
            process = await asyncio.create_subprocess_exec(
                settings.FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                "-of", "json", path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise MediaMetadataError("Server video inspection is not configured") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise MediaMetadataError("Video inspection timed out") from exc
        if process.returncode != 0:
            raise MediaMetadataError((stderr.decode("utf-8", "replace") or "Invalid video metadata")[:300])
        duration = float(json.loads(stdout).get("format", {}).get("duration", 0))
        if duration <= 0:
            raise MediaMetadataError("Video duration is unavailable")
        return duration
    except (ValueError, json.JSONDecodeError) as exc:
        raise MediaMetadataError("Invalid video duration metadata") from exc
    finally:
        if path:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


async def s3_video_metadata(bucket: str, key: str) -> dict:
    """Probe a private S3 object through a short-lived signed URL."""
    client = boto3.client(
        "s3", region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )
    url = await asyncio.to_thread(
        client.generate_presigned_url, "get_object",
        Params={"Bucket": bucket, "Key": key}, ExpiresIn=300,
    )
    try:
        process = await asyncio.create_subprocess_exec(
            settings.FFPROBE_PATH, "-v", "error", "-show_entries",
            "format=format_name,duration:stream=codec_type,codec_name", "-of", "json", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise MediaMetadataError("Server video inspection is not configured") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise MediaMetadataError("Video inspection timed out") from exc
    if process.returncode != 0:
        raise MediaMetadataError((stderr.decode("utf-8", "replace") or "Invalid video")[:300])
    try:
        payload = json.loads(stdout)
        duration = float(payload.get("format", {}).get("duration", 0))
    except (ValueError, json.JSONDecodeError) as exc:
        raise MediaMetadataError("Invalid video metadata") from exc
    formats = set(str(payload.get("format", {}).get("format_name", "")).split(","))
    video_codecs = {item.get("codec_name") for item in payload.get("streams", []) if item.get("codec_type") == "video"}
    allowed_formats = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm"}
    allowed_codecs = {"h264", "hevc", "vp8", "vp9", "av1"}
    if duration <= 0 or not formats.intersection(allowed_formats) or not video_codecs.intersection(allowed_codecs):
        raise MediaMetadataError("Unsupported or corrupted final video")
    return {"duration": duration, "container": sorted(formats), "video_codecs": sorted(video_codecs)}
