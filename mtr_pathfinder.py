'''
Find paths between two stations for Minecraft Transit Railway.
'''

from difflib import SequenceMatcher
from enum import Enum
from io import BytesIO
from math import gcd, sqrt
from operator import itemgetter
from statistics import median_low
from threading import Thread, BoundedSemaphore
from time import gmtime, strftime, time
from typing import Optional, Dict, Literal, Tuple, List, Union
from queue import Queue
import base64
import hashlib
import json
import os
import pickle
import re

from fontTools.ttLib import TTFont
from opencc import OpenCC
from PIL import Image, ImageDraw, ImageFont
import networkx as nx
import requests

SERVER_TICK: int = 20

DEFAULT_AVERAGE_SPEED: dict = {
    'train_normal': 14,
    'train_light_rail': 11,
    'train_high_speed': 40,
    'boat_normal': 10,
    'boat_light_rail': 10,
    'boat_high_speed': 13,
    'cable_car_normal': 8,
    'airplane_normal': 70
}                                   # 列车平均速度，单位 block/s
RUNNING_SPEED: int = 5.612          # 站内换乘速度，单位 block/s
TRANSFER_SPEED: int = 4.317         # 出站换乘速度，单位 block/s
WILD_WALKING_SPEED: int = 2.25      # 非出站换乘（越野）速度，单位 block/s

ROUTE_INTERVAL_DATA = Queue()
semaphore = BoundedSemaphore(25)
original = {}
tmp_names = {}
opencc1 = OpenCC('s2t')
opencc2 = OpenCC('t2jp')
opencc3 = OpenCC('t2s')
opencc4 = OpenCC('jp2t')


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

        draw.text
        box = font.getbbox(words[0])
        y_offset += box[2] - box[0]


