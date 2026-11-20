import base64
import datetime
import json
import os
import re
from urllib.parse import urlencode, urlparse

import requests
from django.conf import settings
from django.utils import timezone

META_GRAPH_API_VERSION = "v19.0"
META_GRAPH_BASE = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}"

DEFAULT_OAUTH_SCOPES = [
    "ads_management",
    "ads_read",
    "pages_show_list",
    "pages_read_engagement",
    "business_management",
]

ACCOUNT_STATUS_MAP = {
    1: "ACTIVE",
    2: "DISABLED",
    3: "UNSETTLED",
    7: "PENDING_RISK_REVIEW",
    8: "PENDING_SETTLEMENT",
    9: "IN_GRACE_PERIOD",
    100: "PENDING_CLOSURE",
    101: "CLOSED",
    201: "ANY_ACTIVE",
    202: "ANY_CLOSED",
}

DISABLE_REASON_MAP = {
    0: "NONE",
    1: "ADS_INTEGRITY_POLICY",
    2: "ADS_IP_REVIEW",
    3: "RISK_PAYMENT",
    4: "GRAY_ACCOUNT_SHUT_DOWN",
    5: "ADS_AFC_REVIEW",
    6: "BUSINESS_INTEGRITY_RAR",
    7: "PERMANENT_CLOSE",
    8: "UNUSED_RESELLER_ACCOUNT",
    9: "UNUSED_ACCOUNT",
}

ZERO_DECIMAL_CURRENCIES = {"JPY", "KRW", "VND", "CLP", "ISK", "TWD"}


def normalize_ad_account_id(raw_id: str) -> str:
    clean = str(raw_id or "").strip()
    if not clean:
        return ""
    return clean if clean.startswith("act_") else f"act_{clean}"


def to_meta_minor_units(amount: float, currency: str = "BDT") -> int:
    val = max(0.0, float(amount or 0))
    if str(currency or "BDT").upper() in ZERO_DECIMAL_CURRENCIES:
        return int(round(val))
    return int(round(val * 100))


def from_meta_minor_units(minor_amount, currency: str = "BDT") -> float:
    try:
        val = float(minor_amount or 0)
    except (TypeError, ValueError):
        return 0.0
    if str(currency or "BDT").upper() in ZERO_DECIMAL_CURRENCIES:
        return val
    return round(val / 100.0, 2)


def parse_meta_error(resp_data: dict, fallback_msg: str = "Meta Graph API request failed.") -> dict:
    err = resp_data.get("error", {}) if isinstance(resp_data, dict) else {}
    code = err.get("code")
    subcode = err.get("error_subcode")
    msg = err.get("error_user_msg") or err.get("message") or fallback_msg
    title = err.get("error_user_title") or ""
    lower = f"{title} {msg}".lower()

    is_billing_guard = (
        code in (1359024, 1359102, 1359010, 2635, 1487390)
        or subcode in (1359024, 1487742, 1885252, 2446391)
        or any(k in lower for k in ("payment", "billing", "card", "unsettled", "spend cap", "disabled", "policy", "rejected"))
    )
    is_token_error = code in (190, 102)
    is_perm_error = code in (10, 200, 294)

    return {
        "message": f"{title}: {msg}".strip(": ") if title else msg,
        "code": code,
        "subcode": subcode,
        "isBillingGuard": is_billing_guard,
        "isTokenError": is_token_error,
        "isPermissionError": is_perm_error,
        "raw": err,
    }


def _meta_get(endpoint: str, access_token: str, params: dict = None, timeout: int = 12) -> tuple[bool, dict]:
    query = dict(params or {})
    query["access_token"] = access_token
    url = f"{META_GRAPH_BASE}/{endpoint.lstrip('/')}"
    try:
        res = requests.get(url, params=query, timeout=timeout)
        data = res.json() if res.content else {}
        if res.status_code == 200 and "error" not in data:
            return True, data
        return False, parse_meta_error(data, f"HTTP {res.status_code}")
    except Exception as exc:
        return False, {"message": str(exc), "isBillingGuard": False, "isTokenError": False}


