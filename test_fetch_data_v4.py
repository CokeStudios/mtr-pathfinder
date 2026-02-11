from mtr_pathfinder_v4 import fetch_data, gen_departure
import hashlib

# 地图设置
LINK = ""   # MTR系统地图链接
MTR_VER = 3   # MTR版本号（3或4）

link_hash = hashlib.md5(LINK.encode('utf-8')).hexdigest()
LOCAL_FILE_PATH = f'mtr-station-data-{link_hash}-{MTR_VER}.json'
DEP_PATH = f'mtr-route-data-{link_hash}-{MTR_VER}.json'

fetch_data(LINK, LOCAL_FILE_PATH, MTR_VER)
gen_departure(LINK, DEP_PATH)