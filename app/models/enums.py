from enum import StrEnum


class CompanyStatus(StrEnum):
    # A company starts here after registration and stays here until a Super
    # Admin approves or rejects it — see AuthService.login's approval gate.
    pending_approval = "pending_approval"
    active = "active"
    rejected = "rejected"
    suspended = "suspended"


class UserStatus(StrEnum):
    pending = "pending"
    active = "active"
    suspended = "suspended"


class CompanyRole(StrEnum):
    company_admin = "company_admin"
    member = "member"


class CompanyMemberStatus(StrEnum):
    # No "invited" state: membership is always granted immediately, either
    # by direct add (existing user) or by emailing generated credentials
    # (new user) — see CompanyMemberService.add_member.
    active = "active"
    suspended = "suspended"


class SocialPlatform(StrEnum):
    instagram = "instagram"
    facebook = "facebook"
    youtube = "youtube"
    google = "google"
    twitter = "twitter"
    linkedin = "linkedin"


class SocialAccountStatus(StrEnum):
    active = "active"
    disabled = "disabled"


class ProductStatus(StrEnum):
    active = "active"
    archived = "archived"


class ProductRole(StrEnum):
    creator = "creator"
    approver = "approver"
    publisher = "publisher"
    analyst = "analyst"
    product_manager = "product_manager"


class CompanyBrandStructure(StrEnum):
    single_brand = "single_brand"
    multi_brand = "multi_brand"
    unsure = "unsure"


class CompanyOnboardingStep(StrEnum):
    registered = "registered"
    email_verified = "email_verified"
    brand_structure_selected = "brand_structure_selected"
    brand_profile_completed = "brand_profile_completed"
    first_product_created = "first_product_created"
    completed = "completed"


class ProductBrandingMode(StrEnum):
    use_company_branding = "use_company_branding"
    separate_brand = "separate_brand"


class SubProductBrandingMode(StrEnum):
    use_product_branding = "use_product_branding"
    separate_brand = "separate_brand"


class SocialAccountScope(StrEnum):
    product = "product"
    sub_products = "sub_products"
    company = "company"


class SocialConnectionMethod(StrEnum):
    oauth = "oauth"
    manual = "manual"


class ContentApprovalPolicy(StrEnum):
    no_approval = "no_approval"
    one_approver = "one_approver"
    product_manager_approval = "product_manager_approval"
    company_admin_approval = "company_admin_approval"


class ContentStatus(StrEnum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    rejected = "rejected"
    scheduled = "scheduled"
    published = "published"


class BrandAnalysisScope(StrEnum):
    company = "company"
    product = "product"
    sub_product = "sub_product"


class CreativeFormat(StrEnum):
    post = "post"
    carousel = "carousel"
    reel = "reel"


class CreativeLanguage(StrEnum):
    en = "en"
    hi = "hi"
    hinglish = "hinglish"


class CreativeQuality(StrEnum):
    draft = "draft"
    standard = "standard"
    hero = "hero"


class VoiceoverMode(StrEnum):
    native_audio = "native_audio"
    silent_text = "silent_text"


class ReelStyle(StrEnum):
    # Scene-by-scene generated b-roll, matching the format the whole reel
    # pipeline was originally built for.
    story = "story"
    # Every scene uses the product's uploaded brand-profile avatar image as
    # its first-frame/style-reference instead of the concept's own cover
    # image, so the same face appears throughout the reel.
    avatar = "avatar"


class GenerationJobStatus(StrEnum):
    queued = "queued"
    running = "running"
    # Reel-format jobs only: ideation + compliance finished and concepts are
    # persisted, but asset rendering (images, video, ffmpeg assembly) hasn't
    # started yet -- it waits for an explicit POST .../render-assets call
    # (see CreativeService.render_assets) instead of running automatically.
    awaiting_render = "awaiting_render"
    succeeded = "succeeded"
    failed = "failed"
    partially_failed = "partially_failed"


class CreativeAssetKind(StrEnum):
    image = "image"
    video = "video"
    raw_clip = "raw_clip"


class RefreshTokenRevokedReason(StrEnum):
    rotated = "rotated"
    reuse_detected = "reuse_detected"
    logout = "logout"
    logout_all = "logout_all"
    password_changed = "password_changed"
