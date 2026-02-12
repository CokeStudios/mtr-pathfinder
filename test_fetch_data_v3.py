from mtr_pathfinder import fetch_data, gen_route_interval
import hashlib

# 地图设置
# MTR模组版本（3/4），默认值为3
MTR_VER: int = 3
# 在线线路图网址，结尾删除"/"
LINK: str = ''

link_hash = hashlib.md5(LINK.encode('utf-8')).hexdigest()
LOCAL_FILE_PATH = f'mtr-station-data-{link_hash}-mtr{MTR_VER}-v3.json'
INTERVAL_PATH = f'mtr-route-data-{link_hash}-mtr{MTR_VER}-v3.json'

fetch_data(LINK, LOCAL_FILE_PATH, MTR_VER)
gen_route_interval(LOCAL_FILE_PATH, INTERVAL_PATH, LINK, MTR_VER)