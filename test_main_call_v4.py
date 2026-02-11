from mtr_pathfinder_v4 import main
import hashlib

# 出发、到达车站
station1 = 'Spawn'
station2 = 'Sundogs'

# 地图设置
# 在线线路图网址，结尾删除"/"
LINK: str = "https://letsplay.minecrafttransitrailway.com/system-map"
# 旅途的最长时间，默认值为3
MAX_HOUR: int = 3
# 从A站到B站，非出站换乘（越野）的最远步行距离，默认值为1500
MAX_WILD_BLOCKS: int = 1500
# 手动增加出站换乘
# "车站: [出站换乘的车站, ...], ..."
TRANSFER_ADDITION: dict[str, list[str]] = {}
# 手动增加非出站换乘（越野）
# "车站: [非出站换乘的车站, ...], ..."
WILD_ADDITION: dict[str, list[str]] = {}
# 车站名称映射
# "车站昵称: 车站实际名称, ..."
STATION_TABLE: dict[str, str] = {}
# 禁止乘坐的路线（未开通的路线）
ORIGINAL_IGNORED_LINES: list = []

# 文件设置
link_hash = hashlib.md5(LINK.encode('utf-8')).hexdigest()
LOCAL_FILE_PATH = f'mtr-station-data-{link_hash}.json'
DEP_PATH = f'mtr-route-data-{link_hash}.json'
BASE_PATH = 'mtr_pathfinder_data'
PNG_PATH = 'mtr_pathfinder_data'

# 是否更新车站数据
UPDATE_DATA: bool = False
# 是否更新路线数据
GEN_DEPARTURE: bool = False

# 寻路设置
# 避开的路线
IGNORED_LINES: list = []
# 避开的车站
AVOID_STATIONS: list = []
# 允许高铁，默认值为True
CALCULATE_HIGH_SPEED: bool = True
# 允许船，默认值为True
CALCULATE_BOAT: bool = True
# 允许非出站换乘（越野），默认值为False
CALCULATE_WALKING_WILD: bool = False
# 仅允许轻轨，默认值为False
ONLY_LRT: bool = False
# 出发时间（秒，0-86400），默认值为None，即当前时间后10秒
DEP_TIME = None

# 输出的图片中是否显示详细信息（每站的到站、出发时间）
DETAIL: bool = False

main(station1, station2, LINK, LOCAL_FILE_PATH, DEP_PATH, BASE_PATH, PNG_PATH, MAX_WILD_BLOCKS, TRANSFER_ADDITION, WILD_ADDITION, STATION_TABLE, ORIGINAL_IGNORED_LINES, UPDATE_DATA, GEN_DEPARTURE, IGNORED_LINES, AVOID_STATIONS, CALCULATE_HIGH_SPEED, CALCULATE_BOAT, CALCULATE_WALKING_WILD, ONLY_LRT, DETAIL, MAX_HOUR, show=True, departure_time=DEP_TIME)