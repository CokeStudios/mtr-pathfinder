from mtr_pathfinder import main
import hashlib

# 出发、到达车站
station1 = ""
station2 = ""

# 地图设置
# MTR模组版本（3/4），默认值为3
MTR_VER = 3
# 在线线路图网址，结尾删除"/"
LINK = ""
# 从A站到B站，非出站换乘（越野）的最远步行距离，默认值为1500
MAX_WILD_BLOCKS = 1500
# 手动增加出站换乘
# "车站: [出站换乘的车站, ...], ..."
TRANSFER_ADDITION = {}
# 手动增加非出站换乘（越野）
# "车站: [非出站换乘的车站, ...], ..."
WILD_ADDITION = {}
# 车站名称映射
# "车站昵称: 车站实际名称, ..."
STATION_TABLE = {}
# 禁止乘坐的路线（未开通的路线）
ORIGINAL_IGNORED_LINES = []

link_hash = hashlib.md5(LINK.encode('utf-8')).hexdigest()
# 文件设置
LOCAL_FILE_PATH = f'mtr-station-data-{link_hash}-{MTR_VER}.json'
INTERVAL_PATH = f'mtr-route-data-{link_hash}-{MTR_VER}.json'
BASE_PATH = 'mtr_pathfinder_data'
PNG_PATH = 'mtr_pathfinder_data'

# 是否更新车站数据
UPDATE_DATA = False
# 是否更新路线数据
GEN_ROUTE_INTERVAL = False

# 寻路设置
# 避开的路线
IGNORED_LINES = []
# 避开的车站
AVOID_STATIONS = []
# 允许高铁，默认值为True
CALCULATE_HIGH_SPEED = True
# 允许船，默认值为True
CALCULATE_BOAT = True
# 允许非出站换乘（越野），默认值为False
CALCULATE_WALKING_WILD = False
# 仅允许轻轨，默认值为False
ONLY_LRT = False
# 计算理论最快路线，不考虑等车时间，默认值为False
IN_THEORY = False

# 输出的图片中是否显示详细信息，默认值为False
DETAIL = False

main(station1, station2, LINK, LOCAL_FILE_PATH, INTERVAL_PATH, BASE_PATH, PNG_PATH, MAX_WILD_BLOCKS, TRANSFER_ADDITION, WILD_ADDITION, STATION_TABLE, ORIGINAL_IGNORED_LINES, UPDATE_DATA, GEN_ROUTE_INTERVAL, IGNORED_LINES, AVOID_STATIONS, CALCULATE_HIGH_SPEED, CALCULATE_BOAT, CALCULATE_WALKING_WILD, ONLY_LRT, IN_THEORY, DETAIL, MTR_VER, show=True)