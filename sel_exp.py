from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from subprocess import Popen
import os
import signal
import time

options = Options()
options.add_argument("--enable-experimental-web-platform-features")
options.add_argument("--ignore-certificate-errors-spki-list=BSQJ0jkQ7wwhR7KvPZ+DSNk2XTZ/MS6xCbo9qu++VdQ=")
options.add_argument("--origin-to-force-quic-on=140.114.79.80:4433")

driver = webdriver.Chrome(options=options)
bw_limit_abspath = "/home/tonyhung/Desktop/GaussianAdaptiveStreamer/scripts/bandwidth_fluctuations.sh"

scene = "train"
driver.get(f"https://140.114.79.80:4433/player?modelId={scene}")

driver.implicitly_wait(0.5)

run_exp_btn = driver.find_element(by=By.CSS_SELECTOR, value="#open-exp")
run_exp_btn.click()

_exp_netname = driver.find_element(by=By.CSS_SELECTOR, value="#exp-networkName")
_exp_netname.send_keys("l")

exp_expname = driver.find_element(by=By.CSS_SELECTOR, value="#exp-fileName")
start_btn = driver.find_element(by=By.CSS_SELECTOR, value="#exp-start")

for id in range(1, 5):
    file_input = driver.find_element(by=By.CSS_SELECTOR, value="#exp-playbackFile")
    movement = f"/home/tonyhung/Desktop/GaussianAdaptiveStreamer/TestMovements/NTHU/{scene}/user{id}_{scene}.json"
    file_input.send_keys(movement)

    loss_rates = ["0.01", "0.05", "0.1", "1"]
    for loss_rate in loss_rates:
        exp_expname.clear()
        exp_expname.send_keys(f"{id}_20_{loss_rate}%")

        abrs = ['simple', 'l2a', 'lol+']
        for abr in abrs:
            script = f"abrOrder = [\'{abr}\'];"
            driver.execute_script(script)

            start_btn.click()
            bw_limit = Popen(["sudo", bw_limit_abspath, "20", loss_rate])

            time.sleep(150)
            os.kill(bw_limit.pid, signal.SIGKILL)
            driver.execute_script("console.clear();")

    # latencies = ["5", "10", "20", "40"]

driver.quit()
