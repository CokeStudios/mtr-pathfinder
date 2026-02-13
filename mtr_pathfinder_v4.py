'''
Find paths between two stations for Minecraft Transit Railway.
'''

from array import array
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from enum import Enum
from io import BytesIO
from math import gcd, sqrt
from operator import itemgetter
from time import gmtime, strftime, time
from typing import Optional, Dict, Literal, Tuple, List, Union
import base64
import hashlib
import json
import mmap
import os
import pickle
import re

from fontTools.ttLib import TTFont
from opencc import OpenCC
from PIL import Image, ImageDraw, ImageFont
import requests

MAX_INT = 2 ** 64 - 1

RUNNING_SPEED: int = 5.612          # 站内换乘速度，单位 block/s
TRANSFER_SPEED: int = 4.317         # 出站换乘速度，单位 block/s
WILD_WALKING_SPEED: int = 2.25      # 非出站换乘（越野）速度，单位 block/s

opencc1 = OpenCC('s2t')
opencc2 = OpenCC('t2jp')


def get_close_matches(words, possibilities, cutoff=0.2):
    result = [(-1, None)]
    s = SequenceMatcher()
    for word in words:
        s.set_seq2(word)
        for x, y in possibilities:
            s.set_seq1(x)
            if s.real_quick_ratio() >= cutoff and \
                    s.quick_ratio() >= cutoff:
                ratio = s.ratio()
                if ratio >= cutoff:
                    result.append((ratio, y))

    return max(result)[1]


# From https://github.com/TrueMyst/PillowFontFallback/blob/main/fontfallback/writing.py
def load_fonts(*font_paths: str) -> Dict[str, TTFont]:
    """
    Loads font files specified by paths into memory and returns a dictionary of font objects.
    """
    fonts = {}
    for path in font_paths:
        font = TTFont(path)
        fonts[path] = font
    return fonts


# From https://github.com/TrueMyst/PillowFontFallback/blob/main/fontfallback/writing.py
def has_glyph(font: TTFont, glyph: str) -> bool:
    """
    Checks if the given font contains a glyph for the specified character.
    """
    for table in font["cmap"].tables:
        if table.cmap.get(ord(glyph)):
            return True
    return False


# From https://github.com/TrueMyst/PillowFontFallback/blob/main/fontfallback/writing.py
def merge_chunks(text: str, fonts: Dict[str, TTFont]) -> List[List[str]]:
    """
    Merges consecutive characters with the same font into clusters, optimizing font lookup.
    """
    chunks = []

    for char in text:
        for font_path, font in fonts.items():
            if has_glyph(font, char):
                chunks.append([char, font_path])
                break

    cluster = chunks[:1]

    for char, font_path in chunks[1:]:
        if cluster[-1][1] == font_path:
            cluster[-1][0] += char
        else:
            cluster.append([char, font_path])

    return cluster


# From https://github.com/TrueMyst/PillowFontFallback/blob/main/fontfallback/writing.py
def draw_text_v2(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    text: str,
    color: Tuple[int, int, int],
    fonts: Dict[str, TTFont],
    size: int,
    anchor: Optional[str] = None,
    align: Literal["left", "center", "right"] = "left",
    direction: Literal["rtl", "ltr", "ttb"] = "ltr",
) -> None:
    """
    Draws text on an image at given coordinates, using specified size, color, and fonts.
    """

    y_offset = 0
    sentence = merge_chunks(text, fonts)

    for words in sentence:
        xy_ = (xy[0] + y_offset, xy[1] - 6)

        font = ImageFont.truetype(words[1], size)
        draw.text(
            xy=xy_,
            text=words[0],
            fill=color,
            font=font,
            anchor=anchor,
            align=align,
            direction=direction,
            embedded_color=True,
        )

        # draw.text
        box = font.getbbox(words[0])
        y_offset += box[2] - box[0]


# From https://github.com/trainline-eu/csa-challenge/blob/2aa0fa55e466692d404d87aa2dcaf5b83bca5920/csa.py
# class Connection:
#     def __init__(self, departure_station_id: str, arrival_station_id: str,
#                  departure_timestamp: int, arrival_timestamp: int,
#                  route_detail):
#         self[0] = int('0x' + departure_station_id, 16)
#         self[1] = int('0x' + arrival_station_id, 16)
#         self[2] = departure_timestamp
#         self[3] = arrival_timestamp
#         self.riding_time = arrival_timestamp - departure_timestamp
#         self[4] = route_detail
#         self.station_count = 1

#     def __str__(self):
#         return ' '.join([str(x) for x in
#                          (self[4],
#                           self[0], self[1],
#                           self[2], self[3],
#                           self.station_count)])


