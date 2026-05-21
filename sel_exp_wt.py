from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from subprocess import Popen
import os
import signal
import time
import json

options = Options()
options.add_argument("--enable-experimental-web-platform-features")
options.add_argument("--ignore-certificate-errors-spki-list=BSQJ0jkQ7wwhR7KvPZ+DSNk2XTZ/MS6xCbo9qu++VdQ=")
options.add_argument("--origin-to-force-quic-on=140.114.79.80:4433")

driver = webdriver.Chrome(options=options)
bw_limit_abspath = "/home/tonyhung/Desktop/GaussianAdaptiveStreamer/scripts/bandwidth_fluctuations.sh"

scene = "train"
driver.get(f"https://140.114.79.80:4433/player?modelId={scene}")
driver.implicitly_wait(5)

fps = driver.find_element(by=By.CSS_SELECTOR, value="#fps")
for id in range(1, 5):

    movement = f"/home/tonyhung/Desktop/GaussianAdaptiveStreamer/TestMovements/NTHU/{scene}/user{id}_{scene}.json"
    f = open(movement, "r")

    fps_log = open(f"wt_exp/fps{id}.log", "w")
    abr_log = open(f"wt_exp/abr{id}.log", "w")
    mvs = json.load(f)
    
    t_cur = time.time()
    t_start = t_cur
    t_end = t_cur + 120

    print(f"id: {id}")
    bw_limit = Popen(["sudo", bw_limit_abspath, "lo", "0", "0"])


    for mv in mvs:
        now = time.time()
        if now > t_end: 
            os.kill(bw_limit.pid, signal.SIGKILL)
            break

        driver.execute_script(f"modifyPosition({mv["angle"]}, {mv["elevation"]}, {mv["x"]}, {mv["y"]}, {mv["z"]});")
        ret = driver.execute_script("return _profile;")
        abr_log.write(f"{now - t_start} {ret}\n")
        if now - t_cur >= 1:
            fps_log.write(f"{now - t_start} {fps.get_attribute("innerHTML")}\n")
            t_cur = now
        # time.sleep(0.001)

    driver.execute_script("throughputABR.reset()")
    abr_log.close()
    fps_log.close()


driver.quit()