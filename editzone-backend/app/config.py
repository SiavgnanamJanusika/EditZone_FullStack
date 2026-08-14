from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "EditZone API"
    ENV: str = "development"

    # Mongo
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "editzone"
    MONGO_CONNECT_TIMEOUT_MS: int = 10_000
    MONGO_SERVER_SELECTION_TIMEOUT_MS: int = 15_000
    MONGO_SOCKET_TIMEOUT_MS: int = 120_000
    MONGO_MAX_POOL_SIZE: int = 50
    MONGO_MIN_POOL_SIZE: int = 0
    MONGO_WAIT_QUEUE_TIMEOUT_MS: int = 15_000
    MONGO_STARTUP_RETRIES: int = 3
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "editzone-api"
    JWT_AUDIENCE: str = "editzone-web"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    SOCKET_CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Chat policy and lifecycle
    CHAT_RETENTION_DAYS_AFTER_COMPLETION: int = 30
    MEDIA_RETENTION_DAYS_AFTER_COMPLETION: int = 30
    DISPUTE_WINDOW_DAYS: int = 7
    CHAT_MESSAGE_EDIT_MINUTES: int = 15
    CHAT_MESSAGE_DELETE_MINUTES: int = 60
    CHAT_CONNECTION_RATE_LIMIT: int = 20
    CHAT_MESSAGE_RATE_LIMIT: int = 30
    CHAT_ROOM_MESSAGE_RATE_LIMIT: int = 120
    CHAT_TYPING_RATE_LIMIT: int = 60
    CHAT_ROOM_TYPING_RATE_LIMIT: int = 240
    CHAT_RATE_LIMIT_WINDOW_SECONDS: int = 60
    VIEW_ONCE_TOKEN_EXPIRE_SECONDS: int = 120

    # File uploads (local fallback; swap for Cloudinary/S3 in production)
    UPLOAD_DIR: str = "app/uploads"
    # Category-specific upload policy. MAX_UPLOAD_MB/MAX_CHAT_VIDEO_MB remain
    # compatibility aliases but never lower the category limits below.
    MAX_UPLOAD_MB: int = 1000
    MAX_CHAT_ATTACHMENT_MB: int = 1000
    MAX_CHAT_VIDEO_MB: int = 1000
    MAX_CHAT_FILE_MB: int = 1000
    MAX_PROJECT_MEDIA_MB: int = 1000
    FINAL_DELIVERY_MAX_BYTES: int = 1_000_000_000
    MAX_PROFILE_IMAGE_MB: int = 10
    MAX_CHAT_IMAGE_MB: int = 1000
    MAX_CHAT_AUDIO_MB: int = 25
    MAX_VOICE_MESSAGE_MB: int = 25
    VOICE_MESSAGE_MAX_SECONDS: int = 600
    MAX_AUDIO_MB: int = 1000
    MAX_DOCUMENT_MB: int = 1000
    MAX_ZIP_MB: int = 1000
    MAX_VIDEO_MB: int = 1000
    MAX_STATUS_IMAGE_MB: int = 25
    MAX_STATUS_VIDEO_MB: int = 150
    MAX_REEL_IMAGE_MB: int = 20
    MAX_REEL_VIDEO_MB: int = 500
    STATUS_LIFETIME_HOURS: int = 24
    STATUS_VIDEO_MAX_SECONDS: float = 90.0
    MAX_STATUS_VIDEO_DURATION_SECONDS: float = 90.0
    MAX_REEL_VIDEO_DURATION_SECONDS: float = 90.0
    FFPROBE_PATH: str = "ffprobe"
    STATUS_CAPTION_MAX_LENGTH: int = 300
    PROFILE_IMAGE_MAX_DIMENSION: int = 2048
    STATUS_IMAGE_MAX_DIMENSION: int = 4096
    IMAGE_MAX_PIXELS: int = 80_000_000
    MAX_VIEW_ONCE_VIDEO_MB: int = 1000
    MAX_FILES_PER_MESSAGE: int = 5
    MAX_TEXT_MESSAGE_LENGTH: int = 5000
    DIRECT_UPLOAD_MIN_MB: int = 25
    MEDIA_LINK_EXPIRE_MINUTES: int = 5
    MEDIA_RETENTION_DAYS: int = 30
    PROJECT_REQUEST_EXPIRY_DAYS: int = 7
    CLAMAV_HOST: str = "localhost"
    CLAMAV_PORT: int = 3310
    # Malware scanning is opt-in. Development installations commonly do not
    # run ClamAV; enabling it implicitly quarantines otherwise valid uploads.
    # Production deployments that require scanning must explicitly set this
    # to true and configure the scanner below.
    MEDIA_SCANNER_ENABLED: bool = False
    MEDIA_SCANNER_SOCKET: str = ""
    CLAMAV_CONNECT_TIMEOUT_SECONDS: int = 5
    MALWARE_SCAN_TIMEOUT_SECONDS: int = 120
    MEDIA_SCAN_TIMEOUT_SECONDS: int = 180
    MEDIA_SCAN_MAX_ATTEMPTS: int = 3
    MALWARE_SCAN_MAX_MB: int = 1000
    S3_UPLOAD_PREFIX: str = "editzone/project-media"
    S3_MULTIPART_EXPIRE_HOURS: int = 24
    S3_PRESIGNED_URL_EXPIRE_SECONDS: int = 900

    # Private editor identity verification images
    AWS_S3_BUCKET: str = ""
    # Preferred deployment name. AWS_S3_BUCKET remains supported for existing installs.
    AWS_S3_BUCKET_NAME: str = ""
    AWS_REGION: str = "ap-south-1"
    AWS_TEXTRACT_REGION: str = ""
    NIC_OCR_PROVIDER: str = "aws_textract"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    NIC_IMAGE_MAX_MB: int = 5
    NIC_OCR_MIN_CONFIDENCE: float = 80.0
    AWS_TEXTRACT_MIN_CONFIDENCE: float = 70.0
    AWS_REKOGNITION_SIMILARITY_THRESHOLD: float = 85.0
    SELFIE_MAX_UPLOAD_MB: int = 5
    SELFIE_VERIFICATION_MAX_ATTEMPTS: int = 3
    SELFIE_LIVENESS_TIMEOUT_SECONDS: int = 20
    LIVE_SELFIE_SESSION_MINUTES: int = 10
    SELFIE_MANUAL_REVIEW_MARGIN: float = 10.0
    IDENTITY_RATE_LIMIT_ATTEMPTS: int = 5
    IDENTITY_RATE_LIMIT_MINUTES: int = 15
    IDENTITY_DOCUMENT_RETENTION_DAYS: int = 7

    # Email (optional - for verification/OTP; console fallback if unset)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = "noreply@editzone.com"

    # Platform economics
    PLATFORM_COMMISSION_PERCENT: float = 15.0
    PROJECT_PLATFORM_COMMISSION_PERCENT: float = 10.0
    PROJECT_SERVICE_FEE_PERCENT: float = 2.0
    PLATFORM_CURRENCY: str = "LKR"

    # PayHere hosted checkout. The merchant secret must never be exposed to React.
    PAYHERE_MERCHANT_ID: str = ""
    PAYHERE_MERCHANT_SECRET: str = ""
    PAYHERE_APP_ID: str = ""
    PAYHERE_APP_SECRET: str = ""
    PAYHERE_SANDBOX: bool = True
    PAYHERE_MODE: str = "sandbox"
    PAYHERE_CURRENCY: str = "LKR"
    PAYHERE_SANDBOX_URL: str = "https://sandbox.payhere.lk/pay/checkout"
    PAYHERE_NOTIFY_URL: str = "http://localhost:8000/api/v1/payments/payhere/notify"
    PAYHERE_RETURN_URL: str = ""
    PAYHERE_CANCEL_URL: str = ""
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_PUBLIC_URL: str = ""
    # Stable Web Push VAPID credentials. The private key is server-only.
    WEB_PUSH_VAPID_PUBLIC_KEY: str = ""
    WEB_PUSH_VAPID_PRIVATE_KEY: str = ""
    WEB_PUSH_VAPID_SUBJECT: str = "mailto:admin@editzone.com"
    PROJECT_MIN_AMOUNT: float = 100.0
    PROJECT_MAX_AMOUNT: float = 10_000_000.0
    CLIENT_SERVICE_FEE_PERCENT: int = 10
    EDITOR_COMMISSION_PERCENT: int = 10
    EDITOR_COMMISSION_MODE: str = "DEDUCT_FROM_PAYOUT"
    APP_TIMEZONE: str = "Asia/Colombo"
    QUOTE_DEFAULT_EXPIRY_HOURS: int = 168
    GOOGLE_CLIENT_ID: str = ""

    # Authentication abuse protection / Cloudflare Turnstile
    TURNSTILE_SECRET_KEY: str = ""
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_LOCK_MINUTES: int = 15
    OTP_MAX_ATTEMPTS: int = 5
    OTP_EXPIRE_SECONDS: int = 300
    OTP_RESEND_SECONDS: int = 60
    FORGOT_PASSWORD_MAX_PER_HOUR: int = 3
    LOGIN_IP_MAX_FAILURES: int = 20
    OTP_EMAIL_MAX_PER_HOUR: int = 5
    OTP_IP_MAX_PER_HOUR: int = 20
    PASSWORD_RESET_IP_MAX_PER_HOUR: int = 10

    # Financial operations
    PAYMENT_RECONCILIATION_MINUTES: int = 15
    PAYOUT_HOLD_DAYS: int = 7

    @model_validator(mode="after")
    def validate_security_configuration(self):
        if self.AWS_S3_BUCKET_NAME.strip():
            self.AWS_S3_BUCKET = self.AWS_S3_BUCKET_NAME.strip()
        for field in (
            "PAYHERE_MERCHANT_ID", "PAYHERE_MERCHANT_SECRET", "PAYHERE_APP_ID",
            "PAYHERE_APP_SECRET", "PAYHERE_CURRENCY", "PAYHERE_SANDBOX_URL",
            "PAYHERE_NOTIFY_URL", "PAYHERE_RETURN_URL", "PAYHERE_CANCEL_URL",
            "FRONTEND_URL", "BACKEND_PUBLIC_URL",
        ):
            setattr(self, field, str(getattr(self, field) or "").strip())
        self.PAYHERE_MODE = self.PAYHERE_MODE.strip().lower()
        if self.PAYHERE_MODE not in {"sandbox", "live"}:
            raise ValueError("PAYHERE_MODE must be sandbox or live")
        if self.PAYHERE_MODE == "live" or not self.PAYHERE_SANDBOX:
            raise ValueError("Live PayHere mode is locked until sandbox verification is complete")
        if self.PAYHERE_CURRENCY.upper() != self.PLATFORM_CURRENCY.upper():
            raise ValueError("PAYHERE_CURRENCY must match PLATFORM_CURRENCY")
        normalized_secret = self.JWT_SECRET_KEY.strip().lower()
        placeholder_markers = ("change_me", "change-me", "changeme", "replace-with", "example", "placeholder")
        if not normalized_secret or any(marker in normalized_secret for marker in placeholder_markers):
            raise ValueError("JWT_SECRET_KEY must be supplied by the environment and must not be a placeholder")
        if len(self.JWT_SECRET_KEY) < 32:
            raise ValueError("JWT_SECRET_KEY must be supplied by the environment and contain at least 32 characters")
        if self.ENV.lower() == "production":
            if len(self.JWT_SECRET_KEY) < 64:
                raise ValueError("JWT_SECRET_KEY must contain at least 64 characters in production")
            if self.JWT_ALGORITHM != "HS256":
                raise ValueError("Unsupported production JWT algorithm")
            if not self.JWT_ISSUER or not self.JWT_AUDIENCE:
                raise ValueError("JWT_ISSUER and JWT_AUDIENCE are required in production")
            if any(origin == "*" for origin in self.CORS_ORIGINS):
                raise ValueError("Wildcard CORS origins are forbidden in production")
            required = {
                "MONGO_URI": self.MONGO_URI, "MONGO_DB_NAME": self.MONGO_DB_NAME,
                "REDIS_URL": self.REDIS_URL,
                "JWT_ISSUER": self.JWT_ISSUER, "JWT_AUDIENCE": self.JWT_AUDIENCE,
                "AWS_S3_BUCKET": self.AWS_S3_BUCKET, "SMTP_HOST": self.SMTP_HOST,
                "SMTP_USER": self.SMTP_USER, "SMTP_PASSWORD": self.SMTP_PASSWORD,
                "TURNSTILE_SECRET_KEY": self.TURNSTILE_SECRET_KEY,
                "PAYHERE_MERCHANT_ID": self.PAYHERE_MERCHANT_ID,
                "PAYHERE_MERCHANT_SECRET": self.PAYHERE_MERCHANT_SECRET,
                "PAYHERE_APP_ID": self.PAYHERE_APP_ID,
                "PAYHERE_APP_SECRET": self.PAYHERE_APP_SECRET,
                "PAYHERE_NOTIFY_URL": self.PAYHERE_NOTIFY_URL,
                "FRONTEND_URL": self.FRONTEND_URL,
                "CLAMAV_HOST": self.CLAMAV_HOST,
            }
            missing = [
                name for name, value in required.items()
                if name not in self.model_fields_set or not str(value).strip()
            ]
            if missing:
                raise ValueError(f"Missing required production configuration: {', '.join(missing)}")
            local_only = {
                "MONGO_URI": self.MONGO_URI, "REDIS_URL": self.REDIS_URL,
                "PAYHERE_NOTIFY_URL": self.PAYHERE_NOTIFY_URL, "FRONTEND_URL": self.FRONTEND_URL,
            }
            invalid = [name for name, value in local_only.items() if "localhost" in value or "127.0.0.1" in value]
            if invalid:
                raise ValueError(f"Production configuration cannot use local-only endpoints: {', '.join(invalid)}")
        return self

settings = Settings()