# From https://github.com/trainline-eu/csa-challenge/blob/2aa0fa55e466692d404d87aa2dcaf5b83bca5920/csa.py and https://ljn.io/posts/connection-scan-algorithm-with-interchange-time
class CSA:
    def __init__(self, max_stations, connections: list[tuple], timeout_min=2):
        self.in_connection = array('L')
        self.earliest_arrival = array('L')
        self.max_stations = max_stations
        self.connections: list[tuple] = connections
        self.timeout_min = timeout_min

    def main_loop(self, arrival_station):
        earliest = MAX_INT
        for i, c in enumerate(self.connections):
            if c[2] >= self.earliest_arrival[c[0]] and c[3] < self.earliest_arrival[c[1]]:
                self.earliest_arrival[c[1]] = c[3]
                self.in_connection[c[1]] = i

                if c[1] == arrival_station:
                    earliest = min(earliest, c[3])

            elif c[2] >= earliest:
                return

            if i % 20000 == 0:
                if time() > self.start_time + 60 * self.timeout_min:
                    raise TimeoutError('Pathfinding timeout')

    def find_path(self, arrival_station):
        route = []
        if self.in_connection[arrival_station] != MAX_INT:
            last_connection_index = self.in_connection[arrival_station]
            while last_connection_index != MAX_INT:
                connection = self.connections[last_connection_index]
                route.append(connection)
                last_connection_index = self.in_connection[connection[0]]

            route.reverse()

        return route

    def compute(self, departure_station, arrival_station, departure_time) -> list[tuple]:
        self.in_connection = array('Q', [MAX_INT for _ in range(self.max_stations)])
        self.earliest_arrival = array('Q', [MAX_INT for _ in range(self.max_stations)])
        self.earliest_arrival[departure_station] = departure_time

        if departure_station <= self.max_stations and arrival_station <= self.max_stations:
            self.start_time = time()
            self.main_loop(arrival_station)

        return self.find_path(arrival_station)


class RouteType(Enum):
    '''
    An Enum class to define the types of the route.
    '''
    IN_THEORY = 0
    WAITING = 1
    REAL_TIME = 2


class ImagePattern(Enum):
    '''
    An Enum class to define the patterns of the image.
    Number -> x offset
    THUMB -> need to -20
    '''
    OR = 0
    FAKE_STATION = 1
    TEXT = 40.2
    STATION = 40  # 圆圈 + 黑体字 -> 车站
    THUMB_TEXT = 60  # 路线种类图标 + 灰字 -> 路线名
    THUMB_INTEND_TEXT = 80
    GREY_TEXT = 40.1
    GREY_INTEND_TEXT = 60.1


def round_ten(n: float) -> int:
    '''
    Round the number in ten.
    '''
    ans = round(n / 10) * 10
    return ans if ans > 0 else 0


def atoi(text: str) -> Union[str, int]:
    '''
    Convert a string to a digit.
    '''
    return int(text) if text.isdigit() else text


def natural_keys(text: str) -> list:
    '''
    A sorting key in number order.
    '''
    return [atoi(c) for c in re.split(r'(\d+)', text)]


def lcm(a: int, b: int) -> int:
    '''
    Calculate LCM of two integers.
    '''
    return a * b // gcd(a, b)


def get_distance(a_dict: dict, b_dict: dict, square: bool = False) -> float:
    '''
    Get the distance of two stations.
    '''
    dist_square = (a_dict['x'] - b_dict['x']) ** 2 + \
        (a_dict['z'] - b_dict['z']) ** 2
    if square is True:
        return dist_square

    return sqrt(dist_square)


