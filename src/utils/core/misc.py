from abc import ABC, abstractmethod
from typing import Any, Optional, Literal
from base64 import b64encode
from functools import lru_cache
from pathlib import Path
import json
from colorama import Fore
from pandas import DataFrame
import pyarrow as pa
import pyarrow.parquet as pq

@lru_cache(maxsize=None)
def find_project_root(start: Optional[Path] = None) -> Optional[Path]:
    """
    根据 .gitignore 寻找项目根目录。
    :param start: 起始目录，默认 Path.cwd()
    :return: 含 .gitignore 的目录 Path，或 None
    """
    start = start or Path.cwd()
    for path in (start, *start.parents):   # 当前目录 + 所有上层目录
        if (path / '.gitignore').is_file():
            return path
    return None

TAMP_DIR = 'temp'
if find_project_root() is not None:
    TAMP_DIR = find_project_root() / TAMP_DIR 
    # 这里会有蜜汁语法报错，不需要理会
TAMP_DIR = Path(TAMP_DIR)
TAMP_DIR.mkdir(exist_ok=True)

# 资产扫描器，自定义增加扫描域
class AssetScanner(ABC):
    pass

def get_query_string(fields: list, query_params: dict):
    """
    生成查询字符串。
    2025年7月17日 17:11:54改动: 移除交集检查，现在传什么查询参数就是什么
    Args:
        fields (list): 需要包含在查询字符串中的字段列表。
        query_params (dict): 查询参数，字典的键表示字段名，值表示对应的值。

    Returns:
        bytes: 生成的查询字符串的Base64编码字节串。

    Raises:
        无

    """
    # 检查fields是否与query_params的键有交集，若有交集
    # 则交集部分的键的值，与其键按如下格式进行合成：f'{key}="{value}"'
    # 所有这些合成字符串再使用AND进行连接，最后返回一个完整的查询字符串
    if not fields or not query_params:
        return b64encode("".encode())
    query_strings = []
    for key, value in query_params.items():
        # if key in fields:
        if value == '' or len(value) == 0: # 如果value为空或者长度为0，则跳过
            continue
        # 针对fofaAPI列表对不同类型的值进行不同的处理
        if isinstance(value, bool):
            query_strings.append(f'({key}={str(value).lower()})') # 不用加引号
        elif isinstance(value, list):
            multi_params_string = "||".join([
                f'{key}="{item}"' for item in value
            ])
            query_strings.append(f'({multi_params_string})')
        else:
            query_strings.append(f'{key}="{value}"')
    return b64encode(" && ".join(query_strings).encode())

def assets_filter(project_name:str | Path, res: dict | str, fields: list):
    """
    清洗资产数据并临时缓存为Parquet文件。

    Args:
        project_name (str | Path): 项目名称或路径，用于创建临时缓存目录。
        res (dict | str): 资产数据，字典或JSON字符串格式，需包含'results'键。
        fields (list): 需要保留的字段列表。

    Returns:
        Path: Parquet文件的路径。
        None: 如果数据格式错误或res['error']为True。

    处理流程：
        1. 检查res类型并转换为dict。
        2. 若res['error']为True或格式不符则返回None。
        3. 按fields清洗数据，生成资产列表。
        4. 将资产列表保存为Parquet文件，路径为项目临时目录下。
    """
    # 若res['error']为True，则返回None
    if isinstance(res, str):
        res = json.loads(res)
    if not isinstance(res, dict):
        return None # 如果res在强制转化缺省处理后仍然不是dict类型，则返回None
    if res.get('error'):
        return None
    
    global TAMP_DIR
    TAMP_DIR = TAMP_DIR / Path(project_name)
    TAMP_DIR.mkdir(exist_ok=True)
    
    raw_assets = res.get('results', []).copy()
    del res
    assets = [
        dict(zip(fields, asset))
        for asset in raw_assets
    ]
    table = pa.table({field: [asset.get(field) for asset in assets] for field in fields})
    # 给表格增加一个列"is_alive" # 默认值为True
    table = table.append_column('is_alive', pa.array([True] * len(table)))
    # 对表格按link列进行去重
    _df = table.to_pandas()
    _df = _df.drop_duplicates(subset=['link'])
    table = pa.Table.from_pandas(_df)
    del _df
    TAMP_DIR = TAMP_DIR / f"raw_assets.parquet"
    pq.write_table(table, TAMP_DIR)

    return TAMP_DIR
def merge_tables(big_table: pa.Table | DataFrame, small_table: pa.Table | DataFrame) -> pa.Table:
    """合并两个表格，支持PyArrow Table和Pandas DataFrame输入
    
    Args:
        big_table: 主表格，可以是PyArrow Table或Pandas DataFrame
        small_table: 次表格，可以是PyArrow Table或Pandas DataFrame
        
    Returns:
        pa.Table: 合并后的PyArrow Table
        
    Raises:
        TypeError: 如果输入类型不是PyArrow Table或Pandas DataFrame
        ValueError: 如果两个表没有共同字段
    """
    # 转换big_table为PyArrow Table
    if isinstance(big_table, DataFrame):
        big_table = pa.Table.from_pandas(big_table)
    elif not isinstance(big_table, pa.Table):
        raise TypeError(f"big_table必须是pyarrow.Table或pandas.DataFrame类型，实际是{type(big_table)}")
    
    # 转换small_table为PyArrow Table
    if isinstance(small_table, DataFrame):
        small_table = pa.Table.from_pandas(small_table)
    elif not isinstance(small_table, pa.Table):
        raise TypeError(f"small_table必须是pyarrow.Table或pandas.DataFrame类型，实际是{type(small_table)}")
    
    # 获取字段信息
    big_fields = set(big_table.schema.names)
    small_fields = set(small_table.schema.names)
    common_fields = big_fields & small_fields
    
    if not common_fields:
        raise ValueError("两个表没有共同的字段，无法合并")
    
    # 处理小表独有字段
    small_only_fields = small_fields - big_fields
    if small_only_fields:
        # 为大表添加小表独有的字段(设为空值)
        for field in small_only_fields:
            field_type = small_table.schema.field(field).type
            big_table = big_table.append_column(field, pa.array([None] * len(big_table), type=field_type))
    
    # 合并表格(以小表字段值为准)
    big_df = big_table.to_pandas()
    small_df = small_table.to_pandas()
    
    # 更新大表数据(用小表覆盖共有字段)
    for field in common_fields:
        # 解决链式赋值和数据类型不兼容问题
        if small_df[field].dtype == bool:
            big_df[field] = small_df[field].astype(bool)
        else:
            big_df[field] = small_df[field]
    
    # 添加小表独有字段数据
    for field in small_only_fields:
        big_df[field] = small_df[field]
    
    return pa.Table.from_pandas(big_df)

if __name__ == '__main__':
    from platform import system
    static_path = __file__
    print(static_path)
    current_os = system()
    if current_os == 'Windows':
        static_filename = static_path.split('\\')[-1]
    elif current_os == 'Linux':
        static_filename = static_path.split('/')[-1]
    print(static_filename)
    

