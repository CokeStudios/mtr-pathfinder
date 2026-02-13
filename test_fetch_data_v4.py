from mtr_pathfinder_v4 import fetch_data, gen_departure
import hashlib

# 地图设置
# 在线线路图网址，结尾删除"/"
LINK: str = ''
# 从A站到B站，非出站换乘（越野）的最远步行距离，默认值为1500
MAX_WILD_BLOCKS: int = 1500

link_hash = hashlib.md5(LINK.encode('utf-8')).hexdigest()
LOCAL_FILE_PATH = f'mtr-station-data-{link_hash}-mtr4-v4.json'
DEP_PATH = f'mtr-route-departure-data-{link_hash}-mtr4-v4.json'

fetch_data(LINK, LOCAL_FILE_PATH, MAX_WILD_BLOCKS)
gen_departure(LINK, DEP_PATH)