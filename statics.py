import os


ROOT = os.path.dirname(__file__)


CAPTURES_DIR = os.path.join(ROOT, "captures")
EXPERIMENTS_DIR = os.path.join(ROOT, "experiment")
TEMPLATES_DIR = os.path.join(ROOT, "templates")
LOG_DIR = os.path.join(ROOT, "logs")
STATIC_DIR = os.path.join(ROOT, "static")
MODELS_DIR = os.path.join(STATIC_DIR, "models")
DASH_DIR = os.path.join("dash")
WEB_SPLAT_DIR = os.path.join(ROOT, "web-splat")
WEB_SPLAT_PUBLIC_DIR = os.path.join(WEB_SPLAT_DIR, "public")
WEB_SPLAT_REQUIRED_FILES = ("index.html", "web_splats.js", "web_splats_bg.wasm")
WEB_SPLAT_CACHE_DIR = os.path.join(STATIC_DIR, "web-splat-cache")

os.makedirs(CAPTURES_DIR, exist_ok=True)
os.makedirs(EXPERIMENTS_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(WEB_SPLAT_CACHE_DIR, exist_ok=True)



PREVIEW_CANDIDATES = ("preview.png", "preview.jpg")

EVICT_AFTER_MS = 10 * 60_000
EVICT_CHECK_EVERY_S = 20