def fetch_data(link: str, LOCAL_FILE_PATH, MAX_WILD_BLOCKS) -> str:
    '''
    Fetch all the route data and station data.
    '''
    link = link.rstrip('/') + '/mtr/api/map/stations-and-routes?dimension=0'
    data = requests.get(link).json()['data']
    data_new = {'stations': {}, 'routes': {},
                'station_coords': {}, 'station_routes': {},
                'transfer_time': {}, 'transfer_dist': {}}
    for d in data['routes']:
        data_new['routes'][d['id']] = d
        lengths = []
        last_x = None
        for x in d['stations']:
            if x['id'] in data_new['station_routes']:
                data_new['station_routes'][x['id']] += [d['id']]
            else:
                data_new['station_routes'][x['id']] = [d['id']]

            if last_x is not None:
                x1 = last_x['x']
                y1 = last_x['y']
                z1 = last_x['z']
                x2 = x['x']
                y2 = x['y']
                z2 = x['z']
                lengths.append(((x1 - x2) ** 2 + (y1 - y2) ** 2 +
                                (z1 - z2) ** 2) ** 0.5)

            last_x = x

        data_new['routes'][d['id']]['lengths'] = lengths

    i = 0
    for d in data['stations']:
        if d['id'] not in data_new['station_routes']:
            continue

        d['station'] = hex(i)[2:]
        data_new['stations'][d['id']] = d
        i += 1

    x_dict = {x['id']: [] for x in data['stations']}
    y_dict = {x['id']: [] for x in data['stations']}
    z_dict = {x['id']: [] for x in data['stations']}
    for route in data['routes']:
        for station in route['stations']:
            x_dict[station['id']] += [station['x']]
            y_dict[station['id']] += [station['y']]
            z_dict[station['id']] += [station['z']]

    for station in data['stations']:
        x_list = x_dict[station['id']]
        y_list = y_dict[station['id']]
        z_list = z_dict[station['id']]
        if len(x_list) == 0:
            continue

        data_new['station_coords'][station['id']] = \
            {'x': sum(x_list) / len(x_list),
             'y': sum(y_list) / len(y_list),
             'z': sum(z_list) / len(z_list)}

    for x, dict1 in data_new['station_coords'].items():
        for y, dict2 in data_new['station_coords'].items():
            if x == y:
                continue

            distance = get_distance(dict1, dict2)

            if x == y:
                speed = RUNNING_SPEED
            elif x in data_new['stations'][y]['connections'] or \
                    y in data_new['stations'][x]['connections']:
                speed = TRANSFER_SPEED
            else:
                speed = WILD_WALKING_SPEED
                if distance > MAX_WILD_BLOCKS:
                    continue

                if abs(dict1['x'] - dict2['x']) > MAX_WILD_BLOCKS or \
                        abs(dict1['z'] - dict2['z']) > MAX_WILD_BLOCKS:
                    continue

            time = distance / speed
            if x not in data_new['transfer_time']:
                data_new['transfer_time'][x] = {}

            data_new['transfer_time'][x][y] = time

            if x not in data_new['transfer_dist']:
                data_new['transfer_dist'][x] = {}

            data_new['transfer_dist'][x][y] = distance

    y = input(f'是否替换{LOCAL_FILE_PATH}文件? (Y/N) ').lower()
    if y == 'y':
        with open(LOCAL_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data_new, f)

    return data_new


def gen_departure(link: str, DEP_PATH) -> None:
    '''
    Download the departures.
    '''
    link = link.rstrip('/') + '/mtr/api/map/departures?dimension=0'
    data = requests.get(link).json()['data']
    departures = data['departures']
    offset = data['cachedResponseTime']
    dep_dict: dict[str, list[int]] = {}
    for x in departures:
        dep_list = set()
        for y in x['departures']:
            # deviation = y['deviation']
            for z in y['departures']:
                # dep = round((z + offset % 86400000 + deviation) / 1000)
                dep = round((z + offset % 86400000) / 1000) % 86400
                dep_list.add(dep)

        dep_list = list(sorted(dep_list))
        dep_dict[x['id']] = dep_list

    y = input(f'是否替换{DEP_PATH}文件? (Y/N) ').lower()
    if y == 'y':
        with open(DEP_PATH, 'w', encoding='utf-8') as f:
            json.dump(dep_dict, f)


def station_name_to_id(data: dict, sta: str, STATION_TABLE,
                       fuzzy_compare=True) -> str:
    '''
    Convert one station's name to its ID.
    '''
    sta = sta.lower()
    if sta in STATION_TABLE:
        sta = STATION_TABLE[sta]

    tra1 = opencc1.convert(sta)
    sta_try = [sta, tra1, opencc2.convert(tra1)]

    all_names = []
    stations = data['stations']
    output = None
    has_station = False
    for station_id, station_dict in stations.items():
        s_1 = station_dict['name']
        all_names.append((s_1, station_id))
        s_split = station_dict['name'].split('|')
        s_2_2 = s_split[-1]
        s_2 = s_2_2.split('/')[-1]
        s_3 = s_split[0]
        for st in sta_try:
            if st in (s_1.lower(), s_2.lower(), s_2_2.lower(), s_3.lower()):
                has_station = True
                output = station_id
                break

    if has_station is False and fuzzy_compare is True:
        return get_close_matches(sta_try, all_names)

    return output


def station_num_to_name(data: dict, sta: str) -> str:
    '''
    Convert one station's code (str of base-10 int) to its name.
    '''
    sta = hex(int(sta))[2:]
    for station in data['stations'].values():
        if station['station'] == sta:
            return station['name']


def sta_id(station: str) -> int:
    return int('0x' + station, 16)


