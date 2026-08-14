from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional, List, Literal
from decimal import Decimal


# ---------- Editor Profile ----------
class ValidatedModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class EditorProfileUpdate(ValidatedModel):
    username: Optional[str] = Field(default=None, min_length=2, max_length=50)
    bio: Optional[str] = Field(default=None, max_length=1000)
    skills: Optional[List[str]] = Field(default=None, max_length=20)
    hourly_rate: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    location: Optional[str] = Field(default=None, max_length=100)
    category: Optional[Literal["Image Editor", "TikTok Editor", "Video Editor"]] = None
    is_available: Optional[bool] = None

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, value):
        if value is None:
            return value
        cleaned = list(dict.fromkeys(skill.strip() for skill in value if skill.strip()))
        if any(len(skill) > 50 for skill in cleaned):
            raise ValueError("Each skill must be 50 characters or fewer")
        return cleaned


# ---------- Requests ----------
class CreateRequestBody(ValidatedModel):
    editor_id: str
    project_title: str = Field(min_length=3, max_length=120)
    project_description: str = Field(min_length=20, max_length=5000)
    content_type: Optional[Literal["YouTube", "Social Media", "Advertisement", "Film", "Event", "Other"]] = "Other"
    source_duration_minutes: Optional[int] = Field(default=None, ge=1, le=10000)
    target_duration_minutes: Optional[int] = Field(default=None, ge=1, le=1000)
    output_format: Optional[str] = Field(default=None, max_length=50)
    aspect_ratio: Optional[Literal["16:9", "9:16", "1:1", "4:5", "Other"]] = "16:9"
    style_reference: Optional[str] = Field(default=None, max_length=500)
    required_deliverables: Optional[List[str]] = Field(default=None, max_length=20)
    budget_min: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    budget_max: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    requested_revision_limit: int = Field(default=2, ge=0, le=10)

    @field_validator("budget_max")
    @classmethod
    def validate_budget_range(cls, value, info):
        minimum = info.data.get("budget_min")
        if value is not None and minimum is not None and value < minimum:
            raise ValueError("Maximum budget must be greater than or equal to minimum budget")
        return value


class RequestActionBody(ValidatedModel):
    action: Literal["accept", "reject"]


class ProposalBody(ValidatedModel):
    amount: float = Field(gt=0, le=10_000_000)
    delivery_days: int = Field(ge=1, le=365)
    included_revisions: int = Field(ge=0, le=10)
    message: str = Field(min_length=10, max_length=1000)


class MediaPolicyUpdate(ValidatedModel):
    editor_download_allowed: bool
    retention_days: int = Field(default=30, ge=1, le=365)


class MediaReportBody(ValidatedModel):
    reason: Literal["unauthorized_download", "copyright", "privacy", "harassment", "other"]
    details: str = Field(min_length=10, max_length=1000)


class MultipartUploadInit(ValidatedModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=120)
    size: int = Field(gt=0)
    request_id: str
    purpose: Literal["chat_attachment", "project_source_file", "project_reference_file", "final_delivery", "dispute_evidence"]
    category: Optional[Literal["video", "viewOnceVideo"]] = None
    view_once: bool = False


class MultipartPart(ValidatedModel):
    part_number: int = Field(ge=1, le=10000)
    etag: str = Field(min_length=3, max_length=200)


class MultipartUploadComplete(ValidatedModel):
    parts: List[MultipartPart] = Field(min_length=1, max_length=10000)


class LifecycleReasonBody(ValidatedModel):
    reason: str = Field(min_length=10, max_length=1000)


class CancelDecisionBody(ValidatedModel):
    approve: bool
    reason: Optional[str] = Field(default=None, max_length=1000)


class FinalDeliveryBody(ValidatedModel):
    upload_id: str = Field(min_length=1, max_length=500)
    delivery_message: Optional[str] = Field(default=None, max_length=1000)


class AdminLifecycleBody(ValidatedModel):
    action: Literal["resume", "request_revision", "cancel", "refund", "complete"]
    reason: str = Field(min_length=10, max_length=1000)


# ---------- Chat ----------
class SendMessageBody(ValidatedModel):
    request_id: str
    text: Optional[str] = Field(default=None, max_length=5000)
    file_url: Optional[str] = Field(default=None, max_length=2048)
    file_type: Optional[Literal["image", "video", "document", "archive", "audio"]] = None