def _meta_post(endpoint: str, access_token: str, payload: dict = None, files=None, timeout: int = 15) -> tuple[bool, dict]:
    body = dict(payload or {})
    body["access_token"] = access_token
    url = f"{META_GRAPH_BASE}/{endpoint.lstrip('/')}"
    try:
        if files:
            res = requests.post(url, data=body, files=files, timeout=timeout)
        else:
            res = requests.post(url, json=body, timeout=timeout)
        data = res.json() if res.content else {}
        if res.status_code in (200, 201) and "error" not in data:
            return True, data
        return False, parse_meta_error(data, f"HTTP {res.status_code}")
    except Exception as exc:
        return False, {"message": str(exc), "isBillingGuard": False, "isTokenError": False}


def build_oauth_login_url(app_id: str, redirect_uri: str, state: str, scopes=None) -> str:
    scope_str = ",".join(scopes or DEFAULT_OAUTH_SCOPES)
    params = urlencode({
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": scope_str,
        "response_type": "code",
    })
    return f"https://www.facebook.com/{META_GRAPH_API_VERSION}/dialog/oauth?{params}"


def exchange_oauth_code(app_id: str, app_secret: str, redirect_uri: str, code: str) -> tuple[bool, dict]:
    url = f"{META_GRAPH_BASE}/oauth/access_token"
    try:
        res = requests.get(
            url,
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=12,
        )
        data = res.json() if res.content else {}
        if res.status_code != 200 or "error" in data:
            return False, parse_meta_error(data, "OAuth token exchange failed")

        short_token = data.get("access_token", "")
        expires_in = int(data.get("expires_in") or 3600)

        # Exchange for a 60-day long-lived user access token when app_secret is available
        if short_token and app_secret:
            ll_res = requests.get(
                url,
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "fb_exchange_token": short_token,
                },
                timeout=12,
            )
            ll_data = ll_res.json() if ll_res.content else {}
            if ll_res.status_code == 200 and ll_data.get("access_token"):
                return True, {
                    "access_token": ll_data["access_token"],
                    "expires_in": int(ll_data.get("expires_in") or 5184000),
                }

        return True, {"access_token": short_token, "expires_in": expires_in}
    except Exception as exc:
        return False, {"message": str(exc)}