def gen_timetable(data: dict, IGNORED_LINES: list[str],
                  CALCULATE_HIGH_SPEED: bool, CALCULATE_BOAT: bool,
                  CALCULATE_WALKING_WILD: bool, ONLY_LRT: bool,
                  AVOID_STATIONS: list, route_type: RouteType,
                  original_ignored_lines: list[str], DEP_PATH: str,
                  version1: str, version2: str,
                  STATION_TABLE, WILD_ADDITION, TRANSFER_ADDITION
                  ) -> list[tuple]:
    '''
    Generate the timetable of all routes.
    '''
    if not os.path.exists('mtr_pathfinder_temp'):
        os.makedirs('mtr_pathfinder_temp')

    with open(DEP_PATH, 'r', encoding='utf-8') as f:
        dep_data: dict[str, list[int]] = json.load(f)

    filename = ''
    m = hashlib.md5()
    if IGNORED_LINES == original_ignored_lines and \
            CALCULATE_BOAT is True and ONLY_LRT is False and \
            AVOID_STATIONS == [] and route_type == RouteType.REAL_TIME:
        for s in original_ignored_lines:
            m.update(s.encode('utf-8'))

        filename = f'mtr_pathfinder_temp{os.sep}' + \
            f'4{int(CALCULATE_HIGH_SPEED)}{int(CALCULATE_WALKING_WILD)}' + \
            f'-{version1}-{version2}-{m.hexdigest()}.dat'
        if os.path.exists(filename):
            with open(filename, 'r+b') as f:
                mmapped_file = mmap.mmap(f.fileno(), 0)
                tt_dict = pickle.load(mmapped_file)

            return tt_dict

    avoid_ids = [station_name_to_id(data, x, STATION_TABLE)
                 for x in AVOID_STATIONS]

    # 添加普通路线
    TEMP_IGNORED_LINES = [x.lower() for x in IGNORED_LINES]
    tt_dict = {}
    for route_id in dep_data.keys():
        if route_id not in data['routes']:
            continue

        route = data['routes'][route_id]
        n: str = route['name']
        if n.split('|')[0].lower() in TEMP_IGNORED_LINES or \
                n.lower() in TEMP_IGNORED_LINES:
            continue

        if n.count('|') > 1:
            if n.split('|')[1].split('|')[0].lower() in TEMP_IGNORED_LINES:
                continue

        if (not CALCULATE_HIGH_SPEED) and route['type'] == 'train_high_speed':
            continue

        if (not CALCULATE_BOAT) and 'boat' in route['type']:
            continue

        if ONLY_LRT and route['type'] != 'train_light_rail':
            continue

        durations = route['durations']
        if durations == []:
            continue

        station_ids = [data['stations'][x['id']]['station']
                       for x in route['stations']]
        if len(station_ids) - 1 < len(durations):
            durations = durations[:len(station_ids) - 1]

        if len(station_ids) - 1 > len(durations):
            continue

        real_ids = [x['id'] for x in route['stations']]
        dwells = [x['dwellTime'] for x in route['stations']]
        if len(dwells) > 0:
            dep = -round(dwells[-1] / 1000)
        else:
            dep = 0

        tt = []
        for i in range(len(station_ids) - 1, 0, -1):
            station1 = station_ids[i - 1]
            station2 = station_ids[i]
            _station1 = real_ids[i - 1]
            _station2 = real_ids[i]
            dur = round(durations[i - 1] / 1000)
            arr_time = dep
            dep_time = dep - dur
            dwell = round(dwells[i - 1] / 1000)
            dep -= dur
            dep -= dwell
            if station1 == station2:
                continue

            if _station2 in avoid_ids:
                continue

            if _station1 not in avoid_ids and _station2 not in avoid_ids:
                tt.append((sta_id(station1), sta_id(station2),
                           dep_time, arr_time,
                           [route_id, route['stations'][-1]['id']]))

            # 添加出站换乘
            connections = data['stations'][_station2]['connections']
            if _station2 in TRANSFER_ADDITION:
                connections += data['stations'][TRANSFER_ADDITION[_station2]]
            for con in connections:
                if con in avoid_ids:
                    continue

                if _station2 not in data['transfer_time']:
                    continue

                if con not in data['transfer_time'][_station2]:
                    continue

                t2 = round(data['transfer_time'][_station2][con])
                dist = data['transfer_dist'][_station2][con]
                con = data['stations'][con]['station']
                tt.append((sta_id(station2), sta_id(con),
                           arr_time, arr_time + t2,
                           [f'出站换乘步行 Walk {round(dist, 2)}m', '']))

            if CALCULATE_WALKING_WILD is True:
                # 添加非出站换乘（越野）
                connections = list(data['stations'].keys())
                if _station2 in WILD_ADDITION:
                    connections += data['stations'][WILD_ADDITION[_station2]]
                for con in connections:
                    if con in avoid_ids:
                        continue

                    if _station2 not in data['transfer_time']:
                        continue

                    if con not in data['transfer_time'][_station2]:
                        continue

                    t2 = round(data['transfer_time'][_station2][con])
                    dist = data['transfer_dist'][_station2][con]
                    con = data['stations'][con]['station']
                    tt.append((sta_id(station2), sta_id(con),
                               arr_time, arr_time + t2,
                               [f'步行 Walk {round(dist, 2)}m', '']))

        tt_dict[route_id] = tt

    if filename != '':
        if not os.path.exists(filename):
            with open(filename, 'wb') as f:
                pickle.dump(tt_dict, f)

    return tt_dict


