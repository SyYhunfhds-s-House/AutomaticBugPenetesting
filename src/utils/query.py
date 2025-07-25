from sympy import im
from urllib3 import HTTPSConnectionPool
from .core import *

import requests
import json
from pathlib import Path
from typing import Optional, Union, Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq
from colorama import init, Fore, Back, Style
init(autoreset=True)

from .core import logger

_config = load_config()

fofa_api = _config['api']['fofa']
fields = _config['fields']

_api = fofa_api['url']
_key = fofa_api['key']
_endpoint = fofa_api['endpoint']

# TODO 编写断点重新查询函数, 自动调用已有缓存的原始资产
# 目前只支持FOFA查询
pq_cache = _config['pq_cache']
pq_raw_assets_filename = pq_cache[0]  # 原始资产数据缓存文件名
del pq_cache  # 删除pq_cache变量, 避免污染全局命名空间
basedir_temp = _config['basedir']['temp']  # 缓存文件的根路径

def _check_raw_assets_cache(
    project_name: str, # 项目名称
) -> Any:
    logger.debug(f"{Fore.YELLOW}正在检查{project_name}项目是否存在原始资产缓存文件...")
    global pq_raw_assets_filename
    pq_raw_assets_path : Path = Path(basedir_temp) / project_name / pq_raw_assets_filename
    if not pq_raw_assets_path.exists():
        return None
    else:
        try:
            raw_table = pq.read_table(pq_raw_assets_path)
            if raw_table is None or raw_table.num_rows == 0:
                logger.debug(f"{Fore.YELLOW}{project_name}任务存在本地缓存文件但为空, 但未进行扫描")
                return None
            else:
                # 询问用户是否重新进行资产查询
                retry = input(f"{Fore.YELLOW}检测到本地缓存, 是否重新进行资产查询? [y/n]:{Style.RESET_ALL} ")
                if retry.lower() == 'y':
                    return None
                else:
                    logger.info(f"{Fore.GREEN}已从缓存中读取到原始资产数据, 共{raw_table.num_rows}条")
                    return pq_raw_assets_path
        except Exception as e:
            logger.error(e)
            return None
    
def test_query(query_params: dict, size: int=100, page: int = 1):
    query_string = get_query_string(fields=fields, query_params=query_params)
    params = {
        'qbase64': query_string,
        'size': size,
        'page': page,
        'key': _key,
        'fields': ','.join(fields)
    }
    # print(params)
    res = requests.get(
        url=f'{_api}{_endpoint}',
        params=params,
    )
    dict_res = json.loads(res.text)
    # from rich.console import Console
    # console = Console()
    # console.print(dict_res)
    
    temp_dir = assets_filter(project_name='test', res=dict_res, fields=fields)
    filtered_assets = pq.read_table(temp_dir)
    # console.print(filtered_assets)

# 资产扫描模块
def asset_query_fofa(
    project_name: str, # 项目名称
                     query_params: dict, 
                     size: int=100, page: int = 1,
                     timeout: int = 10
                     ): 
    """
    使用FOFA API进行资产查询，并将结果临时缓存为Parquet文件。

    Args:
        project_name (str): 项目名称，用于区分缓存目录。
        query_params (dict): 查询参数，键值对形式。
        size (int, optional): 查询返回的资产数量，默认10。
        page (int, optional): 查询页码，默认1。
        timeout (int, optional): 请求超时时间（秒），默认10。

    Returns:
        Path: Parquet文件的路径，包含清洗后的资产数据。
        None: 查询或数据处理失败时返回None。

    处理流程：
        1. 构造查询字符串并请求FOFA API。
        2. 捕获异常并记录日志。
        3. 清洗返回数据并保存为Parquet文件。
        4. 返回Parquet文件路径。
    """
    query_string = get_query_string(fields=fields, query_params=query_params)
    params = {
        'qbase64': query_string,
        'size': size,
        'page': page,
        'key': _key,
        'fields': ','.join(fields)
    }
    logger.info(f"{Fore.CYAN}正在进行{project_name}的资产查询任务")
    logger.debug(f"{Fore.BLUE}查询参数为{query_params}, 返回值列表为{fields},查询条数为{size}")
    
    raw_assets_path = _check_raw_assets_cache(project_name=project_name)
    if raw_assets_path is not None:
        return raw_assets_path
    else:
        logger.info(f"{Fore.YELLOW}缓存文件为空, 将重新进行fofa查询")
    
    if size > 100:
        logger.warning(("单次查询数据过大，可能需要较长时间，请耐心等待"))
    try:
        logger.info(f"{Fore.GREEN}开始发起fofa请求")
        res = requests.get(
            url=f'{_api}{_endpoint}',
            params=params,
            timeout=timeout
        )
        dict_res = json.loads(res.text)
        logger.info(f"{Fore.GREEN}fofa查询成功")
        # print(dict_res)
        if dict_res['size'] == 0:
            logger.warning(f"{Fore.YELLOW}未找到符合条件的资产, 程序退出")
            exit(1)
        else:
            logger.info(f"{Fore.GREEN}共有{dict_res['size']}条资产, 当前仅取前{size}条数据")
            
    except TimeoutError:
        logger.warning(("网络连接超时，请检查网络状况; 若单次查询数据过大，请适当减少查询数据或延长请求时间, \
            如设置timeout参数为更大的值"))
        exit(1)
    except Exception as e:
        logger.error(f'{Fore.RED}{e}')
        exit(1)
    
    temp_filepath = assets_filter(project_name=project_name, res=dict_res, fields=fields)
    return temp_filepath
    
if __name__ == '__main__':
    from rich.console import Console
    console = Console()
    temp_dir = asset_query_fofa(
        'hello', {
            'domain': 'baidu.com'
        }
    )
    data = pq.read_table(temp_dir)
    print(type(data.select(['link'])))
    console.print(data.select(['link']))
    