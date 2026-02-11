from mtr_pathfinder import fetch_data, gen_route_interval
import hashlib

# 地图设置
LINK = "https://letsplay.minecrafttransitrailway.com/system-map"   # MTR系统地图链接
MTR_VER = 4   # MTR版本号（3或4）

link_hash = hashlib.md5(LINK.encode('utf-8')).hexdigest()
LOCAL_FILE_PATH = f'mtr-station-data-{link_hash}-{MTR_VER}.json'
INTERVAL_PATH = f'mtr-route-interval-data-{link_hash}-{MTR_VER}.json'

fetch_data(LINK, LOCAL_FILE_PATH, MTR_VER)
gen_route_interval(LOCAL_FILE_PATH, INTERVAL_PATH, LINK, MTR_VER)