def load_tt(tt_dict: dict[tuple], data, start, end, departure_time: int,
            DEP_PATH, STATION_TABLE, TRANSFER_ADDITION,
            CALCULATE_WALKING_WILD, WILD_ADDITION, MAX_HOUR):
    with open(DEP_PATH, 'r', encoding='utf-8') as f:
        dep_data: dict[str, list[int]] = json.load(f)

    timetable: list[tuple] = []
    start_station = station_name_to_id(data, start, STATION_TABLE)
    end_station = station_name_to_id(data, end, STATION_TABLE)
    if not (start_station and end_station):
        return []

    # 添加起点出站换乘
    ss = data['stations'][start_station]['station']
    connections = data['stations'][start_station]['connections']
    if start in TRANSFER_ADDITION:
        connections += data['stations'][TRANSFER_ADDITION[start]]
    for con in connections:
        if con not in data['transfer_time'][start_station]:
            continue

        t2 = round(data['transfer_time'][start_station][con])
        dist = data['transfer_dist'][start_station][con]
        con = data['stations'][con]['station']
        timetable.append(
            (sta_id(ss), sta_id(con),
             departure_time, departure_time + t2,
             [f'出站换乘步行 Walk {round(dist, 2)}m', '']))

    if CALCULATE_WALKING_WILD is True:
        # 添加起点非出站换乘（越野）
        connections = list(data['stations'].keys())
        if start in WILD_ADDITION:
            connections += data['stations'][WILD_ADDITION[start]]
        for con in connections:
            if start_station not in data['transfer_time']:
                continue

            if con not in data['transfer_time'][start_station]:
                continue

            t2 = round(data['transfer_time'][start_station][con])
            dist = data['transfer_dist'][start_station][con]
            con = data['stations'][con]['station']
            timetable.append(
                (sta_id(ss), sta_id(con),
                 departure_time, departure_time + t2,
                 [f'步行 Walk {round(dist, 2)}m', '']))

    max_time = departure_time + MAX_HOUR * 60 * 60
    trips: dict[str, dict[str, int]] = {}
    trip_no = 0
    for route_id, departures in dep_data.items():
        if route_id not in tt_dict:
            continue

        if max_time > 86400:
            departures += [x + 86400 for x in departures if x <= max_time - 86400]

        tt = tt_dict[route_id]
        for departure in departures:
            if departure >= max_time:
                break

            trips[str(trip_no)] = {}

            for t in tt:
                _t = list(t)
                _t[2] += departure
                _t[3] += departure
                if _t[2] < 0:
                    _t[2] += 86400
                    _t[3] += 86400

                if max_time - 86400 < _t[2] < departure_time:
                    continue

                if _t[4][1] != '':  # Not walking
                    _t += [trip_no]
                    trips[str(trip_no)][str(_t[0])] = _t[2]

                timetable.append(_t)

                # if max_time > 86400 and departure <= max_time - 86400:
                #     _t[2] += 86400
                #     _t[3] += 86400
                #     if _t[4][1] != '':  # Not walking
                #         trips[str(trip_no)][str(_t[0])] = _t[2]

                #     timetable.append(_t)

            trip_no += 1

    # IMPORTANT !!! Connections must be sorted by departure/arrival time.
    timetable.sort(key=itemgetter(2))
    return timetable, trips


def process_path(result: list[tuple], start: str, end: str,
                 trips: dict[str, dict[str, int]], data: dict, detail: bool,
                 STATION_TABLE) -> list[str, int, int, int, list]:
    '''
    Process the path, change it into human readable form.
    '''
    start_station = station_name_to_id(data, start, STATION_TABLE)
    end_station = station_name_to_id(data, end, STATION_TABLE)
    if not (start_station and end_station):
        return None, None, None, None, None

    if start_station == end_station:
        return None, None, None, None, None

    path: list[tuple] = []
    last_detail: tuple = None
    route_new = []
    low_i = MAX_INT
    for i in range(len(result) - 1, -1, -1):
        if i >= low_i:
            continue

        new_leg = result[i]
        if len(new_leg) < 6:
            route_new.append(new_leg)
            continue

        trip = trips[str(new_leg[5])]
        for j in range(i - 1, -1, -1):
            trip_index = str(result[j][0])
            if trip_index not in trip:
                continue

            if trip[trip_index] >= result[j][2]:
                new_leg = [trip_index, new_leg[1], trip[trip_index],
                           new_leg[3], new_leg[4], new_leg[5]]
                low_i = j

        route_new.append(new_leg)

    route_new.reverse()
    for con in route_new:
        if con[4] != last_detail or detail is True:
            path.append(con)
        else:
            last_con = path[-1]
            last_con[3] = con[3]
            last_con[1] = con[1]

        last_detail = con[4]

    stations = data['stations']
    every_route_time = []
    for x in path:
        station_1 = x[0]
        station_2 = x[1]
        sta1_name = station_num_to_name(data, station_1).replace('|', ' ')
        sta2_name = station_num_to_name(data, station_2).replace('|', ' ')
        route_name = x[4][0]
        if route_name in data['routes']:
            z = data['routes'][route_name]
            route_name = data['routes'][route_name]['name']
            route = (z['number'] + ' ' + route_name.split('||')[0]).strip()
            route = route.replace('|', ' ')
            terminus_name: str = stations[x[4][1]]['name']
            if terminus_name.count('|') == 0:
                t1_name = t2_name = terminus_name
            else:
                t1_name = terminus_name.split('|')[0]
                t2_name = terminus_name.split('|')[1].replace('|', ' ')

            if z['circularState'] == 'CLOCKWISE':
                t1_name = '(顺时针) ' + t1_name
                t2_name += ' (Clockwise)'
            elif z['circularState'] == 'ANTICLOCKWISE':
                t1_name = '(逆时针) ' + t1_name
                t2_name += ' (Anticlockwise)'
            terminus = (t1_name, t2_name)

            color = hex(z['color']).lstrip('0x').rjust(6, '0')
            train_type = z['type']
        else:
            color = '000000'
            route = route_name
            terminus = (route_name.split('，用时')[0], 'Walk')
            train_type = None

        color = '#' + color
        r = (sta1_name, sta2_name, color, route, terminus,
             x[2], x[3], train_type)
        every_route_time.append(r)

    return every_route_time