def discover_meta_assets(access_token: str) -> tuple[bool, dict]:
    """
    Fetches user identity, granted permissions, Ad Accounts (with billing health),
    Facebook Pages (with linked Instagram accounts), and Ad Pixels.
    """
    ok_me, me_data = _meta_get("/me", access_token, {"fields": "id,name"})
    if not ok_me:
        return False, me_data

    ok_perms, perms_data = _meta_get("/me/permissions", access_token)
    granted_scopes = []
    if ok_perms:
        granted_scopes = [
            p.get("permission")
            for p in perms_data.get("data", [])
            if p.get("status") == "granted"
        ]

    ok_acc, acc_data = _meta_get(
        "/me/adaccounts",
        access_token,
        {
            "fields": "id,account_id,name,account_status,disable_reason,currency,amount_spent,spend_cap,balance,funding_source_details",
            "limit": 50,
        },
    )
    ad_accounts = []
    if ok_acc:
        for acc in acc_data.get("data", []):
            status_code = int(acc.get("account_status") or 0)
            disable_code = int(acc.get("disable_reason") or 0)
            curr = acc.get("currency") or "BDT"
            funding = acc.get("funding_source_details") or {}
            ad_accounts.append({
                "id": normalize_ad_account_id(acc.get("id")),
                "name": acc.get("name") or acc.get("id"),
                "currency": curr,
                "statusCode": status_code,
                "statusLabel": ACCOUNT_STATUS_MAP.get(status_code, "UNKNOWN"),
                "disableReason": DISABLE_REASON_MAP.get(disable_code, "NONE"),
                "fundingSummary": funding.get("display_string") or ("Card/Billing Linked" if funding else "No Payment Method Detected"),
                "spendCap": from_meta_minor_units(acc.get("spend_cap"), curr),
                "amountSpent": from_meta_minor_units(acc.get("amount_spent"), curr),
                "balance": from_meta_minor_units(acc.get("balance"), curr),
            })

    ok_pages, pages_data = _meta_get(
        "/me/accounts",
        access_token,
        {
            "fields": "id,name,category,access_token,instagram_business_account{id,username}",
            "limit": 50,
        },
    )
    pages = []
    if ok_pages:
        for pg in pages_data.get("data", []):
            ig = pg.get("instagram_business_account") or {}
            pages.append({
                "id": str(pg.get("id") or ""),
                "name": pg.get("name") or "",
                "category": pg.get("category") or "",
                "pageAccessToken": pg.get("access_token") or "",
                "instagramActorId": str(ig.get("id") or ""),
                "instagramUsername": ig.get("username") or "",
            })

    pixels = []
    if ad_accounts:
        primary_act = ad_accounts[0]["id"]
        ok_px, px_data = _meta_get(f"/{primary_act}/adspixels", access_token, {"fields": "id,name", "limit": 25})
        if ok_px:
            pixels = [{"id": str(x.get("id")), "name": x.get("name") or str(x.get("id"))} for x in px_data.get("data", [])]

    return True, {
        "user": {"id": str(me_data.get("id") or ""), "name": me_data.get("name") or ""},
        "grantedScopes": granted_scopes,
        "adAccounts": ad_accounts,
        "pages": pages,
        "pixels": pixels,
    }


def fetch_ad_account_pixels(access_token: str, ad_account_id: str) -> list:
    act_id = normalize_ad_account_id(ad_account_id)
    if not act_id or not access_token:
        return []
    ok_px, px_data = _meta_get(f"/{act_id}/adspixels", access_token, {"fields": "id,name", "limit": 25})
    if not ok_px:
        return []
    return [{"id": str(x.get("id")), "name": x.get("name") or str(x.get("id"))} for x in px_data.get("data", [])]


def evaluate_billing_guard(account_info: dict, granted_scopes: list) -> tuple[str, str]:
    """
    Produces clear Billing & Delivery Guard status + actionable warning message.
    """
    if not account_info:
        return "UNVERIFIED", "Select an active Meta Ad Account to enable live billing & delivery verification."

    status_label = account_info.get("statusLabel", "UNKNOWN")
    disable_reason = account_info.get("disableReason", "NONE")
    funding = str(account_info.get("fundingSummary") or "")
    spend_cap = float(account_info.get("spendCap") or 0)
    amount_spent = float(account_info.get("amountSpent") or 0)

    alerts = []
    billing_status = "HEALTHY"

    if status_label in ("DISABLED", "CLOSED", "PENDING_CLOSURE"):
        billing_status = "BLOCKED"
        alerts.append(f"Ad Account is {status_label} (Reason: {disable_reason}). Clear restrictions in Meta Billing/Account Quality.")
    elif status_label in ("UNSETTLED", "IN_GRACE_PERIOD", "PENDING_SETTLEMENT"):
        billing_status = "PAYMENT_DUE"
        alerts.append(f"Ad Account status is {status_label}. Pay outstanding Meta invoice or update dual-currency card in Meta Billing.")
    elif status_label == "PENDING_RISK_REVIEW":
        billing_status = "WARNING"
        alerts.append("Meta is performing a risk review on this Ad Account; delivery may pause temporarily.")

    if "no payment" in funding.lower():
        if billing_status == "HEALTHY":
            billing_status = "WARNING"
        alerts.append("No primary payment method detected on Ad Account. Add a valid card in Meta Ads Billing before boosting.")

    if spend_cap > 0 and amount_spent >= spend_cap * 0.95:
        if billing_status == "HEALTHY":
            billing_status = "WARNING"
        alerts.append(f"Account Spend Cap nearly reached ({amount_spent:.0f}/{spend_cap:.0f}). Increase spend limit in Meta Billing.")

    if granted_scopes and "ads_management" not in granted_scopes:
        billing_status = "PERMISSION_MISSING"
        alerts.append("Token lacks 'ads_management' permission; campaign launch & pause controls will be blocked by Meta.")

    return billing_status, " ".join(alerts)


