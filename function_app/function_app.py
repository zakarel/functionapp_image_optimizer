import hashlib
import io
import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import azure.functions as func
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings


app = func.FunctionApp()
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 40 * 1024 * 1024
MAX_DIMENSION = 4096
MAX_PIXELS = 16_777_216
MAX_QUERY_LENGTH = 512
try:
    DEFAULT_QUALITY = int(os.environ.get("DEFAULT_WEBP_QUALITY", "85"))
except ValueError as exc:
    raise RuntimeError("DEFAULT_WEBP_QUALITY must be an integer from 1 to 100.") from exc
if not 1 <= DEFAULT_QUALITY <= 100:
    raise RuntimeError("DEFAULT_WEBP_QUALITY must be an integer from 1 to 100.")
DEFAULT_CACHE_CONTROL = "public, max-age=3600"
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"

IMAGE_NAME_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,195}/)*[A-Za-z0-9][A-Za-z0-9._-]{0,195}\.(?:jpe?g|png)$",
    re.IGNORECASE,
)
HEX_COLOR_PATTERN = re.compile(r"^#?[0-9A-Fa-f]{6}$")
CROP_PATTERN = re.compile(
    r"^(?P<x>[1-9]\d{0,3}):(?P<y>[1-9]\d{0,3})(?:,(?P<gravity>[a-z-]+))?$",
    re.IGNORECASE,
)

SUPPORTED_PARAMS = {
    "auto",
    "bg-color",
    "blur",
    "brightness",
    "bw",
    "contrast",
    "crop",
    "dpr",
    "fit",
    "format",
    "height",
    "metadata",
    "optimize",
    "orient",
    "quality",
    "saturation",
    "sharpen",
    "trim",
    "width",
}
SUPPORTED_FORMATS = {"webp", "jpeg", "png"}
SUPPORTED_FITS = {"bounds", "contain", "cover", "crop", "fill"}
GRAVITIES = {
    "center": "Center",
    "north": "North",
    "south": "South",
    "east": "East",
    "west": "West",
    "north-east": "NorthEast",
    "north-west": "NorthWest",
    "south-east": "SouthEast",
    "south-west": "SouthWest",
    "smart": "Center",
}
CONTENT_TYPES = {
    "webp": "image/webp",
    "jpeg": "image/jpeg",
    "png": "image/png",
}
IMAGEMAGICK_FORMATS = {
    "webp": "webp",
    "jpeg": "jpeg",
    "png": "png",
}


class ImageMagickConversionError(Exception):
    pass


class InvalidTransformError(ValueError):
    pass


@dataclass(frozen=True)
class TransformOptions:
    width: int | None = None
    height: int | None = None
    dpr: float = 1.0
    fit: str = "bounds"
    crop_ratio: tuple[int, int] | None = None
    gravity: str = "Center"
    quality: int = DEFAULT_QUALITY
    output_format: str = "webp"
    background: str = "#FFFFFF"
    orient: str = "auto"
    trim: bool = False
    black_and_white: bool = False
    brightness: int = 0
    contrast: int = 0
    saturation: int = 100
    sharpen: float = 0.0
    blur: float = 0.0