def save_image(route_type: RouteType, every_route_time: list,
               BASE_PATH, version1, version2,
               PNG_PATH, departure_time,
               show=False) -> tuple[Image.Image, str]:
    '''
    Save the image of the route.
    '''
    pattern = []
    pattern.append(
        (ImagePattern.TEXT,
         str(strftime('%H:%M:%S', gmtime(departure_time)))))  # 出发时间
    time_img = Image.open(PNG_PATH + os.sep + 'time.png')
    for route_data in every_route_time:
        route_img = Image.open(PNG_PATH + os.sep + f'{route_data[-1]}.png')
        terminus = route_data[4][0] + '方向 To ' + route_data[4][1]

        time1 = str(strftime('%H:%M:%S', gmtime(route_data[5])))
        time2 = str(strftime('%H:%M:%S', gmtime(route_data[6])))
        time3 = str(strftime('%H:%M:%S', gmtime(route_data[6] - route_data[5])))
        if int(time3.split(':', maxsplit=1)[0]) == 0:
            time3 = ''.join(time3.split(':', maxsplit=1)[1:])

        pattern.append((ImagePattern.STATION, route_data[0],
                        route_data[2]))  # 车站
        pattern.append((ImagePattern.TEXT, time1))  # 发车时间
        pattern.append((ImagePattern.THUMB_TEXT, route_img,
                        route_data[3]))  # 路线名
        if route_data[-1] is not None:
            # 正常
            pattern.append((ImagePattern.GREY_TEXT, terminus))  # 方向

        colour = 'grey'
        pattern.append((ImagePattern.THUMB_TEXT, time_img,
                        time3, colour))  # 用时
        pattern.append((ImagePattern.TEXT, time2))  # 到站时间

    pattern.append((ImagePattern.STATION, route_data[1], route_data[2]))
    # full_time = every_route_time[-1][6] - every_route_time[0][5]
    # 总时长从出发时间开始算，不从发车时间开始算
    full_time = every_route_time[-1][6] - departure_time
    return generate_image(pattern, route_type, BASE_PATH,
                          version1, version2, full_time, show)


def calculate_height_width(pattern: list[list[ImagePattern]],
                           route_type, final_str: str,
                           final_str_size: int, BASE_PATH) -> tuple[int]:
    '''
    Calculate the width and the height of the image.
    '''
    text_size = 20
    font = ImageFont.truetype(BASE_PATH + os.sep + 'fonts' + os.sep +
                              'NotoSansKR-Regular.ttf',
                              size=text_size)
    font2 = ImageFont.truetype(BASE_PATH + os.sep + 'fonts' + os.sep +
                               'NotoSansKR-Regular.ttf',
                               size=final_str_size)
    route_len_list = [font.getlength(x[1]) + int(x[0].value) for x in pattern
                      if x[0] not in
                      [ImagePattern.FAKE_STATION, ImagePattern.OR,
                       ImagePattern.THUMB_TEXT,
                       ImagePattern.THUMB_INTEND_TEXT]]
    route_len_list += [font.getlength(x[2]) + int(x[0].value) for x in pattern
                       if x[0] in [ImagePattern.THUMB_TEXT,
                                   ImagePattern.THUMB_INTEND_TEXT]]
    if route_type != RouteType.IN_THEORY:
        len_final_str = font2.getlength(final_str) + 40
        if max(route_len_list) > len_final_str:
            width = round(max(route_len_list))
        else:
            width = round(len_final_str)
    else:
        width = round(max(route_len_list))

    height = (len([x for x in pattern
                   if x[0] not in [ImagePattern.FAKE_STATION,
                                   ImagePattern.OR]]) + 1) * 30 + 48 + 10

    return (width + 10, height)


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
              color: tuple[int, int, int],
              fonts: list[ImageFont.FreeTypeFont, dict[str, TTFont]],
              size: int) -> None:
    for x in text:
        if ord(x) >= 128:
            break
    else:
        draw.text((xy[0], xy[1] - 7), text, color, fonts[0])  # - 6
        return

    draw_text_v2(draw, xy, text, color, fonts[1], size)