def check_account_billing_health(access_token: str, ad_account_id: str, granted_scopes: list = None) -> tuple[bool, dict]:
    act_id = normalize_ad_account_id(ad_account_id)
    if not act_id:
        return False, {"message": "Ad Account ID is required."}

    ok, acc = _meta_get(
        f"/{act_id}",
        access_token,
        {"fields": "id,name,account_status,disable_reason,currency,amount_spent,spend_cap,balance,funding_source_details"},
    )
    if not ok:
        return False, acc

    status_code = int(acc.get("account_status") or 0)
    disable_code = int(acc.get("disable_reason") or 0)
    curr = acc.get("currency") or "BDT"
    funding = acc.get("funding_source_details") or {}

    info = {
        "id": normalize_ad_account_id(acc.get("id")),
        "name": acc.get("name") or act_id,
        "currency": curr,
        "statusCode": status_code,
        "statusLabel": ACCOUNT_STATUS_MAP.get(status_code, "UNKNOWN"),
        "disableReason": DISABLE_REASON_MAP.get(disable_code, "NONE"),
        "fundingSummary": funding.get("display_string") or ("Card/Billing Linked" if funding else "No Payment Method Detected"),
        "spendCap": from_meta_minor_units(acc.get("spend_cap"), curr),
        "amountSpent": from_meta_minor_units(acc.get("amount_spent"), curr),
        "balance": from_meta_minor_units(acc.get("balance"), curr),
    }
    billing_status, alert_msg = evaluate_billing_guard(info, granted_scopes or [])
    info["billingStatus"] = billing_status
    info["billingAlertMessage"] = alert_msg
    return True, info


def fetch_page_published_posts(page_access_token: str, page_id: str, limit: int = 15) -> tuple[bool, list | dict]:
    if not page_id or not page_access_token:
        return False, {"message": "Connected Facebook Page & Page Access Token required to fetch posts."}

    ok, data = _meta_get(
        f"/{page_id}/published_posts",
        page_access_token,
        {
            "fields": "id,message,story,full_picture,permalink_url,created_time,shares,attachments{title,description,url,type,media}",
            "limit": limit,
        },
    )
    if not ok:
        return False, data

    posts = []
    for post in data.get("data", []):
        attachments = post.get("attachments", {}).get("data", []) if isinstance(post.get("attachments"), dict) else []
        first_att = attachments[0] if attachments else {}
        media_img = ""
        if isinstance(first_att.get("media"), dict):
            media_img = first_att["media"].get("image", {}).get("src", "")
        posts.append({
            "id": str(post.get("id") or ""),
            "message": post.get("message") or post.get("story") or first_att.get("title") or "Facebook Page Post",
            "fullPicture": post.get("full_picture") or media_img or "",
            "permalinkUrl": post.get("permalink_url") or "",
            "createdTime": post.get("created_time") or "",
            "sharesCount": int((post.get("shares") or {}).get("count") or 0),
            "attachmentTitle": first_att.get("title") or "",
            "attachmentUrl": first_att.get("url") or "",
        })
    return True, posts


