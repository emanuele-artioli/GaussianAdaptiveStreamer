# gs_app.py

'''
python http3_server.py --certificate certificates/ssl_cert.pem --private-key certificates/ssl_key.pem --host 0.0.0.0
'''
'''
 google-chrome \
  --enable-experimental-web-platform-features \
  --ignore-certificate-errors-spki-list=BSQJ0jkQ7wwhR7KvPZ+DSNk2XTZ/MS6xCbo9qu++VdQ= \
  --origin-to-force-quic-on=localhost:4433 \
  https://localhost:4433/models-ui
'''


from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.types import Scope, Receive, Send

import json
import asyncio
import struct
import time

from aioquic.h3.connection import H3Connection

from routes import render_image_raw
from encoding import encode_jpeg
from models import get_model, ensure_started

from statics import STATIC_DIR
import routes

# send_signal = asyncio.Event()

nextAvailableUserID = 0
userid_lock = asyncio.Lock()

class UserState:
    def __init__(self):
        self.azimuth = 180.0
        self.elevation = 0.0
        self.x = 0.0
        self.y = 0.0
        self.z = 5.0
        self.fx = 1300.0
        self.fy = 800.0
        self.cx = 400.0
        self.cy = 300.0
        self.width = 800
        self.height = 600
        self.profile = 0
        self.modelID = None
        self.model = None
        self.clientTimestemp = 0
        self.lock = asyncio.Lock()
        self.fps = 60

        self.image_id = 0
        self.imageIDLock = asyncio.Lock()

        self.should_stop = False


    async def updatePos(self, msg_json):
        async with self.lock:
            self.azimuth = msg_json["angle"]
            self.elevation = msg_json["elevation"]
            self.x = msg_json["x"]
            self.y = msg_json["y"]
            self.z = msg_json["z"]
            self.fx = msg_json["fx"]
            self.fy = msg_json["fy"]
            self.cx = msg_json["cx"]
            self.cy = msg_json["cy"]
            self.width = msg_json["width"]
            self.height = msg_json["height"]
            self.profile = msg_json["profile"]
            self.clientTimestemp = int(msg_json["timestemp"])

            if self.modelID != msg_json["modelId"]:
                self.modelID = msg_json["modelId"]
                self.model = get_model(self.modelID)
        

    async def getData(self):
        async with self.lock:
            ret = (
                self.azimuth, self.elevation, 
                self.x, self.y, self.z, 
                self.fx, self.fy, self.cx, self.cy,
                self.width, self.height, self.profile, self.model
            )
            timestemp = self.clientTimestemp

        return ret, timestemp
    
    async def getNextImageId(self):
        async with self.imageIDLock:
            ret = self.image_id
            self.image_id = (self.image_id + 1) % 4294967296

        return ret

    

users_data: dict[int, UserState] = {}

async def wt(scope: Scope, receive: Receive, send: Send, connection: H3Connection):
    message = await receive()
    await send({"type": "webtransport.accept"})

    global nextAvailableUserID, users_data
    userID = None

    async with userid_lock:
        userID = nextAvailableUserID
        nextAvailableUserID += 1

    await ensure_started()
    users_data[userID] = UserState()

    task = asyncio.create_task(TimedSend(send, userID))

    buffer = b""
    while not users_data[userID].should_stop:
        message = await receive()
        if message["type"] == "webtransport.stream.receive":
            buffer += message["data"]
            while len(buffer) >= 4:
                length = int.from_bytes(buffer[:4], byteorder="big")
                if len(buffer) >= 4 + length:
                    payload = buffer[4:4 + length]

                    msg_json = json.loads(payload)
                    buffer = buffer[4 + length:]

                    if "closed" in msg_json:
                        users_data[userID].should_stop = True
                        await send({"type": "webtransport.close"})
                    else:
                        await users_data[userID].updatePos(msg_json)
                
                else:
                    break

        else:
            print(message["type"])


async def TimedSend(send: Send, userID: int):
    while not users_data[userID].should_stop:
        asyncio.create_task(RenderAndSendImage(send, userID))
        await asyncio.sleep(1 / users_data[userID].fps)

async def RenderAndSendImage(send: Send, userID: int):
    MAX_DATAGRAM_SIZE = 1350

    await ensure_started()
    user = users_data[userID]
    user_data, timestemp = await user.getData()
    if user_data[-1] is None:
        return 0.0
    
    quality_level = user_data[-2]
    render_start_time_ms = int(time.time_ns() // 1e6)
    img_stream, render_ms = render_image_raw(*user_data)
    jpeg = encode_jpeg(img_stream, quality=70 - quality_level)

    total_chunks = (len(jpeg) + MAX_DATAGRAM_SIZE - 1) // MAX_DATAGRAM_SIZE
    image_id = await user.getNextImageId()
    for i in range(total_chunks):
        start = i * MAX_DATAGRAM_SIZE
        end = start + MAX_DATAGRAM_SIZE
        chunk_data = jpeg[start:end]
        header = struct.pack("!IBBQf", image_id, i, total_chunks, timestemp, render_ms)
        await send(
            {
                "data": header + chunk_data,
                "type": "webtransport.datagram.send",
                "image_id": image_id
            }
        )

    fin_time = time.time()
    return render_ms
    

starlette = Starlette(
    routes=[
        Route("/", routes.render_handler, methods=["GET"]),
        Route("/render", routes.render_handler, methods=["POST"]),
        Route("/metrics/predict", routes.metrics_predict, methods=["POST"]),
        Route("/export", routes.export_experiment, methods=["GET"]),
        Route("/models", routes.get_list_of_all_available_models, methods=["GET"]), 
        Route("/models-ui", routes.models_page, methods=["GET"]),
        Route("/player", routes.player_page, methods=["GET"]),
        Route("/player-wt", routes.player_wt_page, methods=["GET"]),
        Route("/experiment_data", routes.receive_experiment_data, methods=["POST"]),
        Route("/loadModel", routes.load_model, methods=["POST"]),
        Route("/movement", routes.save_movements, methods=["POST"]),
        Route("/saveImages", routes.save_images, methods=["POST"]),
        Route("/save_sr_image", routes.receive_sr, methods=["POST"]),
        Route("/materialize_sampled_frames", routes.materialize_sampled_frames, methods=["POST"]),
        
        Route("/control", routes.control, methods=["POST"]),
        Route("/dash/status", routes.dash_status, methods=["GET"]),
        Route("/dash/stop", routes.dash_stop, methods=["POST"]),
        Route("/player-dash", routes.player_dash_page, methods=["GET"]),
        Route("/dash/{path:path}", routes.dash_file, methods=["GET"]),
        
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    ]
)

# the callable the aioquic server imports
async def app(scope, receive, send, connection):
    if scope["type"] == "webtransport" and scope["path"] == "/wt":
        await wt(scope, receive, send, connection)
    else:
        await starlette(scope, receive, send)
    