def generate_image(pattern, route_type, BASE_PATH, version1, version2,
                   shortest_distance,
                   show: bool = False) -> tuple[Image.Image, str]:
    '''
    Generate the image with PIL.
    '''
    font_list = [BASE_PATH + x
                 for x in (
                    os.sep + 'fonts' + os.sep + "NotoSansSC-Regular.ttf",
                    os.sep + 'fonts' + os.sep + "NotoSansTC-Regular.ttf",
                    os.sep + 'fonts' + os.sep + "NotoSansHK-Regular.ttf",
                    os.sep + 'fonts' + os.sep + "NotoSansJP-Regular.ttf",
                    os.sep + 'fonts' + os.sep + "NotoSansKR-Regular.ttf",
                    os.sep + 'fonts' + os.sep + "NotoSansArabic-Regular.ttf",
                    os.sep + 'fonts' + os.sep +
                    "NotoSansThaiLooped-Regular.ttf",
                 )
                 ]
    font = ImageFont.FreeTypeFont(font_list[0], 20)
    fonts = (font, load_fonts(*font_list))
    gm_full = gmtime(shortest_distance)
    full_time = str(strftime('%H:%M:%S', gm_full))
    final_str = f'车站数据版本 Station data version: {version1}'
    final_str_size = 16

    if int(full_time.split(':', maxsplit=1)[0]) == 0:
        full_time = ''.join(full_time.split(':', maxsplit=1)[1:])

    image = Image.new('RGB',
                      calculate_height_width(pattern, route_type,
                                             final_str, final_str_size,
                                             BASE_PATH),
                      color='white')
    draw = ImageDraw.Draw(image)
    y = last_y = 10
    last_colour = ''
    station_y = []
    for i, pat in enumerate(pattern):
        if pat[0] == ImagePattern.OR:
            draw_text(draw, (30, y), '或', 'black', fonts, 20)
            draw_text(draw, (30, y + 30), 'or', 'black', fonts, 20)
            continue

        elif pat[0] == ImagePattern.TEXT:
            draw_text(draw, (40, y), pat[1], 'black', fonts, 20)

        elif pat[0] == ImagePattern.STATION:
            draw_text(draw, (40, y), pat[1], 'black', fonts, 20)
            if i != 1:
                draw.line(((20, last_y + 10), (20, y)), last_colour, 7)
            station_y.append(y)
            last_y = y
            last_colour = pat[2]

        elif pat[0] == ImagePattern.FAKE_STATION:
            draw.line(((20, last_y + 10), (20, y + 10)), last_colour, 7)
            last_y = y
            last_colour = pat[1]
            continue

        elif pat[0] == ImagePattern.THUMB_TEXT:
            image.paste(pat[1], (30, y - 5))
            if len(pat) > 3:
                colour = pat[3]
            else:
                colour = 'grey'

            draw_text(draw, (60, y), pat[2], colour, fonts, 20)

        elif pat[0] == ImagePattern.THUMB_INTEND_TEXT:
            image.paste(pat[1], (50, y - 5))
            if len(pat) > 3:
                colour = pat[3]
            else:
                colour = 'grey'

            draw_text(draw, (80, y), pat[2], colour, fonts, 20)

        elif pat[0] == ImagePattern.GREY_TEXT:
            draw_text(draw, (35, y), pat[1], 'grey', fonts, 20)

        elif pat[0] == ImagePattern.GREY_INTEND_TEXT:
            draw_text(draw, (55, y), pat[1], 'grey', fonts, 20)

        y += 30

    for y in station_y:
        draw.ellipse(((10, y), (30, y + 20)), fill='white',
                     outline='black', width=3)

    y += 30
    # Final str
    draw_text(draw, (40, y), f'总用时 Total Time: {full_time}',
              'grey', fonts, 20)
    y += 30

    draw_text(draw, (10, y), f'车站数据版本 Station data version: {version1}',
              'black', fonts, 16)
    y += 24
    draw_text(draw, (10, y), f'路线数据版本 Route data version: {version2}',
              'black', fonts, 16)

    output_buffer = BytesIO()
    image.save(output_buffer, 'png')
    if show is True:
        image.show()

    byte_data = output_buffer.getvalue()
    base64_str = base64.b64encode(byte_data).decode('utf-8')
    return image, base64_str