def search_targeting_options(access_token: str, query: str, search_type: str = "adinterest") -> tuple[bool, list | dict]:
    clean_q = str(query or "").strip()
    if not clean_q:
        return True, []

    params = {"type": search_type, "q": clean_q, "limit": 15}
    if search_type == "adgeolocation":
        params["location_types"] = json.dumps(["country", "region", "city"])

    ok, data = _meta_get("/search", access_token, params)
    if not ok:
        return False, data

    results = []
    for item in data.get("data", []):
        if search_type == "adgeolocation":
            results.append({
                "key": str(item.get("key") or ""),
                "name": item.get("name") or "",
                "type": item.get("type") or "city",
                "countryCode": item.get("country_code") or "",
                "countryName": item.get("country_name") or "",
                "region": item.get("region") or "",
            })
        else:
            results.append({
                "id": str(item.get("id") or ""),
                "name": item.get("name") or "",
                "audienceSizeLower": int(item.get("audience_size_lower_bound") or 0),
                "audienceSizeUpper": int(item.get("audience_size_upper_bound") or 0),
                "path": item.get("path") or [],
            })
    return True, results


def build_meta_targeting_spec(targeting_input: dict) -> dict:
    t = targeting_input if isinstance(targeting_input, dict) else {}
    countries = [c for c in (t.get("countries") or ["BD"]) if c]
    cities = t.get("cities") or []
    regions = t.get("regions") or []

    geo_locations = {}
    if cities:
        geo_locations["cities"] = [{"key": str(c.get("key") or c), "radius": int(c.get("radius", 25)), "distance_unit": "kilometer"} for c in cities]
    if regions:
        geo_locations["regions"] = [{"key": str(r.get("key") or r)} for r in regions]
    if not geo_locations:
        geo_locations["countries"] = countries or ["BD"]

    spec = {
        "geo_locations": geo_locations,
        "age_min": max(18, min(65, int(t.get("ageMin") or 18))),
        "age_max": max(18, min(65, int(t.get("ageMax") or 55))),
    }
    if spec["age_min"] > spec["age_max"]:
        spec["age_min"], spec["age_max"] = spec["age_max"], spec["age_min"]

    genders = t.get("genders")
    if isinstance(genders, list) and genders and set(genders).issubset({1, 2}):
        spec["genders"] = [int(g) for g in genders]

    interests = t.get("interests") or []
    clean_interests = [
        {"id": str(i["id"]), "name": str(i.get("name") or "")}
        for i in interests
        if isinstance(i, dict) and i.get("id")
    ]
    if clean_interests:
        spec["flexible_spec"] = [{"interests": clean_interests}]

    placements = str(t.get("placementMode") or "ADVANTAGE_PLUS").upper()
    if placements == "FACEBOOK_ONLY":
        spec["publisher_platforms"] = ["facebook"]
    elif placements == "INSTAGRAM_ONLY":
        spec["publisher_platforms"] = ["instagram"]

    return spec


def estimate_audience_reach(access_token: str, ad_account_id: str, targeting_input: dict) -> tuple[bool, dict]:
    act_id = normalize_ad_account_id(ad_account_id)
    if not act_id or not access_token:
        return False, {"message": "Meta Ad Account and access token required."}

    spec = build_meta_targeting_spec(targeting_input)
    ok, data = _meta_get(
        f"/{act_id}/reachestimate",
        access_token,
        {"targeting_spec": json.dumps(spec)},
    )
    if not ok:
        return False, data

    est = data.get("data", {}) if isinstance(data.get("data"), dict) else data
    return True, {
        "usersLowerBound": int(est.get("users_lower_bound") or 0),
        "usersUpperBound": int(est.get("users_upper_bound") or 0),
        "estimateReady": bool(est.get("estimate_ready", True)),
        "targetingSpec": spec,
    }