# From https://github.com/TrueMyst/PillowFontFallback/blob/main/fontfallback/writing.py
def draw_text(
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
    Draws multiple lines of text on an image, handling newline characters and adjusting spacing between lines.
    """
    spacing = xy[1]
    lines = text.split("\n")

    for line in lines:
        mod_cord = (xy[0], spacing)
        draw_text_v2(
            draw,
            xy=mod_cord,
            text=line,
            color=color,
            fonts=fonts,
            size=size,
            anchor=anchor,
            align=align,
            direction=direction,
        )
        spacing += size + 5


class RouteType(Enum):
    '''
    An Enum class to define the types of the route.
    '''
    IN_THEORY = 0
    WAITING = 1


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
    return ans if ans > 0 else 10


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


def fetch_interval_data(station_id: str, LINK) -> None:
    '''
    Fetch the interval data of a station.
    '''
    global ROUTE_INTERVAL_DATA
    with semaphore:
        link = LINK + f'/arrivals?worldIndex=0&stationId={station_id}'
        try:
            data = requests.get(link).json()
        except Exception:
            pass
        else:
            ROUTE_INTERVAL_DATA.put([station_id, [time(), data]])


def gen_route_interval(LOCAL_FILE_PATH, INTERVAL_PATH, LINK, MTR_VER) -> None:
    '''
    Generate all the interval data.
    '''
    with open(LOCAL_FILE_PATH, encoding='utf-8') as f:
        data = json.load(f)

    if MTR_VER == 3:
        threads: list[Thread] = []
        for station_id in data[0]['stations']:
            t = Thread(target=fetch_interval_data, args=(station_id, LINK))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        interval_data_list = []
        while not ROUTE_INTERVAL_DATA.empty():
            interval_data_list.append(ROUTE_INTERVAL_DATA.get())

        arrivals = dict(interval_data_list)
        dep_dict_per_route: dict[str, list] = {}
        dep_dict_per_route_: dict[str, list] = {}
        for t, arrivals in arrivals.values():
            dep_dict_per_station: dict[str, list] = {}
            for arrival in arrivals[:-1]:
                name = arrival['name']
                if name in dep_dict_per_station:
                    dep_dict_per_station[name] += [arrival['arrival']]
                else:
                    dep_dict_per_station[name] = [arrival['arrival']]

            for x, item in dep_dict_per_station.items():
                dep_s_list = []
                if len(item) == 1:
                    if x not in dep_dict_per_route_:
                        dep_dict_per_route_[x] = [(item[0] / 1000 - t) * 1.25]
                else:
                    for y in range(len(item) - 1):
                        dep_s_list.append((item[y + 1] - item[y]) / 1000)
                    if x in dep_dict_per_route:
                        dep_dict_per_route[x] += [sum(dep_s_list) /
                                                  len(dep_s_list)]
                    else:
                        dep_dict_per_route[x] = [sum(dep_s_list) /
                                                 len(dep_s_list)]

        for x in dep_dict_per_route_:
            if x not in dep_dict_per_route:
                dep_dict_per_route[x] = dep_dict_per_route_[x]

        freq_dict: dict[str, list] = {}
        for route, arrivals in dep_dict_per_route.items():
            if len(arrivals) == 1:
                freq_dict[route] = round_ten(arrivals[0])
            else:
                freq_dict[route] = round_ten(sum(arrivals) / len(arrivals))

    elif MTR_VER == 4:
        link = LINK.rstrip('/') + '/mtr/api/map/departures?dimension=0'
        departures = requests.get(link).json()['data']['departures']
        dep_dict: dict[str, list[int]] = {}
        for x in departures:
            dep_list = set()
            for y in x['departures']:
                for z in y['departures']:
                    dep = round(z / 1000)
                    while dep < 0:
                        dep += 86400

                    dep_list.add(dep)

            dep_list = list(sorted(dep_list))
            dep_dict[x['id']] = dep_list

        freq_dict: dict[str, list] = {}
        for route_id, stats in dep_dict.items():
            if len(stats) == 0:
                continue

            for route_stats in data[0]['routes']:
                if route_stats['id'] == route_id:
                    break
            else:
                print(f'Route {route_id} not found')
                continue

            route_name = route_stats['name']
            freq_list = []
            for i1 in range(len(stats)):
                i2 = i1 + 1
                if i2 == len(stats):
                    i2 = 0
                    dep_2 = stats[i2] + 86400
                else:
                    dep_2 = stats[i2]

                dep_1 = stats[i1]
                freq = dep_2 - dep_1
                freq_list.append(freq)

            median_freq = median_low(freq_list)
            freq_dict[route_name] = round_ten(median_freq)

    else:
        return

    y = input(f'是否替换{INTERVAL_PATH}文件? (Y/N) ').lower()
    if y == 'y':
        with open(INTERVAL_PATH, 'w', encoding='utf-8') as f:
            json.dump(freq_dict, f)


def fetch_data(link: str, LOCAL_FILE_PATH, MTR_VER) -> str:
    '''
    Fetch all the route data and station data.
    '''
    if MTR_VER == 3:
        link = link.rstrip('/') + '/data'
        data = requests.get(link).json()
    else:
        link = link.rstrip('/') + \
            '/mtr/api/map/stations-and-routes?dimension=0'
        data = requests.get(link).json()['data']

        data_new = {'routes': [], 'stations': {}}
        i = 0
        for d in data['stations']:
            d['station'] = hex(i)[2:]
            data_new['stations'][d['id']] = d
            i += 1

        x_dict = {x['id']: [] for x in data['stations']}
        z_dict = {x['id']: [] for x in data['stations']}
        for route in data['routes']:
            # if route['hidden'] is True:
            #     continue

            if route['circularState'] == 'CLOCKWISE':
                route['circular'] = 'cw'
            elif route['circularState'] == 'ANTICLOCKWISE':
                route['circular'] = 'ccw'
            else:
                route['circular'] = ''

            route['durations'] = [round(x / 1000) for x in route['durations']]
            for station in route['stations']:
                x_dict[station['id']] += [station['x']]
                z_dict[station['id']] += [station['z']]

            # route['stations'] = [f'{x}_{route["color"]}'
            #                      for x in route['stations']]

            data_new['routes'].append(route)

        for station in data['stations']:
            x_list = x_dict[station['id']]
            z_list = z_dict[station['id']]
            if len(x_list) == 0:
                continue

            data_new['stations'][station['id']]['x'] = \
                sum(x_list) / len(x_list)
            data_new['stations'][station['id']]['z'] = \
                sum(z_list) / len(z_list)

        data = [data_new]

    y = input(f'是否替换{LOCAL_FILE_PATH}文件? (Y/N) ').lower()
    if y == 'y':
        with open(LOCAL_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    return data


def get_distance(a_dict: dict, b_dict: dict, square: bool = False) -> float:
    '''
    Get the distance of two stations.
    '''
    dist_square = (a_dict['x'] - b_dict['x']) ** 2 + \
        (a_dict['z'] - b_dict['z']) ** 2
    if square is True:
        return dist_square
    return sqrt(dist_square)


def station_name_to_id(data: list, sta: str, STATION_TABLE,
                       fuzzy_compare=True) -> str:
    '''
    Convert a station's name to its ID.
    '''
    sta = sta.lower()
    if sta in STATION_TABLE:
        sta = STATION_TABLE[sta]

    if sta in tmp_names:
        return tmp_names[sta]

    tra1 = opencc1.convert(sta)
    sta_try = [sta, tra1, opencc2.convert(tra1)]

    all_names = []
    stations = data[0]['stations']
    output = None
    has_station = False
    for station_id, station_dict in stations.items():
        s_1 = station_dict['name']
        if 'x' in station_dict and 'z' in station_dict:
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
        output = get_close_matches(sta_try, all_names)

    if output is not None:
        tmp_names[sta] = output

    return output


def get_route_station_index(route: dict, station_1_id: str, station_2_id: str,
                            MTR_VER=3) -> tuple:
    '''
    Get the index of the two stations in one route.
    '''
    if MTR_VER == 3:
        st = [x.split('_')[0] for x in route['stations']]
    else:
        st = [x['id'] for x in route['stations']]

    check_station_2 = False
    for i, station in enumerate(st):
        if station == station_1_id:
            index1 = i
            check_station_2 = True
        if check_station_2 and station == station_2_id:
            index2 = i
            break
    else:
        index1 = index2 = None

    return index1, index2


def get_approximated_time(route: dict, station_1_id: str, station_2_id: str,
                          data: list, tick: bool = False, MTR_VER=3) -> float:
    '''
    Get the approximated time of the two stations in one route.
    '''
    if MTR_VER == 4:
        return get_app_time_v4(route, station_1_id, station_2_id)

    index1, index2 = get_route_station_index(route,
                                             station_1_id, station_2_id)
    if index2 is None:
        return None

    station_1_position = {}
    station_2_position = {}
    t = 0
    stations = route['stations'][index1:index2 + 1]
    for i, station_1 in enumerate(stations):
        try:
            station_2 = stations[i + 1]
        except IndexError:
            break

        station_1_check = False
        station_2_check = False
        for k, position_dict in data[0]['positions'].items():
            if k == station_1:
                station_1_position['x'] = position_dict['x']
                station_1_position['z'] = position_dict['y']
                station_1_check = True
            elif k == station_2:
                station_2_position['x'] = position_dict['x']
                station_2_position['z'] = position_dict['y']
                station_2_check = True
            if station_1_check and station_2_check:
                t += get_distance(station_1_position, station_2_position) \
                    / DEFAULT_AVERAGE_SPEED[route['type']]
                break

    if tick is True:
        t *= 20

    return t


def get_app_time_v4(route: dict,
                    station_1_id: str, station_2_id: str) -> float:
    '''
    Get the approximated time of the two stations in one route.
    '''
    index1, index2 = get_route_station_index(route,
                                             station_1_id, station_2_id, 4)
    if index2 is None:
        return None

    t = 0
    stations = route['stations'][index1:index2 + 1]
    for i, station_1 in enumerate(stations):
        try:
            station_2 = stations[i + 1]
        except IndexError:
            break

        t += get_distance(station_1, station_2) / \
            DEFAULT_AVERAGE_SPEED[route['type']]

    return t


def create_graph(data: list, IGNORED_LINES: list[str],
                 CALCULATE_HIGH_SPEED: bool, CALCULATE_BOAT: bool,
                 CALCULATE_WALKING_WILD: bool, ONLY_LRT: bool,
                 AVOID_STATIONS: list, route_type: RouteType,
                 original_ignored_lines: list[str],
                 INTERVAL_PATH: str,
                 version1: str, version2: str,
                 LOCAL_FILE_PATH, STATION_TABLE,
                 WILD_ADDITION, TRANSFER_ADDITION,
                 MAX_WILD_BLOCKS, MTR_VER, cache) -> nx.MultiDiGraph:
    '''
    Create the graph of all routes.
    '''
    global original, intervals
    with open(INTERVAL_PATH, 'r', encoding='utf-8') as f:
        intervals = json.load(f)

    if not os.path.exists('mtr_pathfinder_temp'):
        os.makedirs('mtr_pathfinder_temp')

    filename = ''
    m = hashlib.md5()
    if cache is True and IGNORED_LINES == original_ignored_lines and \
            CALCULATE_BOAT is True and ONLY_LRT is False and \
            AVOID_STATIONS == [] and route_type == RouteType.WAITING:
        for s in original_ignored_lines:
            m.update(s.encode('utf-8'))

        filename = f'mtr_pathfinder_temp{os.sep}' + \
            f'3{int(CALCULATE_HIGH_SPEED)}{int(CALCULATE_WALKING_WILD)}' + \
            f'-{version1}-{version2}-{m.hexdigest()}.dat'
        if os.path.exists(filename):
            with open(filename, 'rb') as f:
                tup = pickle.load(f)
                G = tup[0]
                original = tup[1]

            return G

    routes = data[0]['routes']
    new_durations = {}
    for it0, route in enumerate(routes):
        name_lower = route['name'].lower()
        if 'placeholder' in name_lower or 'dummy' in name_lower:
            continue

        old_durations = route['durations']
        if 0 in old_durations or old_durations == []:
            stations = route['stations']
            new_dur = []
            for it1 in range(len(route['stations']) - 1):
                if old_durations != [] and old_durations[it1] != 0:
                    new_dur.append(old_durations[it1])
                    continue

                it2 = it1 + 1
                if MTR_VER == 3:
                    station_1 = stations[it1].split('_')[0]
                    station_2 = stations[it2].split('_')[0]
                else:
                    station_1 = stations[it1]['id']
                    station_2 = stations[it2]['id']

                app_time = get_approximated_time(route, station_1, station_2,
                                                 data, True, MTR_VER)
                if app_time == 0:
                    app_time = 0.01
                new_dur.append(app_time)

            if sum(new_dur) == 0:
                continue

            new_durations[str(it0)] = new_dur

    if len(new_durations) > 0:
        for route_id, new_duration in new_durations.items():
            route_id = int(route_id)
            old_route_data = data[0]['routes'][route_id]
            old_route_data['durations'] = new_duration
            data[0]['routes'][route_id] = old_route_data

        with open(LOCAL_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    avoid_ids = [station_name_to_id(data, x, STATION_TABLE)
                 for x in AVOID_STATIONS]

    all_stations = data[0]['stations']
    G = nx.MultiDiGraph()
    edges_dict = {}
    edges_attr_dict = {}
    original = {}
    waiting_walking_dict = {}

    # 添加出站换乘
    for station, station_dict in all_stations.items():
        if 'x' not in station_dict or 'z' not in station_dict:
            continue

        if station in avoid_ids:
            continue

        for transfer in station_dict['connections']:
            if transfer not in all_stations:
                continue

            if transfer in avoid_ids:
                continue

            transfer_dict = all_stations[transfer]
            if 'x' not in transfer_dict or 'z' not in transfer_dict:
                continue

            dist = get_distance(station_dict, transfer_dict)
            duration = dist / TRANSFER_SPEED

            if (station, transfer) in edges_attr_dict:
                edges_attr_dict[(station, transfer)].append(
                    (f'出站换乘步行 Walk {round(dist, 2)}m', duration, 0))
            else:
                edges_attr_dict[(station, transfer)] = [
                    (f'出站换乘步行 Walk {round(dist, 2)}m', duration, 0)]
            waiting_walking_dict[(station, transfer)] = \
                (duration, f'出站换乘步行 Walk {round(dist, 2)}m')

        additions1 = set()
        if station_dict['name'] in TRANSFER_ADDITION:
            for x in TRANSFER_ADDITION[station_dict['name']]:
                additions1.add(x)

        for x in additions1:
            for station2, station2_dict in all_stations.items():
                if station2 in avoid_ids:
                    continue

                if station2_dict['name'] == x:
                    if station2 not in station_dict['connections']:
                        try:
                            dist = get_distance(station_dict, station2_dict)
                            duration = dist / TRANSFER_SPEED
                            if (station, station2) not in edges_attr_dict:
                                edges_attr_dict[(station, station2)] = []
                            edges_attr_dict[(station, station2)].append(
                                (f'出站换乘步行 Walk {round(dist, 2)}m',
                                 duration, 0))
                            waiting_walking_dict[(station, station2)] = \
                                (duration, f'出站换乘步行 Walk {round(dist, 2)}m')
                        except KeyError:
                            pass

                        break

        additions2 = set()
        if station_dict['name'] in WILD_ADDITION and \
                CALCULATE_WALKING_WILD is True:
            for x in WILD_ADDITION[station_dict['name']]:
                additions2.add(x)

        for x in additions2:
            for station2, station2_dict in all_stations.items():
                if station2 in avoid_ids:
                    continue

                if station2_dict['name'] == x:
                    if station2 not in station_dict['connections']:
                        try:
                            dist = get_distance(station_dict, station2_dict)
                            duration = dist / WILD_WALKING_SPEED
                            if (station, station2) not in edges_attr_dict:
                                edges_attr_dict[(station, station2)] = []

                            edges_attr_dict[(station, station2)].append(
                                (f'步行 Walk {round(dist, 2)}m', duration, 0))
                            waiting_walking_dict[(station, station2)] = \
                                (duration, f'步行 Walk {round(dist, 2)}m')
                        except KeyError:
                            pass

                        break

    TEMP_IGNORED_LINES = [x.lower().strip() for x in IGNORED_LINES if x != '']
    # 添加普通路线
    for route in data[0]['routes']:
        n: str = route['name']
        # 禁路线
        number: str = route['number']
        route_names = [n, n.split('|')[0]]
        if ('||' in n and n.count('|') > 2) or \
                ('||' not in n and n.count('|') > 0):
            eng_name = n.split('|')[1].split('|')[0]
            if eng_name != '':
                route_names.append(eng_name)

        if number not in ['', ' ']:
            for tmp_name in route_names[1:]:
                route_names.append(tmp_name + ' ' + number)

        cont = False
        for x in route_names:
            x = x.lower().strip()
            if x in TEMP_IGNORED_LINES:
                cont = True
                break

            if x.isascii():
                continue

            simp1 = opencc3.convert(x)
            if simp1 in TEMP_IGNORED_LINES:
                cont = True
                break

            simp2 = opencc3.convert(opencc4.convert(x))
            if simp2 in TEMP_IGNORED_LINES:
                cont = True
                break

        if cont is True:
            continue

        if (not CALCULATE_HIGH_SPEED) and route['type'] == 'train_high_speed':
            continue

        if (not CALCULATE_BOAT) and 'boat' in route['type']:
            continue

        if ONLY_LRT and route['type'] != 'train_light_rail':
            continue

        if route_type == RouteType.WAITING:
            if route['type'] == 'cable_car_normal':
                intervals[n] = 2

            if n not in intervals:
                continue

        stations = route['stations']
        durations = route['durations']
        if len(stations) < 2:
            continue

        if len(stations) - 1 < len(durations):
            durations = durations[:len(stations) - 1]

        if len(stations) - 1 > len(durations):
            continue

        # if route_type == RouteType.WAITING:
        for i in range(len(durations)):
            for i2 in range(len(durations[i:])):
                i2 += i + 1
                if MTR_VER == 3:
                    station_1 = stations[i].split('_')[0]
                    station_2 = stations[i2].split('_')[0]
                    dur_list = durations[i:i2]
                    station_list = stations[i:i2 + 1]
                    c = False
                    for sta in station_list:
                        if sta.split('_')[0] in avoid_ids:
                            c = True
                    if c is True:
                        continue

                    if 0 in dur_list:
                        t = get_approximated_time(route, station_1, station_2,
                                                  data, MTR_VER)
                        if t is None:
                            continue
                        dur = t
                    else:
                        dur = sum(durations[i:i2]) / SERVER_TICK

                else:
                    station_1 = stations[i]
                    station_2 = stations[i2]
                    dur_list = durations[i:i2]
                    station_list = stations[i:i2 + 1]
                    dwell = sum([x['dwellTime'] / 1000
                                 for x in station_list][1:-1])
                    c = False
                    for sta in station_list:
                        if sta['id'] in avoid_ids:
                            c = True
                    if c is True:
                        continue

                    if 0 in dur_list:
                        t = get_app_time_v4(route, station_1, station_2,
                                            data, MTR_VER)
                        if t is None:
                            continue
                        dur = round(t + dwell)
                    else:
                        dur = round(sum(durations[i:i2]) + dwell)

                    station_1 = station_1['id']
                    station_2 = station_2['id']

                if route_type == RouteType.WAITING:
                    wait = float(intervals[n])
                    if (station_1, station_2) not in edges_dict:
                        edges_dict[(station_1, station_2)] = []

                    edges_dict[(station_1, station_2)].append(
                        (dur, wait, route['name']))

                    original_tuple = (route['name'], station_1, station_2)
                    if original_tuple in original:
                        dur1 = original[original_tuple]
                        if dur < dur1:
                            original[original_tuple] = dur
                    else:
                        original[original_tuple] = dur
                else:
                    if (station_1, station_2) in edges_attr_dict:
                        edges_attr_dict[(station_1, station_2)].append(
                            (route['name'], dur, 0))
                    else:
                        edges_attr_dict[(station_1, station_2)] = [
                            (route['name'], dur, 0)]
        # else:
            # for i, duration in enumerate(durations):
            #     station_1 = stations[i].split('_')[0]
            #     station_2 = stations[i + 1].split('_')[0]
            #     station_list = stations[i:i + 2]
            #     c = False
            #     for sta in station_list:
            #         if sta.split('_')[0] in avoid_ids:
            #             c = True
            #     if c is True:
            #         continue

            #     add_edge = False
            #     if duration == 0:
            #         t = get_approximated_time(route, station_1, station_2,
            #                                   data, MTR_VER)
            #         if t is not None:
            #             add_edge = True
            #     else:
            #         add_edge = True
            #         t = duration / SERVER_TICK

            #     if add_edge is True:
            #         if (station_1, station_2) in edges_attr_dict:
            #             edges_attr_dict[(station_1, station_2)].append(
            #                 (route['name'], t, 0))
            #         else:
            #             edges_attr_dict[(station_1, station_2)] = [
            #                 (route['name'], t, 0)]

    if route_type == RouteType.WAITING:
        for tup, dur_tup in edges_dict.items():
            dur = [x[0] for x in dur_tup]
            wait = [x[1] for x in dur_tup]
            routes = [x[2] for x in dur_tup]
            final_wait = []
            final_routes = []
            min_dur = min(dur)
            for i, x in enumerate(dur):
                if abs(x - min_dur) <= 60:
                    final_wait.append(wait[i])
                    final_routes.append(routes[i])

            s1 = tup[0]
            s2 = tup[1]
            lcm_sum = 1
            sum_interval = 0
            for x in final_wait:
                if x != 0:
                    lcm_sum = lcm(lcm_sum, round(x))
            for x in final_wait:
                if x != 0:
                    sum_interval += (lcm_sum / round(x))

            if sum_interval == 0:
                sum_int = 0
            else:
                sum_int = lcm_sum / sum_interval / 2

            if (s1, s2) in waiting_walking_dict:
                t = waiting_walking_dict[(s1, s2)][0]
                if abs(t - min_dur) <= 60:
                    route_name = waiting_walking_dict[(s1, s2)][1]
                    dur = waiting_walking_dict[(s1, s2)][0]
                    final_routes.append(route_name)
                    original[(route_name, s1, s2)] = dur

            edges_attr_dict[(s1, s2)] = [(final_routes, min_dur, sum_int)]

    for edge in edges_attr_dict.items():
        u, v = edge[0]
        min_time = min(e[1] + e[2] for e in edge[1])
        for r in edge[1]:
            route_name = r[0]
            duration = r[1]
            waiting_time = r[2]
            weight = duration + waiting_time
            if abs(weight - min_time) <= 60 and weight > 0:
                G.add_edge(u, v, weight=weight, name=route_name,
                           waiting=waiting_time)

    # 添加野外行走 (无铁路连接)
    if CALCULATE_WALKING_WILD is True:
        edges_attr_dict = {}
        for station, station_dict in all_stations.items():
            if station in avoid_ids:
                continue

            if 'x' not in station_dict or 'z' not in station_dict:
                continue

            for station2, station2_dict in all_stations.items():
                if station2 in avoid_ids:
                    continue

                if 'x' not in station2_dict or 'z' not in station2_dict:
                    continue

                if station == station2:
                    continue

                if (station, station2) in waiting_walking_dict:
                    continue

                dist = get_distance(station_dict, station2_dict, True)
                if dist <= (MAX_WILD_BLOCKS ** 2):
                    dist = sqrt(dist)
                    duration = dist / WILD_WALKING_SPEED
                    if G.has_edge(station, station2) and \
                            duration - G[station][station2][0]['weight'] > 60:
                        continue

                    edges_attr_dict[(station, station2)] = [
                        (f'步行 Walk {round(dist, 2)}m', duration, 0)]
                    if G.has_edge(station, station2) and \
                            duration + 120 < \
                            G[station][station2][0]['weight']:
                        G.remove_edge(station, station2)

        for edge in edges_attr_dict.items():
            u, v = edge[0]
            for r in edge[1]:
                route_name = r[0]
                duration = r[1]
                waiting_time = r[2]
                G.add_edge(u, v, weight=duration, name=route_name,
                           waiting=waiting_time)

    if filename != '':
        if not os.path.exists(filename):
            with open(filename, 'wb') as f:
                pickle.dump((G, original), f)

    return G


def find_shortest_route(G: nx.MultiDiGraph, start: str, end: str,
                        data: list, STATION_TABLE,
                        MTR_VER) -> list[str, int, int, int, list]:
    '''
    Find the shortest route between two stations.
    '''

    start_station = station_name_to_id(data, start, STATION_TABLE)
    end_station = station_name_to_id(data, end, STATION_TABLE)
    if not (start_station and end_station):
        return None, None, None, None, None

    if start_station == end_station:
        return None, None, None, None, None

    shortest_path = []
    shortest_distance = -1
    try:
        shortest_path = nx.all_shortest_paths(G, start_station,
                                              end_station, weight='weight')
        shortest_path = list(sorted(shortest_path, key=lambda x: len(x)))[0]
        shortest_distance = nx.shortest_path_length(G, start_station,
                                                    end_station,
                                                    weight='weight')
    except nx.exception.NetworkXNoPath:
        return False, False, False, False, False
    except nx.exception.NodeNotFound:
        return False, False, False, False, False

    return process_path(G, shortest_path, shortest_distance, data, MTR_VER)


def process_path(G: nx.MultiDiGraph, path: list, shortest_distance: int,
                 data: list, MTR_VER) -> list[str, int, int, int, list]:
    '''
    Process the path, change it into human readable form.
    '''
    stations = data[0]['stations']
    routes = data[0]['routes']
    station_names = [stations[path[0]]['name']]
    every_route_time = []
    each_route_time = []
    waiting_time = 0
    for i in range(len(path) - 1):
        station_1 = path[i]
        station_2 = path[i + 1]
        edge = G[station_1][station_2]
        duration_list = []
        waiting_list = []
        route_name_list = []
        for v in edge.values():
            duration = v['weight']
            route_name = v['name']
            waiting = v['waiting']
            duration_list.append((route_name, duration))
            waiting_list.append((route_name, waiting))
            if isinstance(route_name, list):
                route_name_list.extend(route_name)
            elif isinstance(route_name, str):
                route_name_list.append(route_name)
            waiting_time += waiting

        if len(route_name_list) == 1:
            route_name = route_name_list[0]
        else:
            route_name = '(' + ' / '.join(route_name_list) + ')'

        station_names.append(route_name)
        station_names.append(stations[path[i + 1]]['name'])

        sta1_name = stations[station_1]['name'].replace('|', ' ')
        sta2_name = stations[station_2]['name'].replace('|', ' ')
        sta1_id = stations[station_1]['id']
        for route_name in route_name_list:
            for x in duration_list:
                if route_name == x[0]:
                    duration = x[1]
                    break
            else:
                for x in duration_list:
                    for y in x[0]:
                        if route_name == y:
                            duration = original[(route_name,
                                                 station_1, station_2)]
                            break

            for x in waiting_list:
                if route_name == x[0]:
                    waiting = x[1]
                    break
            else:
                for x in waiting_list:
                    for y in x[0]:
                        if route_name == y:
                            waiting = x[1]
                            break

            for z in routes:
                if z['name'] == route_name:
                    route = (z['number'] + ' ' +
                             route_name.split('||')[0]).strip()
                    route = route.replace('|', ' ')
                    next_id = None
                    if MTR_VER == 3:
                        sta_id = z['stations'][-1].split('_')[0]
                        for q, x in enumerate(z['stations']):
                            if x.split('_')[0] == sta1_id and \
                                    q != len(z['stations']) - 1:
                                next_id = z['stations'][q + 1].split('_')[0]
                                break
                    else:
                        sta_id = z['stations'][-1]['id']
                        for q, x in enumerate(z['stations']):
                            if x['id'] == sta1_id and \
                                    q != len(z['stations']) - 1:
                                next_id = z['stations'][q + 1]['id']
                                break

                    if z['circular'] in ['cw', 'ccw']:
                        sta_id = next_id

                    terminus_name: str = stations[sta_id]['name']
                    if terminus_name.count('|') == 0:
                        t1_name = t2_name = terminus_name
                    else:
                        t1_name = terminus_name.split('|')[0]
                        t2_name = terminus_name.split('|')[1].replace('|',
                                                                      ' ')

                    if z['circular'] == 'cw':
                        if next_id is None:
                            t1_name = '(顺时针) ' + t1_name
                            t2_name += ' (Clockwise)'
                            terminus = (t1_name, t2_name)
                        else:
                            name1 = '(顺时针) 经由' + t1_name
                            name2 = f'(Clockwise) Via {t2_name}'
                            terminus = (True, name1, name2)
                    elif z['circular'] == 'ccw':
                        if next_id is None:
                            t1_name = '(逆时针) ' + t1_name
                            t2_name += ' (Counterclockwise)'
                            terminus = (t1_name, t2_name)
                        else:
                            name1 = '(逆时针) 经由' + t1_name
                            name2 = f'(Counterclockwise) Via {t2_name}'
                            terminus = (True, name1, name2)
                    else:
                        terminus = (t1_name, t2_name)

                    color = hex(z['color']).lstrip('0x').rjust(6, '0')
                    train_type = z['type']
                    break
            else:
                color = '000000'
                route = route_name
                terminus = (route_name.split('，用时')[0], 'Walk')
                train_type = None

            color = '#' + color

            sep_waiting = None
            if route_name in intervals:
                sep_waiting = int(intervals[route_name])

            r = (sta1_name, sta2_name, color, route, terminus, duration,
                 waiting, sep_waiting, train_type)

            if len(each_route_time) > 0:
                old_r = each_route_time[-1]
                if old_r[:5] != r[:5] or \
                        round(old_r[5]) != round(r[5]):
                    each_route_time.append(r)

            if len(each_route_time) == 0:
                each_route_time.append(r)

        # each_route_time.sort(key=itemgetter(4))
        each_route_time.sort(key=lambda x: natural_keys(x[3]))
        each_route_time.sort(key=itemgetter(5))
        every_route_time.extend(each_route_time)

        each_route_time = []
        duration = 0
        waiting = 0

    end_ = stations[station_2]['name']
    if station_names[-1] != end_:
        station_names += end_

    return ' ->\n'.join(station_names), shortest_distance, \
        waiting_time, shortest_distance - waiting_time, every_route_time


def save_image(route_type: RouteType, every_route_time: list,
               shortest_distance, riding_time, waiting_time,
               BASE_PATH, version1, version2,
               DETAIL, PNG_PATH, show=False) -> tuple[Image.Image, str]:
    '''
    Save the image of the route.
    '''
    pattern = []
    last_sta = ()
    time_img = Image.open(PNG_PATH + os.sep + 'time.png')
    for route_data in every_route_time:
        now_sta = (route_data[0], route_data[1])
        route_img = Image.open(PNG_PATH + os.sep + f'{route_data[-1]}.png')
        if route_data[4][0] is True:
            terminus = ' '.join(route_data[4][1:])
        else:
            terminus = route_data[4][0] + '方向 To ' + route_data[4][1]

        time1 = str(strftime('%H:%M:%S', gmtime(route_data[5])))
        time2 = str(strftime('%H:%M:%S', gmtime(route_data[6])))
        time3 = str(strftime('%H:%M:%S', gmtime(route_data[7])))
        if int(time1.split(':', maxsplit=1)[0]) == 0:
            time1 = ''.join(time1.split(':', maxsplit=1)[1:])
        if int(time2.split(':', maxsplit=1)[0]) == 0:
            time2 = ''.join(time2.split(':', maxsplit=1)[1:])
        if int(time3.split(':', maxsplit=1)[0]) == 0:
            time3 = ''.join(time3.split(':', maxsplit=1)[1:])

        if now_sta != last_sta:
            # 正常
            pattern.append((ImagePattern.STATION, route_data[0],
                            route_data[2]))  # 车站
            if DETAIL and route_type == RouteType.WAITING and \
                    route_data[-1] is not None:
                pattern.append((ImagePattern.TEXT, f'等车 Wait {time2}'))  # 车站
            pattern.append((ImagePattern.THUMB_TEXT, route_img,
                            route_data[3]))  # 路线名
            if route_data[-1] is not None:
                # 正常
                pattern.append((ImagePattern.GREY_TEXT, terminus))  # 方向

            if DETAIL and route_type == RouteType.WAITING and \
                    route_data[-1] is not None:
                pattern.append((ImagePattern.THUMB_TEXT, time_img,
                                f'间隔 Interval {time3}'))

            prefix = ''
            colour = 'grey'
            if DETAIL and route_data[-1] is not None:
                prefix = '乘车 Ride '
                colour = 'black'
            pattern.append((ImagePattern.THUMB_TEXT, time_img,
                            prefix + time1, colour))  # 用时
        else:
            pattern.append((ImagePattern.OR, ))
            pattern.append((ImagePattern.FAKE_STATION, route_data[2]))
            # 有缩进
            pattern.append((ImagePattern.THUMB_INTEND_TEXT, route_img,
                            route_data[3]))  # 路线名
            if route_data[-1] is not None:
                # 正常
                pattern.append((ImagePattern.GREY_INTEND_TEXT,
                                terminus))  # 方向

            if DETAIL and route_type == RouteType.WAITING and \
                    route_data[-1] is not None:
                pattern.append((ImagePattern.THUMB_INTEND_TEXT, time_img,
                                f'间隔 Interval {time3}'))  # 用时

            prefix = ''
            colour = 'grey'
            if DETAIL and route_data[-1] is not None:
                prefix = '乘车 Ride '
                colour = 'black'
            pattern.append((ImagePattern.THUMB_INTEND_TEXT, time_img,
                            prefix + time1, colour))  # 用时

        last_sta = (route_data[0], route_data[1])

    pattern.append((ImagePattern.STATION, route_data[1], route_data[2]))

    return generate_image(pattern, shortest_distance, riding_time,
                          waiting_time, route_type, BASE_PATH,
                          version1, version2, show)


def calculate_height_width(pattern: list[list[ImagePattern]],
                           route_type, final_str: str,
                           final_str_size: int, BASE_PATH,
                           version1, version2) -> tuple[int]:
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
    if route_type != RouteType.IN_THEORY:
        height += 60

    return (width + 10, height)


def generate_image(pattern, shortest_distance, riding_time, waiting_time,
                   route_type, BASE_PATH, version1, version2,
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
    fonts = load_fonts(*font_list)
    gm_full = gmtime(shortest_distance)
    gm_waiting = gmtime(waiting_time)
    gm_travelling = gmtime(riding_time)
    full_time = str(strftime('%H:%M:%S', gm_full))
    waiting_time = str(strftime('%H:%M:%S', gm_waiting))
    travelling_time = str(strftime('%H:%M:%S', gm_travelling))
    if travelling_time[1] == '0':
        final_str = f'车站数据版本 Station data version: {version1}'
        final_str_size = 16
    else:
        final_str = f'其中乘车时间 Travelling Time: {travelling_time}'
        final_str_size = 20

    if int(full_time.split(':', maxsplit=1)[0]) == 0:
        full_time = ''.join(full_time.split(':', maxsplit=1)[1:])
    if int(waiting_time.split(':', maxsplit=1)[0]) == 0:
        waiting_time = ''.join(waiting_time.split(':', maxsplit=1)[1:])
    if int(travelling_time.split(':', maxsplit=1)[0]) == 0:
        travelling_time = ''.join(travelling_time.split(':', maxsplit=1)[1:])

    image = Image.new('RGB',
                      calculate_height_width(pattern, route_type,
                                             final_str, final_str_size,
                                             BASE_PATH, version1, version2),
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
            if i != 0:
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
    if route_type == RouteType.IN_THEORY:
        draw_text(draw, (40, y), f'总用时 Total Time: {full_time}',
                  'grey', fonts, 20)
        y += 30
    else:
        draw_text(draw, (40, y), f'总用时 Total Time: {full_time}',
                  'grey', fonts, 20)
        y += 30
        draw_text(draw, (40, y),
                  f'其中乘车时间 Travelling Time: {travelling_time}',
                  'grey', fonts, 20)
        y += 30
        draw_text(draw, (40, y), f'其中等车时间 Waiting Time: {waiting_time}',
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
         LOCAL_FILE_PATH, INTERVAL_PATH, BASE_PATH, PNG_PATH,
         MAX_WILD_BLOCKS: int = 1500,
         TRANSFER_ADDITION: dict[str, list[str]] = {},
         WILD_ADDITION: dict[str, list[str]] = {},
         STATION_TABLE: dict[str, str] = {},
         ORIGINAL_IGNORED_LINES: list = [], UPDATE_DATA: bool = False,
         GEN_ROUTE_INTERVAL: bool = False, IGNORED_LINES: list = [],
         AVOID_STATIONS: list = [], CALCULATE_HIGH_SPEED: bool = True,
         CALCULATE_BOAT: bool = True, CALCULATE_WALKING_WILD: bool = False,
         ONLY_LRT: bool = False, IN_THEORY: bool = False, DETAIL: bool = False,
         MTR_VER: int = 3, G=None, gen_image=True, show=False,
         cache=True) -> Union[tuple[Image.Image, str], bool, None]:
    '''
    Main function. You can call it in your own code.
    Output:
    False -- Route not found 找不到路线
    None -- Incorrect station name(s) 车站输入错误，请重新输入
    else 其他 -- tuple
    (image object, base64 str of the generated image)
    (图片对象, 生成图片的 base64 字符串)
    '''
    if MTR_VER not in [3, 4]:
        raise NotImplementedError('MTR_VER should be 3 or 4')

    IGNORED_LINES += ORIGINAL_IGNORED_LINES
    STATION_TABLE = {x.lower(): y.lower() for x, y in STATION_TABLE.items()}
    if LINK.endswith('/index.html'):
        LINK = LINK.rstrip('/index.html')

    if UPDATE_DATA is True or (not os.path.exists(LOCAL_FILE_PATH)):
        if LINK == '':
            raise ValueError('Railway System Map link is empty')

        data = fetch_data(LINK, LOCAL_FILE_PATH, MTR_VER)
    else:
        with open(LOCAL_FILE_PATH, encoding='utf-8') as f:
            data = json.load(f)

    if GEN_ROUTE_INTERVAL is True or (not os.path.exists(INTERVAL_PATH)):
        # if MTR_VER == 4:
        #     raise NotImplementedError(
        #         'Please use the real-time pathfinder for MTR 4.0.0 '
        #         'or input interval data manually.')

        if LINK == '':
            raise ValueError('Railway System Map link is empty')

        gen_route_interval(LOCAL_FILE_PATH, INTERVAL_PATH, LINK, MTR_VER)

    version1 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(LOCAL_FILE_PATH)))
    version2 = strftime('%Y%m%d-%H%M',
                        gmtime(os.path.getmtime(INTERVAL_PATH)))

    if IN_THEORY is True:
        route_type = RouteType.IN_THEORY
    else:
        route_type = RouteType.WAITING

    if G is None:
        G = create_graph(data, IGNORED_LINES, CALCULATE_HIGH_SPEED,
                         CALCULATE_BOAT, CALCULATE_WALKING_WILD, ONLY_LRT,
                         AVOID_STATIONS, route_type, ORIGINAL_IGNORED_LINES,
                         INTERVAL_PATH, version1, version2, LOCAL_FILE_PATH,
                         STATION_TABLE, WILD_ADDITION, TRANSFER_ADDITION,
                         MAX_WILD_BLOCKS, MTR_VER, cache)

    shortest_path, shortest_distance, waiting_time, riding_time, ert = \
        find_shortest_route(G, station1, station2,
                            data, STATION_TABLE, MTR_VER)

    if gen_image is False:
        return ert, shortest_distance

    if shortest_path in [False, None]:
        return shortest_path

    return save_image(route_type, ert, shortest_distance, riding_time,
                      waiting_time, BASE_PATH, version1, version2, DETAIL,
                      PNG_PATH, show)


def run():
    # 地图设置
    # MTR模组版本（3/4），默认值为3
    MTR_VER: int = 3
    # 在线线路图网址，结尾删除"/"
    LINK: str = ''
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

    link_hash = hashlib.md5(LINK.encode('utf-8')).hexdigest()
    # 文件设置
    LOCAL_FILE_PATH = f'mtr-station-data-{link_hash}-mtr{MTR_VER}-v3.json'
    INTERVAL_PATH = f'mtr-route-data-{link_hash}-mtr{MTR_VER}-v3.json'
    BASE_PATH = 'mtr_pathfinder_data'
    PNG_PATH = 'mtr_pathfinder_data'

    # 是否更新车站数据
    UPDATE_DATA: bool = False
    # 是否更新路线数据
    GEN_ROUTE_INTERVAL: bool = False

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
    # 计算理论最快路线，不考虑等车时间，默认值为False
    IN_THEORY: bool = False

    # 输出的图片中是否显示详细信息，默认值为False
    DETAIL: bool = False

    # 出发、到达车站
    station1 = ''
    station2 = ''

    main(station1, station2, LINK, LOCAL_FILE_PATH, INTERVAL_PATH,
         BASE_PATH, PNG_PATH, MAX_WILD_BLOCKS,
         TRANSFER_ADDITION, WILD_ADDITION, STATION_TABLE,
         ORIGINAL_IGNORED_LINES, UPDATE_DATA, GEN_ROUTE_INTERVAL,
         IGNORED_LINES, AVOID_STATIONS, CALCULATE_HIGH_SPEED,
         CALCULATE_BOAT, CALCULATE_WALKING_WILD, ONLY_LRT, IN_THEORY, DETAIL,
         MTR_VER, show=True)


if __name__ == '__main__':
    run()