def _blob_service() -> BlobServiceClient:
    return BlobServiceClient(
        account_url=f"https://{os.environ['IMAGE_STORAGE_ACCOUNT']}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )


def _source_blob_name(name: str) -> str:
    prefix = os.environ.get("SOURCE_PATH_PREFIX", "").strip("/")
    return f"{prefix}/{name}" if prefix else name


def _error(message: str, status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        message,
        status_code=status_code,
        headers={"X-TipRanks-IO-Error": message},
    )


def _parse_int(
    params: dict[str, str],
    name: str,
    *,
    minimum: int,
    maximum: int,
    default: int | None = None,
) -> int | None:
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise InvalidTransformError(f"{name} must be an integer.") from exc
    if value < minimum or value > maximum:
        raise InvalidTransformError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _parse_float(
    params: dict[str, str],
    name: str,
    *,
    minimum: float,
    maximum: float,
    default: float,
) -> float:
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise InvalidTransformError(f"{name} must be numeric.") from exc
    if value < minimum or value > maximum:
        raise InvalidTransformError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _parse_bool(params: dict[str, str], name: str, default: bool = False) -> bool:
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise InvalidTransformError(f"{name} must be true or false.")


def _parse_options(params: dict[str, str]) -> TransformOptions:
    unknown = sorted(set(params) - SUPPORTED_PARAMS)
    if unknown:
        raise InvalidTransformError(f"Unsupported parameter: {unknown[0]}.")

    width = _parse_int(params, "width", minimum=1, maximum=MAX_DIMENSION)
    height = _parse_int(params, "height", minimum=1, maximum=MAX_DIMENSION)
    dpr = _parse_float(params, "dpr", minimum=0.5, maximum=4.0, default=1.0)
    scaled_width = round(width * dpr) if width else None
    scaled_height = round(height * dpr) if height else None
    if scaled_width and scaled_width > MAX_DIMENSION:
        raise InvalidTransformError("width multiplied by dpr exceeds 4096.")
    if scaled_height and scaled_height > MAX_DIMENSION:
        raise InvalidTransformError("height multiplied by dpr exceeds 4096.")
    if scaled_width and scaled_height and scaled_width * scaled_height > MAX_PIXELS:
        raise InvalidTransformError("Requested dimensions exceed the 16 MP output limit.")

    crop_ratio = None
    gravity = "Center"
    crop = params.get("crop")
    if crop:
        match = CROP_PATTERN.fullmatch(crop)
        if not match:
            raise InvalidTransformError(
                "crop must use an aspect ratio such as 16:9 or 1:1,center."
            )
        crop_ratio = (int(match.group("x")), int(match.group("y")))
        gravity_name = (match.group("gravity") or "center").lower()
        if gravity_name not in GRAVITIES:
            raise InvalidTransformError(
                f"Unsupported crop gravity: {gravity_name}."
            )
        gravity = GRAVITIES[gravity_name]
        ratio = crop_ratio[0] / crop_ratio[1]
        if scaled_width and not scaled_height:
            scaled_height = max(1, round(scaled_width / ratio))
        elif scaled_height and not scaled_width:
            scaled_width = max(1, round(scaled_height * ratio))

    fit = params.get("fit", "bounds").lower()
    if crop_ratio and (scaled_width or scaled_height):
        fit = "crop"
    if fit not in SUPPORTED_FITS:
        raise InvalidTransformError(
            f"fit must be one of: {', '.join(sorted(SUPPORTED_FITS))}."
        )
    if fit in {"contain", "cover", "crop", "fill"} and not (
        scaled_width and scaled_height
    ):
        raise InvalidTransformError(f"fit={fit} requires both width and height.")
    if scaled_width and scaled_height and scaled_width * scaled_height > MAX_PIXELS:
        raise InvalidTransformError("Requested dimensions exceed the 16 MP output limit.")

    output_format = params.get("format", "").lower()
    auto = params.get("auto", "").lower()
    if not output_format and auto in {"format", "webp"}:
        output_format = "webp"
    output_format = output_format or "webp"
    if output_format == "jpg":
        output_format = "jpeg"
    if output_format not in SUPPORTED_FORMATS:
        raise InvalidTransformError(
            f"format must be one of: {', '.join(sorted(SUPPORTED_FORMATS))}."
        )

    metadata = params.get("metadata", "none").lower()
    if metadata not in {"none", "strip"}:
        raise InvalidTransformError(
            "Only metadata=none is supported; metadata is stripped by default."
        )
    optimize = params.get("optimize", "true").lower()
    if optimize not in {"1", "true", "yes", "on"}:
        raise InvalidTransformError("optimize currently supports only true.")

    background = params.get("bg-color", "#FFFFFF")
    if not HEX_COLOR_PATTERN.fullmatch(background):
        raise InvalidTransformError("bg-color must be a six-digit hexadecimal color.")
    if not background.startswith("#"):
        background = f"#{background}"

    orient = params.get("orient", "auto").lower()
    if orient not in {"auto", "0", "90", "180", "270"}:
        raise InvalidTransformError("orient must be auto, 0, 90, 180, or 270.")

    return TransformOptions(
        width=scaled_width,
        height=scaled_height,
        dpr=dpr,
        fit=fit,
        crop_ratio=crop_ratio,
        gravity=gravity,
        quality=_parse_int(
            params,
            "quality",
            minimum=1,
            maximum=100,
            default=DEFAULT_QUALITY,
        )
        or DEFAULT_QUALITY,
        output_format=output_format,
        background=background.upper(),
        orient=orient,
        trim=_parse_bool(params, "trim"),
        black_and_white=_parse_bool(params, "bw"),
        brightness=_parse_int(
            params, "brightness", minimum=-100, maximum=100, default=0
        )
        or 0,
        contrast=_parse_int(
            params, "contrast", minimum=-100, maximum=100, default=0
        )
        or 0,
        saturation=_parse_int(
            params, "saturation", minimum=0, maximum=200, default=100
        )
        or 0,
        sharpen=_parse_float(
            params, "sharpen", minimum=0.0, maximum=10.0, default=0.0
        ),
        blur=_parse_float(
            params, "blur", minimum=0.0, maximum=20.0, default=0.0
        ),
    )


def _resource_limits() -> list[str]:
    return [
        "-limit",
        "memory",
        "256MiB",
        "-limit",
        "map",
        "512MiB",
        "-limit",
        "disk",
        "512MiB",
        "-limit",
        "time",
        "25",
    ]


def _identify(data: bytes, image_format: str) -> tuple[int, int]:
    command = [
        "identify",
        *_resource_limits(),
        "-format",
        "%w %h",
        f"{image_format}:-",
    ]
    try:
        result = subprocess.run(
            command,
            input=data,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ImageMagickConversionError("Image inspection timed out.") from exc
    if result.returncode != 0:
        logger.warning(
            "ImageMagick identify failed: %s",
            result.stderr.decode(errors="replace")[-1000:],
        )
        raise ImageMagickConversionError("The source file is not a valid image.")
    try:
        width, height = (int(value) for value in result.stdout.split())
    except (ValueError, TypeError) as exc:
        raise ImageMagickConversionError(
            "ImageMagick returned invalid dimensions."
        ) from exc
    if width <= 0 or height <= 0 or width * height > 40_000_000:
        raise ImageMagickConversionError("The source image dimensions are unsupported.")
    return width, height


def _crop_geometry(
    source_width: int,
    source_height: int,
    ratio: tuple[int, int],
) -> tuple[int, int]:
    target_ratio = ratio[0] / ratio[1]
    source_ratio = source_width / source_height
    if source_ratio > target_ratio:
        return round(source_height * target_ratio), source_height
    return source_width, round(source_width / target_ratio)


def _build_transform_args(
    options: TransformOptions,
    source_dimensions: tuple[int, int],
) -> list[str]:
    args: list[str] = []
    if options.orient == "auto":
        args.append("-auto-orient")
    elif options.orient != "0":
        args.extend(["-rotate", options.orient])
    args.append("-strip")

    if options.trim:
        args.extend(["-trim", "+repage"])

    width = options.width
    height = options.height
    if options.crop_ratio and not width and not height:
        crop_width, crop_height = _crop_geometry(
            source_dimensions[0],
            source_dimensions[1],
            options.crop_ratio,
        )
        args.extend(
            [
                "-gravity",
                options.gravity,
                "-crop",
                f"{crop_width}x{crop_height}+0+0",
                "+repage",
            ]
        )
    if width or height:
        geometry = f"{width or ''}x{height or ''}"
        if options.fit == "bounds":
            args.extend(["-resize", f"{geometry}>"])
        elif options.fit in {"cover", "crop"}:
            args.extend(
                [
                    "-resize",
                    f"{geometry}^",
                    "-gravity",
                    options.gravity,
                    "-extent",
                    geometry,
                ]
            )
        elif options.fit == "contain":
            args.extend(
                [
                    "-resize",
                    f"{geometry}>",
                    "-background",
                    options.background,
                    "-gravity",
                    "Center",
                    "-extent",
                    geometry,
                ]
            )
        elif options.fit == "fill":
            args.extend(["-resize", f"{geometry}!"])

    if options.black_and_white:
        args.extend(["-colorspace", "Gray"])
    if options.brightness or options.contrast:
        args.extend(
            [
                "-brightness-contrast",
                f"{options.brightness}x{options.contrast}",
            ]
        )
    if options.saturation != 100:
        args.extend(["-modulate", f"100,{options.saturation},100"])
    if options.sharpen:
        args.extend(["-unsharp", f"0x{options.sharpen:g}"])
    if options.blur:
        args.extend(["-blur", f"0x{options.blur:g}"])

    if options.output_format in {"jpeg", "png"}:
        args.extend(
            [
                "-background",
                options.background,
                "-alpha",
                "remove",
                "-alpha",
                "off",
            ]
        )
    args.extend(["-quality", str(options.quality)])
    if options.output_format == "webp":
        args.extend(
            [
                "-define",
                "webp:method=6",
                "-define",
                "webp:auto-filter=true",
                "-define",
                "webp:image-hint=photo",
            ]
        )
    elif options.output_format == "jpeg":
        args.extend(["-interlace", "Plane", "-sampling-factor", "4:2:0"])
    elif options.output_format == "png":
        args.extend(["-define", "png:compression-level=9"])
    return args


def _detect_image_format(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    raise ImageMagickConversionError("The source file is not a supported JPEG or PNG image.")


def _transform_image(
    data: bytes,
    source_format: str,
    options: TransformOptions,
) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    source_dimensions = _identify(data, source_format)
    with tempfile.TemporaryDirectory(prefix="image-conversion-") as directory:
        extension = "jpg" if options.output_format == "jpeg" else options.output_format
        output_path = Path(directory) / f"output.{extension}"
        command = [
            "convert",
            *_resource_limits(),
            f"{source_format}:-",
            *_build_transform_args(options, source_dimensions),
            f"{IMAGEMAGICK_FORMATS[options.output_format]}:{output_path}",
        ]
        try:
            result = subprocess.run(
                command,
                input=data,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise ImageMagickConversionError("Image conversion timed out.") from exc

        if result.returncode != 0:
            logger.warning(
                "ImageMagick rejected transformation: %s",
                result.stderr.decode(errors="replace")[-1000:],
            )
            raise ImageMagickConversionError(
                "The image could not be transformed with these parameters."
            )

        output_size = output_path.stat().st_size
        if output_size == 0 or output_size > MAX_OUTPUT_BYTES:
            raise ImageMagickConversionError(
                "The transformed image has an invalid size."
            )
        encoded = output_path.read_bytes()
        output_dimensions = _identify(encoded, options.output_format)
        return encoded, source_dimensions, output_dimensions


def _variant_key(
    name: str,
    source_etag: str,
    options: TransformOptions,
) -> str:
    normalized = json.dumps(asdict(options), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{source_etag}:{normalized}".encode()).hexdigest()[:20]
    stem, _ = os.path.splitext(name)
    extension = "jpg" if options.output_format == "jpeg" else options.output_format
    return f"variants/{stem}/{digest}.{extension}"


def _io_info(
    source_size: int,
    source_dimensions: tuple[int, int],
    source_format: str,
    output_size: int,
    output_dimensions: tuple[int, int],
    output_format: str,
) -> str:
    return (
        f"ifsz={source_size} "
        f"idim={source_dimensions[0]}x{source_dimensions[1]} "
        f"ifmt={source_format} "
        f"ofsz={output_size} "
        f"odim={output_dimensions[0]}x{output_dimensions[1]} "
        f"ofmt={output_format}"
    )


@app.route(
    route="io/{*name}",
    methods=["GET", "HEAD"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def image_optimizer(req: func.HttpRequest) -> func.HttpResponse:
    name = req.route_params.get("name", "")
    if not IMAGE_NAME_PATTERN.fullmatch(name):
        return _error("Use a nested .jpg, .jpeg, or .png source path.", 400)
    if len(req.url.partition("?")[2]) > MAX_QUERY_LENGTH:
        return _error("The transformation query is too long.", 400)

    try:
        options = _parse_options(dict(req.params))
    except InvalidTransformError as exc:
        return _error(str(exc), 400)

    service = _blob_service()
    source_blob = service.get_blob_client(
        container=os.environ["IMAGE_INPUT_CONTAINER"],
        blob=_source_blob_name(name),
    )
    try:
        source_properties = source_blob.get_blob_properties()
        source_etag = str(source_properties.etag)
    except ResourceNotFoundError:
        return _error("Source image not found.", 404)

    variant_name = _variant_key(name, source_etag, options)
    variant_blob = service.get_blob_client(
        container=os.environ["IMAGE_OUTPUT_CONTAINER"],
        blob=variant_name,
    )
    try:
        variant_properties = variant_blob.get_blob_properties()
        body = b"" if req.method == "HEAD" else variant_blob.download_blob().readall()
        return func.HttpResponse(
            body=body,
            status_code=200,
            mimetype=CONTENT_TYPES[options.output_format],
            headers={
                "Cache-Control": IMMUTABLE_CACHE_CONTROL,
                "X-TipRanks-IO-Cache": "VARIANT_HIT",
                "X-TipRanks-IO-Variant": variant_name,
                "ETag": str(variant_properties.etag),
            },
        )
    except ResourceNotFoundError:
        pass

    source = source_blob.download_blob().readall()
    if len(source) > MAX_UPLOAD_BYTES:
        return _error("The source image exceeds the 20 MB processing limit.", 413)
    try:
        source_format = _detect_image_format(source)
        encoded, source_dimensions, output_dimensions = _transform_image(
            source,
            source_format,
            options,
        )
    except ImageMagickConversionError as exc:
        return _error(str(exc), 422)

    try:
        variant_blob.upload_blob(
            data=io.BytesIO(encoded),
            overwrite=False,
            content_settings=ContentSettings(
                content_type=CONTENT_TYPES[options.output_format],
                cache_control=IMMUTABLE_CACHE_CONTROL,
            ),
        )
    except ResourceExistsError:
        variant_properties = variant_blob.get_blob_properties()
        encoded = (
            b""
            if req.method == "HEAD"
            else variant_blob.download_blob().readall()
        )
        return func.HttpResponse(
            body=encoded,
            status_code=200,
            mimetype=CONTENT_TYPES[options.output_format],
            headers={
                "Cache-Control": IMMUTABLE_CACHE_CONTROL,
                "X-TipRanks-IO-Cache": "VARIANT_RACE_HIT",
                "X-TipRanks-IO-Variant": variant_name,
                "ETag": str(variant_properties.etag),
            },
        )
    return func.HttpResponse(
        body=b"" if req.method == "HEAD" else encoded,
        status_code=200,
        mimetype=CONTENT_TYPES[options.output_format],
        headers={
            "Cache-Control": IMMUTABLE_CACHE_CONTROL,
            "X-TipRanks-IO-Cache": "GENERATED",
            "X-TipRanks-IO-Variant": variant_name,
            "X-TipRanks-IO-Info": _io_info(
                len(source),
                source_dimensions,
                source_format,
                len(encoded),
                output_dimensions,
                options.output_format,
            ),
        },
    )


@app.route(
    route="health",
    methods=["GET", "HEAD"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(status_code=200)