def upload_ad_image(access_token: str, ad_account_id: str, image_url: str, public_origin: str = "") -> tuple[bool, str]:
    """
    Uploads a local /media image or remote HTTP image to Meta /{ad_account_id}/adimages
    and returns the resulting Meta image_hash.
    """
    act_id = normalize_ad_account_id(ad_account_id)
    clean_url = str(image_url or "").strip()
    if not clean_url:
        return False, "Ad Creative image URL is required for product/sales ads."

    img_bytes = None
    filename = "ad_creative.jpg"

    # Resolve local Django MEDIA_ROOT file directly when path starts with /media/
    parsed = urlparse(clean_url)
    rel_path = parsed.path if parsed.scheme else clean_url
    if rel_path.startswith("/media/"):
        disk_path = os.path.join(str(settings.MEDIA_ROOT), rel_path[len("/media/"):].lstrip("/\\"))
        if os.path.isfile(disk_path):
            filename = os.path.basename(disk_path) or filename
            with open(disk_path, "rb") as f:
                img_bytes = f.read()

    if img_bytes is None:
        fetch_url = clean_url
        if not fetch_url.startswith(("http://", "https://")) and public_origin:
            fetch_url = f"{public_origin.rstrip('/')}/{fetch_url.lstrip('/')}"
        try:
            dl = requests.get(fetch_url, timeout=12)
            if dl.status_code == 200 and dl.content:
                img_bytes = dl.content
            else:
                return False, f"Failed to download creative image (HTTP {dl.status_code})."
        except Exception as exc:
            return False, f"Could not fetch creative image: {exc}"

    b64_str = base64.b64encode(img_bytes).decode("utf-8")
    ok, data = _meta_post(f"/{act_id}/adimages", access_token, {"bytes": b64_str, "name": filename})
    if not ok:
        return False, data.get("message", "Meta adimages upload failed.")

    images_map = data.get("images") or {}
    if isinstance(images_map, dict):
        for _fname, info in images_map.items():
            if isinstance(info, dict) and info.get("hash"):
                return True, info["hash"]

    return False, "Meta did not return an image_hash from adimages upload."