class UserProfileUpdate(ValidatedModel):
    username: Optional[str] = Field(default=None, min_length=2, max_length=50)
    district: Optional[Literal[
        "Colombo", "Gampaha", "Kalutara", "Kandy", "Matale", "Nuwara Eliya", "Galle", "Matara",
        "Hambantota", "Jaffna", "Kilinochchi", "Mannar", "Vavuniya", "Mullaitivu", "Batticaloa",
        "Ampara", "Trincomalee", "Kurunegala", "Puttalam", "Anuradhapura", "Polonnaruwa",
        "Badulla", "Monaragala", "Ratnapura", "Kegalle",
    ]] = None
    profile_picture: Optional[str] = Field(default=None, max_length=2048)
    phone: Optional[str] = Field(default=None, pattern=r"^(?:\+94|0)[0-9]{9}$")


class AccountDeletionBody(ValidatedModel):
    confirmation: Literal["DELETE"]
    password: Optional[str] = Field(default=None, max_length=128)
    current_password: Optional[str] = Field(default=None, max_length=128)
    google_credential: Optional[str] = Field(default=None, max_length=8192)
    reason: Optional[str] = Field(default=None, max_length=500)


# ---------- Payments ----------
class CreatePaymentBody(ValidatedModel):
    request_id: str
    delivery_id: Optional[str] = Field(default=None, max_length=500)
    # Legacy callers may still send these fields, but checkout identity is
    # always loaded from the authenticated profile in the payment router.
    address: Optional[str] = Field(default=None, max_length=200)
    city: Optional[str] = Field(default=None, max_length=100)


class CreateChatPaymentBody(ValidatedModel):
    """Chat checkout accepts an identifier only; pricing stays server-side."""
    request_id: str


class CreateQuoteBody(ValidatedModel):
    project_id: str
    amount: Decimal
    note: Optional[str] = Field(default=None, max_length=500)
    expires_at: Optional[datetime] = None
    expiry_days: Optional[int] = Field(default=None, ge=1, le=30)

    @field_validator("amount")
    @classmethod
    def finite_amount(cls, value):
        if not value.is_finite():
            raise ValueError("Amount must be a finite number")
        return value


class FinalQuoteBody(ValidatedModel):
    amount: Decimal
    note: Optional[str] = Field(default=None, max_length=500)
    expires_at: Optional[datetime] = None
    expiry_days: Optional[int] = Field(default=None, ge=1, le=30)

    @field_validator("amount")
    @classmethod
    def finite_amount(cls, value):
        if not value.is_finite():
            raise ValueError("Amount must be a finite number")
        return value


class InitiateQuotePaymentBody(ValidatedModel):
    quote_id: str
    address: Optional[str] = Field(default=None, max_length=200)
    city: Optional[str] = Field(default=None, max_length=100)


class PayoutActionBody(ValidatedModel):
    status: Literal["APPROVED", "PROCESSING", "PAID", "FAILED", "ADJUSTED"]
    reference: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=1000)


class PortfolioItemBody(ValidatedModel):
    upload_id: str
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1500)
    skills: List[str] = Field(default_factory=list, max_length=20)
    thumbnail_upload_id: Optional[str] = None


class PortfolioItemUpdate(ValidatedModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1500)
    skills: Optional[List[str]] = Field(default=None, max_length=20)


class RefundPaymentBody(ValidatedModel):
    reason: str = Field(min_length=5, max_length=500)


# ---------- Reviews ----------
class CreateReviewBody(ValidatedModel):
    request_id: str
    rating: int = Field(ge=1, le=5)
    comment: str = Field(max_length=2000)

    @field_validator("comment")
    @classmethod
    def validate_comment_len(cls, v):
        if len(v.strip()) < 100:
            raise ValueError("Review must be at least 100 characters")
        return v


# ---------- Admin ----------
class BanUserBody(ValidatedModel):
    is_banned: bool


class ApproveDeliveryBody(ValidatedModel):
    approve: bool
    admin_note: Optional[str] = Field(default=None, max_length=1000)


class IdentityReviewDecision(ValidatedModel):
    decision: Literal["approve", "reject"]
    note: Optional[str] = Field(default=None, max_length=1000)
