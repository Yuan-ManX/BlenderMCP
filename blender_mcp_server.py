import socket
import json
import asyncio
import logging
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List

from mcp.server.fastmcp import FastMCP, Context, Image


# 配置日志系统
logging.basicConfig(level=logging.INFO,  # 设置日志级别为 INFO
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s') # 设置日志格式
# 获取名为 "BlenderMCPServer" 的日志记录器
logger = logging.getLogger("BlenderMCPServer")


@dataclass
class BlenderConnection:
    """
    BlenderConnection 类用于管理与 Blender 插件的套接字连接。

    属性:
        host (str): Blender 插件服务器的主机名或 IP 地址。
        port (int): Blender 插件服务器的端口号。
        sock (socket.socket, 可选): 用于与 Blender 插件通信的套接字对象，默认为 None。
    """
    host: str
    port: int
    sock: socket.socket = None  # 用于与 Blender 插件通信的套接字对象
    
    def connect(self) -> bool:
        """
        Connect to the Blender addon socket server
        连接到 Blender 插件的套接字服务器。

        返回:
            bool: 如果连接成功则返回 True，否则返回 False。
        """
        if self.sock:
            # 如果已经连接，则直接返回 True
            return True
            
        try:
            # 创建一个 TCP 套接字
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 连接到 Blender 插件服务器
            self.sock.connect((self.host, self.port))
            # 记录连接成功的信息
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            # 连接成功，返回 True
            return True
        except Exception as e:
            # 记录连接失败的错误信息
            logger.error(f"Failed to connect to Blender: {str(e)}")
            # 重置套接字对象为 None
            self.sock = None
            # 连接失败，返回 False
            return False
    
    def disconnect(self):
        """
        Disconnect from the Blender addon
        断开与 Blender 插件的连接。
        """
        if self.sock:
            try:
                # 关闭套接字连接
                self.sock.close()
            except Exception as e:
                # 记录断开连接时的错误
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                # 重置套接字对象为 None
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        """
        接收完整的响应，可能需要接收多个数据块。

        参数:
            sock (socket.socket): 用于接收数据的套接字。
            buffer_size (int, 可选): 每次接收数据的缓冲区大小，默认为 8192 字节。

        返回:
            bytes: 完整的响应数据。

        异常:
            Exception: 如果接收到的数据不完整或发生错误，则抛出异常。
        """
        # 用于存储接收到的数据块
        chunks = []
        # 设置套接字的超时时间，与插件的超时时间保持一致
        sock.settimeout(15.0) 
        
        try:
            while True:
                try:
                    # 接收数据块
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # 如果接收到空数据块，可能连接已关闭
                        if not chunks:  # 如果尚未接收到任何数据，则视为错误
                            raise Exception("Connection closed before receiving any data")
                        break # 否则，退出循环
                    
                    # 将接收到的数据块添加到列表中
                    chunks.append(chunk)
                    
                    # 检查是否已经接收到完整的 JSON 对象
                    try:
                        # 合并所有数据块
                        data = b''.join(chunks)
                        # 尝试解析 JSON
                        json.loads(data.decode('utf-8'))
                        # 如果解析成功，则返回完整的数据
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # 如果解析失败，说明数据不完整，继续接收
                        continue
                except socket.timeout:
                    # 如果接收超时，则退出循环并尝试使用已接收的数据
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # 重新抛出异常以供调用者处理
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise # 重新抛出异常以供调用者处理
            
        # 如果到达这里，说明我们超时或提前退出了循环
        # 尝试使用已接收的数据
        if chunks:
            # 合并所有数据块
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # 尝试解析已接收的数据
                json.loads(data.decode('utf-8'))
                # 如果解析成功，则返回数据
                return data
            except json.JSONDecodeError:
                # 如果解析失败，则数据不完整
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response"""
        """
        向 Blender 发送命令并返回响应。

        参数:
            command_type (str): 命令类型。
            params (dict, 可选): 命令参数，默认为空字典。

        返回:
            dict: 命令的响应结果。

        异常:
            ConnectionError: 如果未连接到 Blender，则抛出异常。
            Exception: 其他与通信相关的异常。
        """
        if not self.sock and not self.connect():
            # 如果未连接，则抛出连接错误
            raise ConnectionError("Not connected to Blender")
        
        command = {
            "type": command_type,  # 命令类型
            "params": params or {} # 命令参数，默认为空字典
        }
        
        try:
            # 记录发送的命令
            logger.info(f"Sending command: {command_type} with params: {params}")
            
            # 发送命令到 Blender
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # 设置接收响应的超时时间
            self.sock.settimeout(15.0)  # 与插件的超时时间保持一致
            
            # 使用改进的 receive_full_response 方法接收响应
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            
            # 解析响应的 JSON 数据
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")
            
            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Blender"))
            
            # 返回响应的结果部分
            return response.get("result", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Blender")
            # 不在这里尝试重新连接 - 让 get_blender_connection 处理重新连接
            # 只是使当前套接字无效，以便下次使用时会重新创建
            self.sock = None
            raise Exception("Timeout waiting for Blender response - try simplifying your request")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Blender lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Blender: {str(e)}")
            # 尝试记录接收到的内容
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Blender: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Blender: {str(e)}")
            # 不在这里尝试重新连接 - 让 get_blender_connection 处理重新连接
            self.sock = None
            raise Exception(f"Communication error with Blender: {str(e)}")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """
    管理服务器的生命周期，包括启动和关闭。

    参数:
        server (FastMCP): FastMCP 服务器实例。

    异步上下文管理器:
        返回一个空的字典，因为资源管理使用全局连接。
    """
    try:
        # 记录服务器启动日志
        logger.info("BlenderMCP server starting up")
        
        # 在启动时尝试连接到 Blender 以验证其可用性
        try:
            # 获取或创建与 Blender 的连接，这将初始化全局连接（如果尚未初始化）
            blender = get_blender_connection()
            logger.info("Successfully connected to Blender on startup")
        except Exception as e:
            # 如果连接失败，记录警告信息
            logger.warning(f"Could not connect to Blender on startup: {str(e)}")
            logger.warning("Make sure the Blender addon is running before using Blender resources or tools")
        
        # 返回一个空的上下文，因为资源管理使用全局连接
        yield {}
    finally:
        # 在服务器关闭时清理全局连接
        global _blender_connection
        if _blender_connection:
            logger.info("Disconnecting from Blender on shutdown")
            # 断开与 Blender 的连接
            _blender_connection.disconnect() 
            # 重置连接对象为 None
            _blender_connection = None
        logger.info("BlenderMCP server shut down")


# 创建 FastMCP 服务器实例，并启用生命周期管理
mcp = FastMCP(
    "BlenderMCP",  # 服务器名称
    description="Blender integration through the Model Context Protocol",  # 服务器描述
    lifespan=server_lifespan  # 生命周期管理函数
)


# 资源端点

# 全局连接用于资源管理（因为资源无法访问上下文）
# 全局变量用于存储与 Blender 的连接
_blender_connection = None
_polyhaven_enabled = False  # Add this global variable


def get_blender_connection():
    """Get or create a persistent Blender connection"""
    """
    获取或创建一个持久的 Blender 连接。

    返回:
        BlenderConnection: 与 Blender 的连接对象。

    异常:
        Exception: 如果无法连接到 Blender，则抛出异常。
    """
    # 声明全局变量
    global _blender_connection, _polyhaven_enabled  
    
    # 如果已有连接，检查其是否仍然有效
    if _blender_connection is not None:
        try:
            # 通过发送获取 PolyHaven 状态的命令来检查连接是否有效
            result = _blender_connection.send_command("get_polyhaven_status")
            # 将 PolyHaven 的状态存储在全局变量中
            _polyhaven_enabled = result.get("enabled", False)
            # 返回现有连接
            return _blender_connection
        except Exception as e:
            # 如果连接无效，记录警告信息
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                # 断开现有连接
                _blender_connection.disconnect()
            except:
                # 如果断开连接时发生异常，则忽略
                pass
            # 重置连接对象为 None
            _blender_connection = None
    
    # 如果没有有效连接，则创建一个新的连接
    if _blender_connection is None:
        # 创建新的连接对象
        _blender_connection = BlenderConnection(host="localhost", port=9876)
        # 尝试连接到 Blender
        if not _blender_connection.connect():
            # 如果连接失败，记录错误日志
            logger.error("Failed to connect to Blender")
            # 重置连接对象为 None
            _blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        # 如果连接成功，记录信息日志
        logger.info("Created new persistent connection to Blender")
    
    # 返回连接对象
    return _blender_connection


@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    """Get detailed information about the current Blender scene"""
    """
    获取当前 Blender 场景的详细信息。

    参数:
        ctx (Context): FastMCP 上下文对象。

    返回:
        str: 包含场景信息的 JSON 字符串。
    """
    try:
        # 获取 Blender 连接
        blender = get_blender_connection()
        # 发送获取场景信息的命令
        result = blender.send_command("get_scene_info")
        
        # 将 Blender 返回的结果转换为格式化的 JSON 字符串并返回
        return json.dumps(result, indent=2)
    except Exception as e:
        # 如果发生异常，记录错误日志并返回错误信息
        logger.error(f"Error getting scene info from Blender: {str(e)}")
        return f"Error getting scene info: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    """
    获取 Blender 场景中特定对象的详细信息。

    参数:
        ctx (Context): FastMCP 上下文对象。
        object_name (str): 要获取信息的对象的名称。

    返回:
        str: 包含对象信息的 JSON 字符串。
    """
    try:
        # 获取 Blender 连接
        blender = get_blender_connection()
        # 发送获取对象信息的命令，并传递对象名称作为参数
        result = blender.send_command("get_object_info", {"name": object_name})
        
        # 将 Blender 返回的结果转换为格式化的 JSON 字符串并返回
        return json.dumps(result, indent=2)
    except Exception as e:
        # 如果发生异常，记录错误日志并返回错误信息
        logger.error(f"Error getting object info from Blender: {str(e)}")
        return f"Error getting object info: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def create_object(
    ctx: Context,
    type: str = "CUBE",
    name: str = None,
    location: List[float] = None,
    rotation: List[float] = None,
    scale: List[float] = None
) -> str:
    """
    在 Blender 场景中创建一个新对象。

    参数:
        ctx (Context): FastMCP 上下文对象。
        type (str): 对象类型（"CUBE", "SPHERE", "CYLINDER", "PLANE", "CONE", "TORUS", "EMPTY", "CAMERA", "LIGHT"）。
        name (str, 可选): 对象的可选名称。
        location (List[float], 可选): 可选的位置坐标列表 [x, y, z]。
        rotation (List[float], 可选): 可选的旋转角度列表 [x, y, z]，单位为弧度。
        scale (List[float], 可选): 可选的缩放因子列表 [x, y, z]。

    返回:
        str: 创建对象的成功消息或错误信息。
    """
    try:
        # 获取全局的 Blender 连接
        blender = get_blender_connection()
        
        # 设置缺失参数的默认值
        # 如果未提供位置，则默认为 [0, 0, 0]
        loc = location or [0, 0, 0]
        # 如果未提供旋转，则默认为 [0, 0, 0]
        rot = rotation or [0, 0, 0]
        # 如果未提供缩放，则默认为 [1, 1, 1]
        sc = scale or [1, 1, 1]
        
        params = {
            "type": type,     # 对象类型
            "location": loc,  # 位置坐标
            "rotation": rot,  # 旋转角度
            "scale": sc       # 缩放因子
        }
        
        if name:
            # 如果提供了名称，则添加到参数中
            params["name"] = name
        
        # 发送创建对象的命令到 Blender
        result = blender.send_command("create_object", params)
        # 返回创建成功的消息，包含对象名称
        return f"Created {type} object: {result['name']}"
    except Exception as e:
        # 如果发生异常，记录错误日志并返回错误信息
        logger.error(f"Error creating object: {str(e)}")
        return f"Error creating object: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def modify_object(
    ctx: Context,
    name: str,
    location: List[float] = None,
    rotation: List[float] = None,
    scale: List[float] = None,
    visible: bool = None
) -> str:
    """
    修改 Blender 场景中现有的对象。

    参数:
        ctx (Context): FastMCP 上下文对象。
        name (str): 要修改的对象名称。
        location (List[float], 可选): 可选的位置坐标列表 [x, y, z]。
        rotation (List[float], 可选): 可选的旋转角度列表 [x, y, z]，单位为弧度。
        scale (List[float], 可选): 可选的缩放因子列表 [x, y, z]。
        visible (bool, 可选): 可选的可见性标志。

    返回:
        str: 修改对象的成功消息或错误信息。
    """
    try:
        # 获取全局的 Blender 连接
        blender = get_blender_connection()
        
        # 初始化参数字典，包含对象名称
        params = {"name": name}
        
        if location is not None:
            # 如果提供了位置，则添加到参数中
            params["location"] = location
        if rotation is not None:
            # 如果提供了旋转，则添加到参数中
            params["rotation"] = rotation
        if scale is not None:
            # 如果提供了缩放，则添加到参数中
            params["scale"] = scale
        if visible is not None:
            # 如果提供了可见性标志，则添加到参数中
            params["visible"] = visible
        
        # 发送修改对象的命令到 Blender
        result = blender.send_command("modify_object", params)
        # 返回修改成功的消息，包含对象名称
        return f"Modified object: {result['name']}"
    except Exception as e:
        # 如果发生异常，记录错误日志并返回错误信息
        logger.error(f"Error modifying object: {str(e)}")
        return f"Error modifying object: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def delete_object(ctx: Context, name: str) -> str:
    """
    从 Blender 场景中删除一个对象。

    参数:
        ctx (Context): FastMCP 上下文对象。
        name (str): 要删除的对象名称。

    返回:
        str: 删除对象的成功消息或错误信息。
    """
    try:
        # 获取全局的 Blender 连接
        blender = get_blender_connection()
        
        # 发送删除对象的命令到 Blender，传递对象名称作为参数
        result = blender.send_command("delete_object", {"name": name})
        # 返回删除成功的消息，包含对象名称
        return f"Deleted object: {name}"
    except Exception as e:
        # 如果发生异常，记录错误日志并返回错误信息
        logger.error(f"Error deleting object: {str(e)}")
        return f"Error deleting object: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def set_material(
    ctx: Context,
    object_name: str,
    material_name: str = None,
    color: List[float] = None
) -> str:
    """
    为对象设置或创建材质。

    参数:
        ctx (Context): FastMCP 上下文对象。
        object_name (str): 要应用材质的对象名称。
        material_name (str, 可选): 要使用或创建的材质名称。
        color (List[float], 可选): 可选的 [R, G, B] 颜色值，范围为 0.0-1.0。

    返回:
        str: 应用材质的成功消息或错误信息。
    """
    try:
        # 获取全局的 Blender 连接
        blender = get_blender_connection()
        
        # 初始化参数字典，包含对象名称
        params = {"object_name": object_name}
        
        if material_name:
            # 如果提供了材质名称，则添加到参数中
            params["material_name"] = material_name
        if color:
            # 如果提供了颜色，则添加到参数中
            params["color"] = color
        
        # 发送设置材质的命令到 Blender
        result = blender.send_command("set_material", params)
        # 返回应用材质的成功消息，包含材质名称
        return f"Applied material to {object_name}: {result.get('material_name', 'unknown')}"
    except Exception as e:
        # 如果发生异常，记录错误日志并返回错误信息
        logger.error(f"Error setting material: {str(e)}")
        return f"Error setting material: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def execute_blender_code(ctx: Context, code: str) -> str:
    """
    在 Blender 中执行任意 Python 代码。

    参数:
        ctx (Context): FastMCP 上下文对象。
        code (str): 要执行的 Python 代码。

    返回:
        str: 执行代码的成功消息或错误信息。
    """
    try:
        # 获取全局的 Blender 连接
        blender = get_blender_connection()
        
        # 发送执行代码的命令到 Blender，传递代码作为参数
        result = blender.send_command("execute_code", {"code": code})
        # 返回执行成功的消息，包含代码执行结果
        return f"Code executed successfully: {result.get('result', '')}"
    except Exception as e:
        # 如果发生异常，记录错误日志并返回错误信息
        logger.error(f"Error executing code: {str(e)}")
        return f"Error executing code: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def get_polyhaven_categories(ctx: Context, asset_type: str = "hdris") -> str:
    """
    获取 PolyHaven 上特定资产类型的分类列表。

    参数:
        ctx (Context): FastMCP 上下文对象。
        asset_type (str, 可选): 要获取分类的资产类型（"hdris", "textures", "models", "all"），默认为 "hdris"。

    返回:
        str: 分类列表的格式化字符串或错误信息。
    """
    try:
        # 获取 Blender 连接
        blender = get_blender_connection()
        if not _polyhaven_enabled:
            # 如果 PolyHaven 未启用，返回提示信息
            return "PolyHaven integration is disabled. Select it in the sidebar in BlenderMCP, then run it again."
        # 发送获取分类的命令到 Blender，传递资产类型作为参数
        result = blender.send_command("get_polyhaven_categories", {"asset_type": asset_type})
        
        if "error" in result:
            # 如果返回结果中包含错误信息，则返回错误信息
            return f"Error: {result['error']}"
        
        # 格式化分类列表，使其更易读
        categories = result["categories"]
        formatted_output = f"Categories for {asset_type}:\n\n"
        
        # 按分类中的资产数量降序排序
        sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
        
        for category, count in sorted_categories:
            # 添加每个分类及其资产数量
            formatted_output += f"- {category}: {count} assets\n"
        
        # 返回格式化后的分类列表
        return formatted_output
    
    except Exception as e:
        # 如果发生异常，记录错误日志并返回错误信息
        logger.error(f"Error getting Polyhaven categories: {str(e)}")
        return f"Error getting Polyhaven categories: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def search_polyhaven_assets(
    ctx: Context,
    asset_type: str = "all",
    categories: str = None
) -> str:
    """
    在 PolyHaven 上搜索资产，并可选择进行过滤。

    参数:
        ctx (Context): FastMCP 上下文对象。
        asset_type (str, 可选): 要搜索的资产类型（"hdris", "textures", "models", "all"），默认为 "all"。
        categories (str, 可选): 可选的分类过滤条件，以逗号分隔。

    返回:
        str: 搜索结果的格式化字符串或错误信息。
    """
    try:
        # 获取 Blender 连接
        blender = get_blender_connection()
        # 发送搜索资产命令到 Blender，传递资产类型和分类过滤条件作为参数
        result = blender.send_command("search_polyhaven_assets", {
            "asset_type": asset_type,
            "categories": categories
        })
        
        if "error" in result:
            # 如果返回结果中包含错误信息，则返回错误信息
            return f"Error: {result['error']}"
        
        # 格式化资产列表，使其更易读
        assets = result["assets"]
        total_count = result["total_count"]
        returned_count = result["returned_count"]
        
        # 记录找到的资产总数
        formatted_output = f"Found {total_count} assets"
        if categories:
            # 如果提供了分类过滤条件，则添加分类信息
            formatted_output += f" in categories: {categories}"
        formatted_output += f"\nShowing {returned_count} assets:\n\n"
        
        # 按下载数量降序排序资产（按受欢迎程度）
        sorted_assets = sorted(assets.items(), key=lambda x: x[1].get("download_count", 0), reverse=True)
        
        for asset_id, asset_data in sorted_assets:
            # 添加资产名称和 ID
            formatted_output += f"- {asset_data.get('name', asset_id)} (ID: {asset_id})\n"
            # 添加资产类型
            formatted_output += f"  Type: {['HDRI', 'Texture', 'Model'][asset_data.get('type', 0)]}\n"
            # 添加分类信息
            formatted_output += f"  Categories: {', '.join(asset_data.get('categories', []))}\n"
            # 添加下载数量
            formatted_output += f"  Downloads: {asset_data.get('download_count', 'Unknown')}\n\n"
        
        # 返回格式化后的资产列表
        return formatted_output
    
    except Exception as e:
        # 如果发生异常，记录错误日志并返回错误信息
        logger.error(f"Error searching Polyhaven assets: {str(e)}")
        return f"Error searching Polyhaven assets: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def download_polyhaven_asset(
    ctx: Context,
    asset_id: str,
    asset_type: str,
    resolution: str = "1k",
    file_format: str = None
) -> str:
    """
    下载并导入 PolyHaven 资产到 Blender。

    参数:
        ctx (Context): FastMCP 上下文对象。
        asset_id (str): 要下载的资产 ID。
        asset_type (str): 资产类型（"hdris", "textures", "models"）。
        resolution (str, 可选): 下载的分辨率（例如，"1k", "2k", "4k"），默认为 "1k"。
        file_format (str, 可选): 可选的文件格式（例如，hdr, exr 用于 HDRIs；jpg, png 用于纹理；gltf, fbx 用于模型）。

    返回:
        str: 指示成功或失败的消息。
    """
    try:
        # 获取 Blender 连接
        blender = get_blender_connection()
        # 发送下载资产的命令到 Blender，传递资产 ID、类型、分辨率和文件格式作为参数
        result = blender.send_command("download_polyhaven_asset", {
            "asset_id": asset_id,
            "asset_type": asset_type,
            "resolution": resolution,
            "file_format": file_format
        })
        
        if "error" in result:
            # 如果返回结果中包含错误信息，则返回错误信息
            return f"Error: {result['error']}"
        
        if result.get("success"):
            # 获取成功消息
            message = result.get("message", "Asset downloaded and imported successfully")
            
            # 根据资产类型添加额外的信息
            if asset_type == "hdris":
                return f"{message}. The HDRI has been set as the world environment."
            elif asset_type == "textures":
                # 获取材质名称
                material_name = result.get("material", "")
                # 获取纹理贴图列表
                maps = ", ".join(result.get("maps", []))
                return f"{message}. Created material '{material_name}' with maps: {maps}."
            elif asset_type == "models":
                return f"{message}. The model has been imported into the current scene."
            else:
                return message
        else:
            # 返回下载失败的消息
            return f"Failed to download asset: {result.get('message', 'Unknown error')}"
    except Exception as e:
        # 记录错误日志
        logger.error(f"Error downloading Polyhaven asset: {str(e)}")
        # 返回错误信息
        return f"Error downloading Polyhaven asset: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def set_texture(
    ctx: Context,
    object_name: str,
    texture_id: str
) -> str:
    """
    将之前下载的 PolyHaven 纹理应用到对象上。

    参数:
        ctx (Context): FastMCP 上下文对象。
        object_name (str): 要应用纹理的对象名称。
        texture_id (str): 要应用的 PolyHaven 纹理 ID（必须先下载）。

    返回:
        str: 指示成功或失败的消息。
    """
    try:
        # 获取全局连接
        blender = get_blender_connection()
        
        # 发送设置纹理的命令到 Blender，传递对象名称和纹理 ID 作为参数
        result = blender.send_command("set_texture", {
            "object_name": object_name,
            "texture_id": texture_id
        })
        
        if "error" in result:
            # 如果返回结果中包含错误信息，则返回错误信息
            return f"Error: {result['error']}"
        
        if result.get("success"):
            # 获取材质名称
            material_name = result.get("material", "")
            # 获取纹理贴图列表
            maps = ", ".join(result.get("maps", []))
            
            # 添加详细的材质信息
            # 获取材质信息
            material_info = result.get("material_info", {})
            # 获取节点数量
            node_count = material_info.get("node_count", 0)
            # 材质是否有节点
            has_nodes = material_info.get("has_nodes", False)
            # 获取纹理节点列表
            texture_nodes = material_info.get("texture_nodes", [])
            
            # 构建输出字符串
            output = f"Successfully applied texture '{texture_id}' to {object_name}.\n"
            # 添加材质信息
            output += f"Using material '{material_name}' with maps: {maps}.\n\n"
            # 添加节点信息
            output += f"Material has nodes: {has_nodes}\n"
            # 添加节点数量
            output += f"Total node count: {node_count}\n\n"
            
            if texture_nodes:
                # 如果有纹理节点，添加节点信息
                output += "Texture nodes:\n"
                for node in texture_nodes:
                    # 添加节点名称和图像信息
                    output += f"- {node['name']} using image: {node['image']}\n"
                    if node['connections']:
                        # 如果有连接，添加连接信息
                        output += "  Connections:\n"
                        for conn in node['connections']:
                            # 添加连接细节
                            output += f"    {conn}\n"
            else:
                # 如果没有纹理节点，添加信息
                output += "No texture nodes found in the material.\n"
            
            # 返回输出字符串
            return output
        else:
            # 返回应用纹理失败的消息
            return f"Failed to apply texture: {result.get('message', 'Unknown error')}"
    except Exception as e:
        # 记录错误日志
        logger.error(f"Error applying texture: {str(e)}")
        # 返回错误信息
        return f"Error applying texture: {str(e)}"


# 将函数注册为工具端点
@mcp.tool()
def get_polyhaven_status(ctx: Context) -> str:
    """
    检查 Blender 中是否启用了 PolyHaven 集成。
    返回指示 PolyHaven 功能是否可用的消息。
    """
    try:
        # 获取 Blender 连接
        blender = get_blender_connection()
        # 发送获取 PolyHaven 状态命令
        result = blender.send_command("get_polyhaven_status")
        # 获取启用状态
        enabled = result.get("enabled", False)
        # 获取消息
        message = result.get("message", "")
        # 返回消息
        return message
    except Exception as e:
        # 记录错误日志
        logger.error(f"Error checking PolyHaven status: {str(e)}")
        # 返回错误信息
        return f"Error checking PolyHaven status: {str(e)}"


# 将函数注册为提示端点
@mcp.prompt()
def asset_creation_strategy() -> str:
    """
    定义在 Blender 中创建资产的优先策略。
    """
    return """When creating 3D content in Blender, always start by checking if PolyHaven is available:

    0. Before anything, always check the scene from get_scene_info()
    1. First use get_polyhaven_status() to verify if PolyHaven integration is enabled.

    2. If PolyHaven is enabled:
       - For objects/models: Use download_polyhaven_asset() with asset_type="models"
       - For materials/textures: Use download_polyhaven_asset() with asset_type="textures"
       - For environment lighting: Use download_polyhaven_asset() with asset_type="hdris"

    3. If PolyHaven is disabled or when falling back to basic tools:
       - create_object() for basic primitives (CUBE, SPHERE, CYLINDER, etc.)
       - set_material() for basic colors and materials

    Only fall back to basic creation tools when:
    - PolyHaven is disabled
    - A simple primitive is explicitly requested
    - No suitable PolyHaven asset exists
    - The task specifically requires a basic material/color
    """


def main():
    """
    Run the MCP server
    运行 MCP 服务器
    """
    mcp.run()


if __name__ == "__main__":
    main()