def launch_meta_campaign_flow(connection, access_token: str, payload: dict, public_origin: str = "") -> tuple[bool, dict]:
    """
    Executes the full 4-step Meta Marketing API campaign creation / Quick Boost flow:
      1. Campaign -> 2. AdSet -> 3. Image Upload & AdCreative (or Post Boost object_story_id) -> 4. Ad
    """
    act_id = normalize_ad_account_id(connection.adAccountId)
    page_id = str(payload.get("pageId") or connection.pageId or "").strip()
    pixel_id = str(payload.get("pixelId") or connection.pixelId or "").strip()
    currency = connection.adAccountCurrency or "BDT"

    if not act_id:
        return False, {"message": "No Meta Ad Account connected for this store tenant."}
    if not page_id:
        return False, {"message": "No Facebook Page connected for this store tenant."}

    mode = str(payload.get("campaignType") or "PRODUCT_SALES").upper()
    objective = str(payload.get("objective") or ("OUTCOME_SALES" if mode == "PRODUCT_SALES" else "OUTCOME_TRAFFIC")).upper()
    desired_status = "ACTIVE" if str(payload.get("status") or "PAUSED").upper() == "ACTIVE" else "PAUSED"
    camp_name = str(payload.get("name") or "AmarDokan SaaS Boost").strip()

    daily_budget_val = float(payload.get("dailyBudget") or 0)
    duration_days = int(payload.get("durationDays") or 0)
    lifetime_budget_val = float(payload.get("lifetimeBudget") or 0)

    if daily_budget_val <= 0 and lifetime_budget_val <= 0:
        return False, {"message": "Please set a valid Daily Budget or Lifetime Budget."}

    # Step 1: Create Meta Campaign
    camp_body = {
        "name": camp_name,
        "objective": objective,
        "status": desired_status,
        "special_ad_categories": [],
        "is_adset_budget_sharing_enabled": False,
    }
    ok_camp, camp_res = _meta_post(f"/{act_id}/campaigns", access_token, camp_body)
    if not ok_camp:
        return False, {"stage": "CAMPAIGN", **camp_res}

    meta_campaign_id = str(camp_res["id"])

    # Step 2: Build Targeting & Create Meta AdSet
    targeting_spec = build_meta_targeting_spec(payload.get("targeting") or {})
    optimization_goal = str(payload.get("optimizationGoal") or "").upper()
    if not optimization_goal:
        if objective == "OUTCOME_SALES" and pixel_id:
            optimization_goal = "OFFSITE_CONVERSIONS"
        elif objective == "OUTCOME_ENGAGEMENT":
            optimization_goal = "POST_ENGAGEMENT"
        elif objective == "OUTCOME_LEADS":
            optimization_goal = "LEAD_GENERATION"
        else:
            optimization_goal = "LINK_CLICKS"

    now_utc = timezone.now()
    start_time_iso = payload.get("startTime") or (now_utc + datetime.timedelta(minutes=5)).isoformat()
    end_time_iso = payload.get("endTime")
    if not end_time_iso and duration_days > 0:
        end_time_iso = (now_utc + datetime.timedelta(days=duration_days)).isoformat()

    adset_body = {
        "name": f"{camp_name} - AdSet",
        "campaign_id": meta_campaign_id,
        "billing_event": "IMPRESSIONS",
        "optimization_goal": optimization_goal,
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": targeting_spec,
        "status": desired_status,
        "start_time": start_time_iso,
    }
    if end_time_iso:
        adset_body["end_time"] = end_time_iso

    if daily_budget_val > 0:
        adset_body["daily_budget"] = to_meta_minor_units(daily_budget_val, currency)
    else:
        adset_body["lifetime_budget"] = to_meta_minor_units(lifetime_budget_val, currency)
        if not end_time_iso:
            adset_body["end_time"] = (now_utc + datetime.timedelta(days=7)).isoformat()

    if optimization_goal == "OFFSITE_CONVERSIONS":
        if not pixel_id:
            # Fallback gracefully or reject with clear message
            _meta_post(f"/{meta_campaign_id}", access_token, {"status": "DELETED"})
            return False, {
                "stage": "ADSET",
                "message": "A Meta Pixel ID is required for Website Sales (OFFSITE_CONVERSIONS). Bind a Pixel or switch goal to LINK_CLICKS.",
            }
        adset_body["promoted_object"] = {
            "pixel_id": pixel_id,
            "custom_event_type": "PURCHASE",
        }

    ok_adset, adset_res = _meta_post(f"/{act_id}/adsets", access_token, adset_body)
    if not ok_adset:
        _meta_post(f"/{meta_campaign_id}", access_token, {"status": "DELETED"})
        return False, {"stage": "ADSET", **adset_res}

    meta_adset_id = str(adset_res["id"])

    # Step 3: Create Ad Creative (either Boost Existing Page Post OR New Product Creative)
    post_id = str(payload.get("postId") or "").strip()
    creative_body = {"name": f"{camp_name} - Creative"}
    image_hash = ""
    image_url = str(payload.get("imageUrl") or "").strip()
    link_url = str(payload.get("landingUrl") or "").strip()

    if mode == "QUICK_BOOST_POST" and post_id:
        story_id = post_id if "_" in post_id else f"{page_id}_{post_id}"
        creative_body["object_story_id"] = story_id
    else:
        if not link_url:
            _meta_post(f"/{meta_campaign_id}", access_token, {"status": "DELETED"})
            return False, {"stage": "CREATIVE", "message": "Landing / Product URL is required for product ads."}

        if image_url:
            ok_img, hash_or_err = upload_ad_image(access_token, act_id, image_url, public_origin=public_origin)
            if not ok_img:
                _meta_post(f"/{meta_campaign_id}", access_token, {"status": "DELETED"})
                return False, {"stage": "IMAGE_UPLOAD", "message": hash_or_err}
            image_hash = hash_or_err

        link_data = {
            "link": link_url,
            "message": str(payload.get("primaryText") or payload.get("headline") or camp_name),
            "name": str(payload.get("headline") or payload.get("productName") or camp_name),
            "description": str(payload.get("description") or ""),
            "call_to_action": {
                "type": str(payload.get("callToAction") or "SHOP_NOW"),
                "value": {"link": link_url},
            },
        }
        if image_hash:
            link_data["image_hash"] = image_hash

        story_spec = {"page_id": page_id, "link_data": link_data}
        if connection.instagramActorId:
            story_spec["instagram_actor_id"] = connection.instagramActorId

        creative_body["object_story_spec"] = story_spec

    ok_cr, cr_res = _meta_post(f"/{act_id}/adcreatives", access_token, creative_body)
    if not ok_cr:
        _meta_post(f"/{meta_campaign_id}", access_token, {"status": "DELETED"})
        return False, {"stage": "CREATIVE", **cr_res}

    meta_creative_id = str(cr_res["id"])

    # Step 4: Publish Ad linking AdSet + Creative
    ad_body = {
        "name": f"{camp_name} - Ad",
        "adset_id": meta_adset_id,
        "creative": {"creative_id": meta_creative_id},
        "status": desired_status,
    }
    ok_ad, ad_res = _meta_post(f"/{act_id}/ads", access_token, ad_body)
    if not ok_ad:
        _meta_post(f"/{meta_campaign_id}", access_token, {"status": "DELETED"})
        return False, {"stage": "AD", **ad_res}

    return True, {
        "metaCampaignId": meta_campaign_id,
        "metaAdSetId": meta_adset_id,
        "metaCreativeId": meta_creative_id,
        "metaAdId": str(ad_res["id"]),
        "imageHash": image_hash,
        "optimizationGoal": optimization_goal,
        "targetingSpec": targeting_spec,
        "startTime": start_time_iso,
        "endTime": end_time_iso,
        "status": desired_status,
    }


