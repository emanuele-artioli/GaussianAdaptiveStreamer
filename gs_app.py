# gs_app.py

'''
python http3_server.py --certificate certificates/ssl_cert.pem --private-key certificates/ssl_key.pem --host 0.0.0.0
'''
'''
bash scripts/launch_quic_chrome.sh

macOS direct command:
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --user-data-dir=/tmp/gaussian-streamer-quic-profile \
    --new-window \
    --enable-experimental-web-platform-features \
    --ignore-certificate-errors-spki-list=BSQJ0jkQ7wwhR7KvPZ+DSNk2XTZ/MS6xCbo9qu++VdQ= \
    --origin-to-force-quic-on=localhost:4433 \
    https://localhost:4433/models-ui
'''


from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles


from statics import STATIC_DIR, WEB_SPLAT_PUBLIC_DIR
import routes

starlette = Starlette(
    routes=[
        Route("/", routes.render_handler, methods=["GET"]),
        Route("/render", routes.render_handler, methods=["POST"]),
        Route("/metrics/predict", routes.metrics_predict, methods=["POST"]),
        Route("/export", routes.export_experiment, methods=["GET"]),
        Route("/models", routes.get_list_of_all_available_models, methods=["GET"]), 
        Route("/models-ui", routes.models_page, methods=["GET"]),
        Route("/player", routes.player_page, methods=["GET"]),
        Route("/player-legacy", routes.player_legacy_page, methods=["GET"]),
        Route("/web-splat-model/{model_id}", routes.web_splat_model_file, methods=["GET"]),
        Route("/web-splat-scene/{model_id}", routes.web_splat_scene_file, methods=["GET"]),
        Route("/loadModel", routes.load_model, methods=["POST"]),
        Route("/movement", routes.save_movements, methods=["POST"]),
        Route("/saveImages", routes.save_images, methods=["POST"]),
        
        Route("/control", routes.control, methods=["POST"]),
        Route("/dash/status", routes.dash_status, methods=["GET"]),
        Route("/dash/stop", routes.dash_stop, methods=["POST"]),
        Route("/player-dash", routes.player_dash_page, methods=["GET"]),
        Route("/dash/{path:path}", routes.dash_file, methods=["GET"]),
        Mount("/web-splat", StaticFiles(directory=WEB_SPLAT_PUBLIC_DIR, html=True, check_dir=False), name="web-splat"),
        
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    ]
)

# the callable the aioquic server imports
async def app(scope, receive, send):
    await starlette(scope, receive, send)
    