def main(station1: str, station2: str, LINK: str,
         LOCAL_FILE_PATH, DEP_PATH, BASE_PATH, PNG_PATH,
         MAX_WILD_BLOCKS: int = 1500,
         TRANSFER_ADDITION: dict[str, list[str]] = {},
         WILD_ADDITION: dict[str, list[str]] = {},
         STATION_TABLE: dict[str, str] = {},
         ORIGINAL_IGNORED_LINES: list = [], UPDATE_DATA: bool = False,
         GEN_DEPARTURE: bool = False, IGNORED_LINES: list = [],
         AVOID_STATIONS: list = [],
         CALCULATE_HIGH_SPEED: bool = True, CALCULATE_BOAT: bool = True,
         CALCULATE_WALKING_WILD: bool = False, ONLY_LRT: bool = False,
         DETAIL: bool = False, MAX_HOUR=3, timetable=None, gen_image=True,
         show=False, departure_time=None, tz=0,
         timeout_min=2) -> Union[tuple[Image.Image, str], bool, None]:
    '''
    Main function. You can call it in your own code.
    Output:
    False -- Route not found 找不到路线
    None -- Incorrect station name(s) 车站输入错误，请重新输入
    else 其他 -- base64 str of the generated image 生成图片的 base64 字符串
    '''
    if departure_time is None:
        dtz = timezone(timedelta(hours=tz))
        t1 = datetime.now().replace(year=1970, month=1, day=1)
        try:
            t1 = t1.astimezone(dtz).replace(tzinfo=timezone.utc)
        except OSError:
            t1 = t1.replace(tzinfo=timezone.utc)

        departure_time = round(t1.timestamp())
        departure_time += 10  # 寻路时间

    departure_time %= 86400

    IGNORED_LINES += ORIGINAL_IGNORED_LINES
    STATION_TABLE = {x.lower(): y.lower() for x, y in STATION_TABLE.items()}
    if LINK.endswith('/index.html'):
        LINK = LINK.rstrip('/index.html')

    if UPDATE_DATA is True or (not os.path.exists(LOCAL_FILE_PATH)):
        if LINK == '':
            raise ValueError('Railway System Map link is empty')

        data = fetch_data(LINK, LOCAL_FILE_PATH, MAX_WILD_BLOCKS)
    else:
        with open(LOCAL_FILE_PATH, encoding='utf-8') as f:
            data = json.load(f)

    if GEN_DEPARTURE is True or (not os.path.exists(DEP_PATH)):
        if LINK == '':
            raise ValueError('Railway System Map link is empty')

        gen_departure(LINK, DEP_PATH)

    version1 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(LOCAL_FILE_PATH)))
    version2 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(DEP_PATH)))

    route_type = RouteType.REAL_TIME
    if timetable is None:
        timetable = gen_timetable(
            data, IGNORED_LINES, CALCULATE_HIGH_SPEED, CALCULATE_BOAT,
            CALCULATE_WALKING_WILD, ONLY_LRT, AVOID_STATIONS, route_type,
            ORIGINAL_IGNORED_LINES, DEP_PATH, version1, version2,
            STATION_TABLE, WILD_ADDITION, TRANSFER_ADDITION)

    tt, trips = load_tt(timetable, data, station1, station2, departure_time,
                        DEP_PATH, STATION_TABLE, TRANSFER_ADDITION,
                        CALCULATE_WALKING_WILD, WILD_ADDITION, MAX_HOUR)

    csa = CSA(len(data['stations']), tt, timeout_min)
    s1 = station_name_to_id(data, station1, STATION_TABLE)
    s2 = station_name_to_id(data, station2, STATION_TABLE)
    if s1 is None or s2 is None:
        return None

    s1 = int('0x' + data['stations'][s1]['station'], 16)
    s2 = int('0x' + data['stations'][s2]['station'], 16)
    result = csa.compute(s1, s2, departure_time)
    if result == []:
        return False

    ert = process_path(result, station1, station2, trips,
                       data, DETAIL, STATION_TABLE)

    if gen_image is False:
        return ert

    if ert[0] in [False, None]:
        return ert[0]

    return save_image(route_type, ert, BASE_PATH, version1, version2,
                      PNG_PATH, departure_time, show)


def run():
    # 地图设置
    # 在线线路图网址，结尾删除"/"
    LINK: str = ''
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
    LOCAL_FILE_PATH = f'mtr-station-data-{link_hash}-mtr4-v4.json'
    DEP_PATH = f'mtr-route-departure-data-{link_hash}-mtr4-v4.json'
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

    # 出发、到达车站
    station1 = ''
    station2 = ''

    main(station1, station2, LINK, LOCAL_FILE_PATH, DEP_PATH,
         BASE_PATH, PNG_PATH, MAX_WILD_BLOCKS,
         TRANSFER_ADDITION, WILD_ADDITION, STATION_TABLE,
         ORIGINAL_IGNORED_LINES, UPDATE_DATA, GEN_DEPARTURE,
         IGNORED_LINES, AVOID_STATIONS, CALCULATE_HIGH_SPEED,
         CALCULATE_BOAT, CALCULATE_WALKING_WILD, ONLY_LRT, DETAIL, MAX_HOUR,
         show=True, departure_time=DEP_TIME)


if __name__ == '__main__':
    run()