def update_meta_object_status(access_token: str, meta_object_id: str, new_status: str) -> tuple[bool, dict]:
    clean_status = "ACTIVE" if str(new_status).upper() == "ACTIVE" else ("DELETED" if str(new_status).upper() == "DELETED" else "PAUSED")
    return _meta_post(f"/{meta_object_id}", access_token, {"status": clean_status})


def update_meta_adset(
    access_token: str,
    meta_adset_id: str,
    currency: str = "BDT",
    daily_budget=None,
    lifetime_budget=None,
    end_time: str = None,
    new_status: str = None,
    targeting_input: dict = None,
) -> tuple[bool, dict]:
    body = {}
    if daily_budget is not None and float(daily_budget) > 0:
        body["daily_budget"] = to_meta_minor_units(float(daily_budget), currency)
    if lifetime_budget is not None and float(lifetime_budget) > 0:
        body["lifetime_budget"] = to_meta_minor_units(float(lifetime_budget), currency)
    if end_time:
        body["end_time"] = end_time
    if new_status:
        body["status"] = "ACTIVE" if str(new_status).upper() == "ACTIVE" else "PAUSED"
    if targeting_input:
        body["targeting"] = build_meta_targeting_spec(targeting_input)

    if not body:
        return True, {"success": True}
    return _meta_post(f"/{meta_adset_id}", access_token, body)


def fetch_remote_campaigns_tree(access_token: str, ad_account_id: str) -> tuple[bool, list | dict]:
    act_id = normalize_ad_account_id(ad_account_id)
    if not act_id:
        return False, {"message": "Ad Account ID is required."}

    fields = (
        "id,name,objective,status,effective_status,issues_info,"
        "adsets.limit(20){id,name,status,effective_status,daily_budget,lifetime_budget,optimization_goal,billing_event,targeting,start_time,end_time,"
        "insights.date_preset(maximum){spend,impressions,reach,clicks,ctr,cpc}},"
        "ads.limit(20){id,name,adset_id,status,effective_status,ad_review_feedback,creative{id,name,title,body,image_url,effective_object_story_id},"
        "insights.date_preset(maximum){spend,impressions,reach,clicks}},"
        "insights.date_preset(maximum){spend,impressions,reach,clicks,ctr,cpc,cpm,actions,action_values}"
    )
    ok, data = _meta_get(f"/{act_id}/campaigns", access_token, {"fields": fields, "limit": 50})
    if not ok:
        return False, data
    return True, data.get("data", [])

