import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_local_env_file():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("\"'")
        if name and name not in os.environ:
            os.environ[name] = value


_load_local_env_file()


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = _env_bool("DJANGO_DEBUG", default=_env_bool("DEBUG", default=True))

# SECURITY WARNING: keep the production secret key outside source code.
SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-jhbki5g5pz021^1_z+i+7+9_8ztede=1b03##ne)$od4^5g)o7")
if not DEBUG and SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("Set SECRET_KEY in the production environment.")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]
if "test" in sys.argv and "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("testserver")
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "plusone",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "plusone.middleware.BrowserBudgetMiddleware",
    "plusone.middleware.LastSeenMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "plusone.context_processors.open_chat_badge",
                "plusone.context_processors.product_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    try:
        import dj_database_url
    except ImportError as exc:
        raise ImproperlyConfigured("DATABASE_URL requires dj-database-url to be installed.") from exc
    DATABASES = {
        "default": dj_database_url.config(default=DATABASE_URL, conn_max_age=600, ssl_require=not DEBUG)
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = os.environ.get("PLUSONE_CAMPUS_TIME_ZONE", "Asia/Shanghai").strip()
try:
    ZoneInfo(TIME_ZONE)
except (ZoneInfoNotFoundError, ValueError) as exc:
    raise ImproperlyConfigured("PLUSONE_CAMPUS_TIME_ZONE must be a valid IANA time zone.") from exc

PLUSONE_CAMPUS_NAME = os.environ.get("PLUSONE_CAMPUS_NAME", "").strip()
PLUSONE_SUPPORT_EMAIL = os.environ.get("PLUSONE_SUPPORT_EMAIL", "").strip()
PLUSONE_BROWSER_BUDGET_COOKIE = "plusone_usage"
PLUSONE_WEB_PUSH_ENABLED = _env_bool("PLUSONE_WEB_PUSH_ENABLED", default=False)
PLUSONE_VAPID_PUBLIC_KEY = os.environ.get("PLUSONE_VAPID_PUBLIC_KEY", "").strip()
PLUSONE_VAPID_PRIVATE_KEY = os.environ.get("PLUSONE_VAPID_PRIVATE_KEY", "").strip()
PLUSONE_VAPID_SUBJECT = os.environ.get("PLUSONE_VAPID_SUBJECT", "").strip()
PLUSONE_WEB_PUSH_ALLOWED_HOSTS = tuple(value.strip() for value in os.environ.get("PLUSONE_WEB_PUSH_ALLOWED_HOSTS",
    "fcm.googleapis.com,updates.push.services.mozilla.com,push.services.mozilla.com,*.notify.windows.com,web.push.apple.com").split(",") if value.strip())

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

if not DEBUG:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _env_bool("SECURE_SSL_REDIRECT", default=not DEBUG)
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "3600" if not DEBUG else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = _env_bool("SECURE_HSTS_PRELOAD", default=False)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "discover"
LOGOUT_REDIRECT_URL = "discover"

PLUSONE_OPENAI_MODEL = "gpt-4o-mini"
PLUSONE_DEEPSEEK_MODEL = "deepseek-v4-flash"
PLUSONE_MODERATION_MODE = os.environ.get("PLUSONE_MODERATION_MODE", "external")
# Disable only NEW matches during an incident. Existing waiting chats remain
# understood by this server; never roll back to pre-WAITING code.
PLUSONE_NEW_MATCHES_ENABLED = _env_bool("PLUSONE_NEW_MATCHES_ENABLED", default=DEBUG)

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
