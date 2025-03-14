import json
import threading
import socket
import time
import traceback
import os
import shutil
import requests  
import tempfile  

import bpy
from bpy.props import StringProperty, IntProperty


# Blender插件的元数据
bl_info = {
    "name": "Blender MCP",
    "author": "BlenderMCP",
    "version": (0, 1),
    "blender": (3, 0, 0),  # 兼容的Blender版本
    "location": "View3D > Sidebar > BlenderMCP",  # 插件在Blender界面中的位置
    "description": "Connect Blender to Claude via MCP",
    "category": "Interface",  # 插件类别
}


class BlenderMCPServer:
    """
    BlenderMCPServer 类用于创建一个服务器，监听来自客户端的连接并处理命令。
    """
    def __init__(self, host='localhost', port=9876):
        """
        初始化 BlenderMCPServer 实例。

        参数:
            host (str, 可选): 服务器绑定的主机名或 IP 地址，默认为 'localhost'。
            port (int, 可选): 服务器监听的端口号，默认为 9876。
        """
        # 服务器主机名或 IP 地址
        self.host = host
        # 服务器端口号
        self.port = port
        # 服务器运行状态标志
        self.running = False
        # 服务器套接字对象
        self.socket = None
        # 客户端套接字对象
        self.client = None
        # 命令队列，用于存储待处理的命令
        self.command_queue = []
        # 缓冲区，用于存储不完整的接收数据
        self.buffer = b'' 
    
    def start(self):
        """
        启动服务器。
        """
        # 设置服务器运行状态为 True
        self.running = True
        # 创建一个 TCP 套接字
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # 设置套接字选项，允许地址重用
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            # 绑定套接字到指定的主机和端口
            self.socket.bind((self.host, self.port))
            # 开始监听，允许最多一个挂起的连接
            self.socket.listen(1)
            # 将套接字设置为非阻塞模式
            self.socket.setblocking(False)
            # 注册一个定时器回调函数，用于处理服务器操作
            bpy.app.timers.register(self._process_server, persistent=True)
            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
            # 如果绑定或监听失败，打印错误信息
            print(f"Failed to start server: {str(e)}")
            # 调用 stop 方法停止服务器
            self.stop()
            
    def stop(self):
        """
        停止服务器。
        """
        # 设置服务器运行状态为 False
        self.running = False
        # 检查 timers 模块是否有 unregister 方法
        if hasattr(bpy.app.timers, "unregister"):
            # 如果定时器已注册，则注销
            if bpy.app.timers.is_registered(self._process_server):
                bpy.app.timers.unregister(self._process_server)
        if self.socket:
            # 关闭服务器套接字
            self.socket.close()
        if self.client:
            # 关闭客户端套接字
            self.client.close()
        # 重置服务器套接字为 None
        self.socket = None
        # 重置客户端套接字为 None
        self.client = None
        # 打印服务器停止信息
        print("BlenderMCP server stopped")

    def _process_server(self):
        """
        定时器回调函数，用于处理服务器操作。

        返回:
            float: 下次调用的时间间隔（秒）。
        """
        if not self.running:
            return None  # 如果服务器未运行，则取消定时器注册
            
        try:
            # 接受新的客户端连接
            if not self.client and self.socket:
                try:
                    # 接受连接
                    self.client, address = self.socket.accept()
                    # 将客户端套接字设置为非阻塞模式
                    self.client.setblocking(False)
                    print(f"Connected to client: {address}")
                except BlockingIOError:
                    pass  # 没有连接等待
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")
                
            # 处理现有连接
            if self.client:
                try:
                    # 尝试接收数据
                    try:
                        # 接收最多 8192 字节的数据
                        data = self.client.recv(8192)
                        if data:
                            # 将接收到的数据添加到缓冲区
                            self.buffer += data
                            # 尝试处理完整的消息
                            try:
                                # 尝试将缓冲区内容解析为 JSON
                                command = json.loads(self.buffer.decode('utf-8'))
                                # 如果解析成功，清空缓冲区并处理命令
                                self.buffer = b''
                                # 执行命令
                                response = self.execute_command(command)
                                # 将响应转换为 JSON 字符串
                                response_json = json.dumps(response)
                                # 发送响应回客户端
                                self.client.sendall(response_json.encode('utf-8'))
                            except json.JSONDecodeError:
                                # 如果解析失败，说明数据不完整，保持在缓冲区
                                pass
                        else:
                            # 如果接收到空数据，说明客户端已断开连接
                            print("Client disconnected")
                            self.client.close()
                            self.client = None
                            self.buffer = b''
                    except BlockingIOError:
                        pass  # 没有数据可接收
                    except Exception as e:
                        # 打印错误信息
                        print(f"Error receiving data: {str(e)}")
                        self.client.close()
                        self.client = None
                        self.buffer = b''
                        
                except Exception as e:
                    # 打印客户端错误信息
                    print(f"Error with client: {str(e)}")
                    if self.client:
                        self.client.close()
                        self.client = None
                    self.buffer = b''
                    
        except Exception as e:
            # 打印服务器错误信息
            print(f"Server error: {str(e)}")
            
        return 0.1  # 继续定时器调用，每次间隔 0.1 秒

    def execute_command(self, command):
        """
        在主 Blender 线程中执行命令。

        参数:
            command (dict): 包含命令类型和参数的字典。

        返回:
            dict: 命令执行的响应结果。
        """
        try:
            # 获取命令类型
            cmd_type = command.get("type")
            # 获取命令参数，默认为空字典
            params = command.get("params", {})
            
            # 确保在正确的上下文中执行命令
            if cmd_type in ["create_object", "modify_object", "delete_object"]:
                # 复制当前上下文
                override = bpy.context.copy()
                # 找到类型为 'VIEW_3D' 的区域，并设置上下文
                override['area'] = [area for area in bpy.context.screen.areas if area.type == 'VIEW_3D'][0]
                with bpy.context.temp_override(**override):
                    # 执行内部命令
                    return self._execute_command_internal(command)
            else:
                # 执行内部命令
                return self._execute_command_internal(command)
                
        except Exception as e:
            # 打印错误信息
            print(f"Error executing command: {str(e)}")
            # 打印完整的错误堆栈跟踪
            traceback.print_exc()
            # 返回错误响应
            return {"status": "error", "message": str(e)}

    def _execute_command_internal(self, command):
        """
        在适当的上下文中执行内部命令。

        参数:
            command (dict): 包含命令类型和参数的字典。

        返回:
            dict: 命令执行的响应结果。
        """
        # 获取命令类型
        cmd_type = command.get("type")
        # 获取命令参数，默认为空字典
        params = command.get("params", {})

        # 添加用于检查 PolyHaven 状态的处理程序
        if cmd_type == "get_polyhaven_status":
            # 返回 PolyHaven 状态
            return {"status": "success", "result": self.get_polyhaven_status()}
        
        # 基础处理程序，始终可用
        handlers = {
            "get_scene_info": self.get_scene_info,     # 获取场景信息
            "create_object": self.create_object,       # 创建对象
            "modify_object": self.modify_object,       # 修改对象
            "delete_object": self.delete_object,       # 删除对象
            "get_object_info": self.get_object_info,   # 获取对象信息
            "execute_code": self.execute_code,         # 执行代码
            "set_material": self.set_material,         # 设置材质
            "get_polyhaven_status": self.get_polyhaven_status,  # 获取 PolyHaven 状态
        }
        
        # 只有在启用 PolyHaven 时添加 PolyHaven 处理程序
        if bpy.context.scene.blendermcp_use_polyhaven:
            polyhaven_handlers = {
                "get_polyhaven_categories": self.get_polyhaven_categories, # 获取 PolyHaven 分类
                "search_polyhaven_assets": self.search_polyhaven_assets, # 搜索 PolyHaven 资产
                "download_polyhaven_asset": self.download_polyhaven_asset, # 下载 PolyHaven 资产
                "set_texture": self.set_texture, # 设置纹理
            }
            # 更新处理程序字典
            handlers.update(polyhaven_handlers)
        
        # 获取对应的处理程序
        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                # 执行处理程序
                result = handler(**params)
                print(f"Handler execution complete")
                # 返回成功响应
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                # 打印完整的错误堆栈跟踪
                traceback.print_exc()
                # 返回错误响应
                return {"status": "error", "message": str(e)}
        else:
            # 返回未知命令类型的错误响应
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

    def get_simple_info(self):
        """
        获取基本的 Blender 信息。

        返回:
            dict: 包含 Blender 版本、场景名称和对象数量的字典。
        """
        return {
            "blender_version": ".".join(str(v) for v in bpy.app.version), # Blender 版本
            "scene_name": bpy.context.scene.name, # 场景名称
            "object_count": len(bpy.context.scene.objects) # 对象数量
        }
    
    def get_scene_info(self):
        """
        获取当前 Blender 场景的信息。

        返回:
            dict: 包含场景名称、对象数量、对象列表和材质数量。
        """
        try:
            print("Getting scene info...")
            # 简化场景信息以减少数据大小
            scene_info = {
                "name": bpy.context.scene.name,  # 场景名称
                "object_count": len(bpy.context.scene.objects),  # 对象数量
                "objects": [],  # 对象列表
                "materials_count": len(bpy.data.materials),  # 材质数量
            }
            
            # 收集最小的对象信息（限制为前 10 个对象）
            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:  # 从 20 个减少到 10 个
                    break
                    
                obj_info = {
                    "name": obj.name, # 对象名称
                    "type": obj.type, # 对象类型
                    # 仅包括基本的定位数据
                    "location": [round(float(obj.location.x), 2), 
                                round(float(obj.location.y), 2), 
                                round(float(obj.location.z), 2)],
                }
                scene_info["objects"].append(obj_info)
            
            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            # 返回场景信息
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            # 打印完整的错误堆栈跟踪
            traceback.print_exc()
            # 返回错误信息
            return {"error": str(e)}
    
    def create_object(self, type="CUBE", name=None, location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1)):
        """
        在场景中创建一个新对象。

        参数:
            type (str): 对象类型（默认为 "CUBE"）。
            name (str, 可选): 对象名称。
            location (tuple, 可选): 对象位置（默认为 (0, 0, 0)）。
            rotation (tuple, 可选): 对象旋转（默认为 (0, 0, 0)）。
            scale (tuple, 可选): 对象缩放（默认为 (1, 1, 1)）。

        返回:
            dict: 包含对象名称、类型、位置、旋转和缩放的字典。
        """
        # 取消选择所有对象
        bpy.ops.object.select_all(action='DESELECT')
        
        # 根据类型创建对象
        if type == "CUBE":
            bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation, scale=scale)
        elif type == "SPHERE":
            bpy.ops.mesh.primitive_uv_sphere_add(location=location, rotation=rotation, scale=scale)
        elif type == "CYLINDER":
            bpy.ops.mesh.primitive_cylinder_add(location=location, rotation=rotation, scale=scale)
        elif type == "PLANE":
            bpy.ops.mesh.primitive_plane_add(location=location, rotation=rotation, scale=scale)
        elif type == "CONE":
            bpy.ops.mesh.primitive_cone_add(location=location, rotation=rotation, scale=scale)
        elif type == "TORUS":
            bpy.ops.mesh.primitive_torus_add(location=location, rotation=rotation, scale=scale)
        elif type == "EMPTY":
            bpy.ops.object.empty_add(location=location, rotation=rotation, scale=scale)
        elif type == "CAMERA":
            bpy.ops.object.camera_add(location=location, rotation=rotation)
        elif type == "LIGHT":
            bpy.ops.object.light_add(type='POINT', location=location, rotation=rotation, scale=scale)
        else:
            raise ValueError(f"Unsupported object type: {type}")
        
        # 获取创建的对象
        obj = bpy.context.active_object
        
        # 如果提供了名称，则重命名对象
        if name:
            obj.name = name
        
        return {
            "name": obj.name, # 对象名称
            "type": obj.type, # 对象类型
            "location": [obj.location.x, obj.location.y, obj.location.z], # 对象位置
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z], # 对象旋转
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z], # 对象缩放
        }
    
    def modify_object(self, name, location=None, rotation=None, scale=None, visible=None):
        """
        修改场景中现有的对象。

        参数:
            name (str): 对象名称。
            location (tuple, 可选): 对象位置。
            rotation (tuple, 可选): 对象旋转。
            scale (tuple, 可选): 对象缩放。
            visible (bool, 可选): 对象可见性。

        返回:
            dict: 包含对象名称、类型、位置、旋转、缩放和可见性的字典。
        """
        # 查找对象
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        
        # 修改属性
        if location is not None:
            obj.location = location
        
        if rotation is not None:
            obj.rotation_euler = rotation
        
        if scale is not None:
            obj.scale = scale
        
        if visible is not None:
            obj.hide_viewport = not visible
            obj.hide_render = not visible
        
        return {
            "name": obj.name, # 对象名称
            "type": obj.type, # 对象类型
            "location": [obj.location.x, obj.location.y, obj.location.z], # 对象位置
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z], # 对象旋转
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z], # 对象缩放
            "visible": obj.visible_get(), # 对象可见性
        }
    
    def delete_object(self, name):
        """
        从场景中删除指定名称的对象。

        参数:
            name (str): 要删除的对象名称。

        返回:
            dict: 包含被删除对象名称的字典。

        异常:
            ValueError: 如果指定名称的对象未找到，则抛出异常。
        """
        # 从 Blender 数据中获取对象
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        
         # 存储对象名称以便返回
        obj_name = obj.name
        
        # 取消选择所有对象，然后选择要删除的对象
        bpy.ops.object.select_all(action='DESELECT')
        # 选择对象
        obj.select_set(True)
        # 删除对象
        bpy.ops.object.delete()
        
        # 返回被删除对象的信息
        return {"deleted": obj_name}
    
    def get_object_info(self, name):
        """
        获取指定对象的详细信息。

        参数:
            name (str): 要获取信息的对象名称。

        返回:
            dict: 包含对象详细信息的字典。

        异常:
            ValueError: 如果指定名称的对象未找到，则抛出异常。
        """
        # 从 Blender 数据中获取对象
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        
        # 初始化对象信息字典
        obj_info = {
            "name": obj.name, # 对象名称
            "type": obj.type, # 对象类型（例如，MESH, CAMERA, LIGHT 等）
            "location": [obj.location.x, obj.location.y, obj.location.z], # 对象位置
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z], # 对象旋转（欧拉角）
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z], # 对象缩放因子
            "visible": obj.visible_get(), # 对象是否可见
            "materials": [], # 材料列表，初始化为空
        }
        
        # 添加材料槽信息
        for slot in obj.material_slots:
            if slot.material:
                # 如果材料存在，则添加到列表中
                obj_info["materials"].append(slot.material.name)
        
        # 如果对象是网格类型且有数据，则添加网格数据
        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices), # 顶点数量
                "edges": len(mesh.edges), # 边数量
                "polygons": len(mesh.polygons), # 多边形数量
            }
        
        # 返回对象信息
        return obj_info
    
    def execute_code(self, code):
        """
        在 Blender 中执行任意的 Python 代码。

        参数:
            code (str): 要执行的 Python 代码字符串。

        返回:
            dict: 包含执行结果的字典。

        异常:
            Exception: 如果代码执行过程中发生错误，则抛出异常。
        """
        # 这是一个功能强大但潜在危险的操作 - 请谨慎使用
        try:
            # 创建一个用于执行的局部命名空间
            namespace = {"bpy": bpy}
            # 执行代码
            exec(code, namespace)
            # 返回执行成功的消息
            return {"executed": True}
        except Exception as e:
            # 抛出异常，包含错误信息
            raise Exception(f"Code execution error: {str(e)}")
    
    def set_material(self, object_name, material_name=None, create_if_missing=True, color=None):
        """
        为对象设置或创建材质。

        参数:
            object_name (str): 要应用材质的对象名称。
            material_name (str, 可选): 要使用或创建的材质名称。如果未提供，则生成一个默认名称。
            create_if_missing (bool, 可选): 如果为 True，则在材质不存在时创建新材质，默认为 True。
            color (list, 可选): 要设置的材质颜色，格式为 [R, G, B] 或 [R, G, B, A]，范围为 0.0-1.0。

        返回:
            dict: 包含设置材质结果的字典。

        异常:
            ValueError: 如果对象未找到或对象无法接受材质，则抛出异常。
        """
        try:
            # 获取对象
            obj = bpy.data.objects.get(object_name)
            if not obj:
                raise ValueError(f"Object not found: {object_name}")
            
            # 确保对象可以接受材质
            if not hasattr(obj, 'data') or not hasattr(obj.data, 'materials'):
                raise ValueError(f"Object {object_name} cannot accept materials")
            
            # 创建或获取材质
            if material_name:
                # 获取指定名称的材质
                mat = bpy.data.materials.get(material_name)
                if not mat and create_if_missing:
                    # 如果材质不存在且需要创建，则创建新材质
                    mat = bpy.data.materials.new(name=material_name)
                    print(f"Created new material: {material_name}")
            else:
                # 如果未提供材质名称，则生成默认名称
                mat_name = f"{object_name}_material"
                mat = bpy.data.materials.get(mat_name)
                if not mat:
                    # 创建新材质
                    mat = bpy.data.materials.new(name=mat_name)
                material_name = mat_name
                print(f"Using material: {mat_name}")
            
            # 设置材质节点（如果需要）
            if mat:
                if not mat.use_nodes:
                    # 启用材质节点
                    mat.use_nodes = True
                
                # 获取或创建 Principled BSDF 节点
                principled = mat.node_tree.nodes.get('Principled BSDF')
                if not principled:
                    principled = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                    # 获取或创建 Material Output 节点
                    output = mat.node_tree.nodes.get('Material Output')
                    if not output:
                        output = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
                     # 如果尚未链接，则链接节点
                    if not principled.outputs[0].links:
                        mat.node_tree.links.new(principled.outputs[0], output.inputs[0])
                
                # 如果提供了颜色，则设置材质颜色
                if color and len(color) >= 3:
                    principled.inputs['Base Color'].default_value = (
                        color[0],
                        color[1],
                        color[2],
                        1.0 if len(color) < 4 else color[3]
                    )
                    print(f"Set material color to {color}")
            
            # 将材质分配给对象（如果尚未分配）
            if mat:
                if not obj.data.materials:
                    # 如果没有材质，则添加材质
                    obj.data.materials.append(mat)
                else:
                    # 仅修改第一个材质槽
                    obj.data.materials[0] = mat
                
                print(f"Assigned material {mat.name} to object {object_name}")
                
                return {
                    "status": "success",
                    "object": object_name,
                    "material": mat.name,
                    "color": color if color else None
                }
            else:
                raise ValueError(f"Failed to create or find material: {material_name}")
            
        except Exception as e:
            print(f"Error in set_material: {str(e)}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": str(e),
                "object": object_name,
                "material": material_name if 'material_name' in locals() else None
            }
    
    def render_scene(self, output_path=None, resolution_x=None, resolution_y=None):
        """
        渲染当前场景。

        参数:
            output_path (str, 可选): 渲染输出的文件路径。如果提供，渲染结果将保存到该路径。
            resolution_x (int, 可选): 渲染输出的水平分辨率（宽度）。如果提供，将设置场景的分辨率宽度。
            resolution_y (int, 可选): 渲染输出的垂直分辨率（高度）。如果提供，将设置场景的分辨率高度。

        返回:
            dict: 包含渲染结果的字典。
        """
        if resolution_x is not None:
            # 设置渲染的宽度
            bpy.context.scene.render.resolution_x = resolution_x
        
        if resolution_y is not None:
            # 设置渲染的高度
            bpy.context.scene.render.resolution_y = resolution_y
        
        if output_path:
            # 设置渲染输出的文件路径
            bpy.context.scene.render.filepath = output_path
        
        # 执行渲染操作，write_still=True 表示渲染完成后保存图像
        bpy.ops.render.render(write_still=bool(output_path))
        
        # 返回渲染结果的信息
        return {
            "rendered": True,
            "output_path": output_path if output_path else "[not saved]",
            "resolution": [bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y],
        }

    def get_polyhaven_categories(self, asset_type):
        """
        从 Polyhaven 获取特定资产类型的分类列表。

        参数:
            asset_type (str): 资产类型，必须是以下之一：'hdris', 'textures', 'models', 'all'。

        返回:
            dict: 包含分类信息的字典或错误信息。
        """
        try:
            # 检查 asset_type 是否有效
            if asset_type not in ["hdris", "textures", "models", "all"]:
                return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}
            
            # 发送 GET 请求到 Polyhaven API 获取分类列表
            response = requests.get(f"https://api.polyhaven.com/categories/{asset_type}")
            if response.status_code == 200:
                # 如果请求成功，返回分类数据
                return {"categories": response.json()}
            else:
                # 如果请求失败，返回错误信息
                return {"error": f"API request failed with status code {response.status_code}"}
        except Exception as e:
            # 捕获并返回任何异常信息
            return {"error": str(e)}
    
    def search_polyhaven_assets(self, asset_type=None, categories=None):
        """
        从 Polyhaven 搜索资产，并可选择进行过滤。

        参数:
            asset_type (str, 可选): 资产类型，必须是以下之一：'hdris', 'textures', 'models', 'all'。
            categories (str, 可选): 以逗号分隔的分类列表，用于过滤搜索结果。

        返回:
            dict: 包含搜索结果的字典或错误信息。
        """
        try:
            # Polyhaven 的资产搜索 API 端点
            url = "https://api.polyhaven.com/assets"
            # 初始化参数字典
            params = {}
            
            if asset_type and asset_type != "all":
                # 如果提供了 asset_type 并且不是 'all'，则添加到参数中
                if asset_type not in ["hdris", "textures", "models"]:
                    return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}
                # 设置资产类型参数
                params["type"] = asset_type
                
            if categories:
                # 如果提供了分类，则添加到参数中
                params["categories"] = categories
            
            # 发送 GET 请求到 Polyhaven API 进行搜索
            response = requests.get(url, params=params)
            if response.status_code == 200:
                # 如果请求成功，解析 JSON 数据
                assets = response.json()
                # 为了避免 Blender 被大量数据淹没，限制返回的资产数量为 20 个
                limited_assets = {}
                for i, (key, value) in enumerate(assets.items()):
                    if i >= 20:  # 限制到 20 个资产
                        break
                    limited_assets[key] = value
                
                # 返回包含资产信息的字典
                return {"assets": limited_assets, "total_count": len(assets), "returned_count": len(limited_assets)}
            else:
                # 如果请求失败，返回错误信息
                return {"error": f"API request failed with status code {response.status_code}"}
        except Exception as e:
            # 捕获并返回任何异常信息
            return {"error": str(e)}
    
    def download_polyhaven_asset(self, asset_id, asset_type, resolution="1k", file_format=None):
        """
        从 Polyhaven 下载资产并导入到 Blender 中。

        参数:
            asset_id (str): 要下载的资产的 ID。
            asset_type (str): 资产类型，必须是以下之一：'hdris', 'textures', 'models'。
            resolution (str, 可选): 下载的分辨率，默认为 '1k'。
            file_format (str, 可选): 文件格式（例如，hdr, exr 用于 HDRIs；jpg, png 用于纹理；gltf, fbx 用于模型）。如果未提供，将使用默认格式。

        返回:
            dict: 包含下载和导入结果的字典或错误信息。
        """
        try:
            # 首先获取资产的文件信息
            files_response = requests.get(f"https://api.polyhaven.com/files/{asset_id}")
            if files_response.status_code != 200:
                return {"error": f"Failed to get asset files: {files_response.status_code}"}
            
            files_data = files_response.json()
            
            # 处理不同类型的资产
            if asset_type == "hdris":
                # 对于 HDRIs，下载 .hdr 或 .exr 文件
                if not file_format:
                    file_format = "hdr"  # HDRIs 的默认格式为 hdr
                
                # 检查请求的分辨率和格式是否可用
                if "hdri" in files_data and resolution in files_data["hdri"] and file_format in files_data["hdri"][resolution]:
                    file_info = files_data["hdri"][resolution][file_format]
                    file_url = file_info["url"]
                    
                    # 由于 Blender 无法直接从内存中正确加载 HDR 数据，因此需要先保存到临时文件中
                    with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                        # 下载文件
                        response = requests.get(file_url)
                        if response.status_code != 200:
                            return {"error": f"Failed to download HDRI: {response.status_code}"}
                        
                        tmp_file.write(response.content)
                        tmp_path = tmp_file.name
                    
                    try:
                        # 如果场景中没有世界环境，则创建一个新的世界环境
                        if not bpy.data.worlds:
                            bpy.data.worlds.new("World")
                        
                        world = bpy.data.worlds[0]
                        world.use_nodes = True
                        node_tree = world.node_tree
                        
                        # 清除现有的节点
                        for node in node_tree.nodes:
                            node_tree.nodes.remove(node)
                        
                        # 创建节点
                        tex_coord = node_tree.nodes.new(type='ShaderNodeTexCoord')
                        tex_coord.location = (-800, 0)
                        
                        mapping = node_tree.nodes.new(type='ShaderNodeMapping')
                        mapping.location = (-600, 0)
                        
                        # 从临时文件加载图像
                        env_tex = node_tree.nodes.new(type='ShaderNodeTexEnvironment')
                        env_tex.location = (-400, 0)
                        env_tex.image = bpy.data.images.load(tmp_path)
                        
                        # 设置颜色空间
                        if file_format.lower() == 'exr':
                            # 尝试使用 Linear 颜色空间
                            try:
                                env_tex.image.colorspace_settings.name = 'Linear'
                            except:
                                # 如果 Linear 不可用，则回退到 Non-Color
                                env_tex.image.colorspace_settings.name = 'Non-Color'
                        else:  # hdr
                            # 对于 HDR 文件，尝试以下选项按顺序设置颜色空间
                            for color_space in ['Linear', 'Linear Rec.709', 'Non-Color']:
                                try:
                                    env_tex.image.colorspace_settings.name = color_space
                                    break  # 如果成功设置颜色空间，则停止
                                except:
                                    continue
                        
                        background = node_tree.nodes.new(type='ShaderNodeBackground')
                        background.location = (-200, 0)
                        
                        output = node_tree.nodes.new(type='ShaderNodeOutputWorld')
                        output.location = (0, 0)
                        
                        # 连接节点
                        node_tree.links.new(tex_coord.outputs['Generated'], mapping.inputs['Vector'])
                        node_tree.links.new(mapping.outputs['Vector'], env_tex.inputs['Vector'])
                        node_tree.links.new(env_tex.outputs['Color'], background.inputs['Color'])
                        node_tree.links.new(background.outputs['Background'], output.inputs['Surface'])
                        
                        # 设置为活动世界环境
                        bpy.context.scene.world = world
                        
                        # 清理临时文件
                        try:
                            tempfile._cleanup()  # 这将清理所有临时文件
                        except:
                            pass
                        
                        return {
                            "success": True, 
                            "message": f"HDRI {asset_id} imported successfully",
                            "image_name": env_tex.image.name
                        }
                    except Exception as e:
                        return {"error": f"Failed to set up HDRI in Blender: {str(e)}"}
                else:
                    return {"error": f"Requested resolution or format not available for this HDRI"}
                    
            elif asset_type == "textures":
                # 对于纹理，下载 .jpg 或其他格式的文件
                if not file_format:
                    file_format = "jpg"  # 纹理默认格式为 jpg

                # 用于存储下载的纹理图像
                downloaded_maps = {}
                
                try:
                    for map_type in files_data:
                        # 跳过非纹理文件
                        if map_type not in ["blend", "gltf"]: 
                            # 检查请求的分辨率和格式是否可用
                            if resolution in files_data[map_type] and file_format in files_data[map_type][resolution]:
                                file_info = files_data[map_type][resolution][file_format]
                                file_url = file_info["url"]
                                
                                # 使用 NamedTemporaryFile 就像我们对 HDRIs 所做的那样
                                with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                                    # 下载文件
                                    response = requests.get(file_url)
                                    if response.status_code == 200:
                                        tmp_file.write(response.content)
                                        tmp_path = tmp_file.name
                                        
                                        # 从临时文件加载图像
                                        image = bpy.data.images.load(tmp_path)
                                        image.name = f"{asset_id}_{map_type}.{file_format}"
                                        
                                        # 将图像打包到 .blend 文件中
                                        image.pack()
                                        
                                        # 根据地图类型设置颜色空间
                                        if map_type in ['color', 'diffuse', 'albedo']:
                                            try:
                                                image.colorspace_settings.name = 'sRGB'
                                            except:
                                                pass
                                        else:
                                            try:
                                                image.colorspace_settings.name = 'Non-Color'
                                            except:
                                                pass
                                        
                                        downloaded_maps[map_type] = image
                                        
                                        # 清理临时文件
                                        try:
                                            os.unlink(tmp_path)
                                        except:
                                            pass
                
                    if not downloaded_maps:
                        return {"error": f"No texture maps found for the requested resolution and format"}
                    
                    # 创建具有下载纹理的新材质
                    mat = bpy.data.materials.new(name=asset_id)
                    mat.use_nodes = True
                    nodes = mat.node_tree.nodes
                    links = mat.node_tree.links
                    
                    # 清除默认节点
                    for node in nodes:
                        nodes.remove(node)
                    
                    # 创建输出节点
                    output = nodes.new(type='ShaderNodeOutputMaterial')
                    output.location = (300, 0)
                    
                    # 创建原理化 BSDF 节点
                    principled = nodes.new(type='ShaderNodeBsdfPrincipled')
                    principled.location = (0, 0)
                    links.new(principled.outputs[0], output.inputs[0])
                    
                    # 根据可用的贴图添加纹理节点
                    tex_coord = nodes.new(type='ShaderNodeTexCoord')
                    tex_coord.location = (-800, 0)
                    
                    mapping = nodes.new(type='ShaderNodeMapping')
                    mapping.location = (-600, 0)
                    mapping.vector_type = 'TEXTURE'  # 将默认的 'POINT' 更改为 'TEXTURE'
                    links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])
                    
                    # 纹理节点的定位偏移
                    x_pos = -400
                    y_pos = 300
                    
                    # 连接不同的纹理贴图
                    for map_type, image in downloaded_maps.items():
                        tex_node = nodes.new(type='ShaderNodeTexImage')
                        tex_node.location = (x_pos, y_pos)
                        tex_node.image = image
                        
                        # 根据地图类型设置颜色空间
                        if map_type.lower() in ['color', 'diffuse', 'albedo']:
                            try:
                                tex_node.image.colorspace_settings.name = 'sRGB'
                            except:
                                pass  # 如果 sRGB 不可用，则使用默认设置
                        else:
                            try:
                                tex_node.image.colorspace_settings.name = 'Non-Color'
                            except:
                                pass  # 如果 Non-Color 不可用，则使用默认设置
                        
                        links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])
                        
                        # 将纹理连接到原理化 BSDF 的适当输入
                        if map_type.lower() in ['color', 'diffuse', 'albedo']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                        elif map_type.lower() in ['roughness', 'rough']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
                        elif map_type.lower() in ['metallic', 'metalness', 'metal']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
                        elif map_type.lower() in ['normal', 'nor']:
                            # 添加法线贴图节点
                            normal_map = nodes.new(type='ShaderNodeNormalMap')
                            normal_map.location = (x_pos + 200, y_pos)
                            links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                            links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                        elif map_type in ['displacement', 'disp', 'height']:
                            # 添加位移节点
                            disp_node = nodes.new(type='ShaderNodeDisplacement')
                            disp_node.location = (x_pos + 200, y_pos - 200)
                            links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                            links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                        
                        y_pos -= 250
                    
                    return {
                        "success": True, 
                        "message": f"Texture {asset_id} imported as material",
                        "material": mat.name,
                        "maps": list(downloaded_maps.keys())
                    }
                
                except Exception as e:
                    return {"error": f"Failed to process textures: {str(e)}"}
                
            elif asset_type == "models":
                # 对于模型，优先选择 glTF 格式（如果可用）
                if not file_format:
                    file_format = "gltf"  # 模型默认格式为 gltf
                
                if file_format in files_data and resolution in files_data[file_format]:
                    file_info = files_data[file_format][resolution][file_format]
                    file_url = file_info["url"]
                    
                    # 创建临时目录以存储模型及其依赖项
                    temp_dir = tempfile.mkdtemp()
                    main_file_path = ""
                    
                    try:
                        # 下载主模型文件
                        main_file_name = file_url.split("/")[-1]
                        main_file_path = os.path.join(temp_dir, main_file_name)
                        
                        response = requests.get(file_url)
                        if response.status_code != 200:
                            return {"error": f"Failed to download model: {response.status_code}"}
                        
                        with open(main_file_path, "wb") as f:
                            f.write(response.content)
                        
                        # 检查是否有包含的文件并下载它们
                        if "include" in file_info and file_info["include"]:
                            for include_path, include_info in file_info["include"].items():
                                # 获取包含文件的 URL - 这是修复部分
                                include_url = include_info["url"]
                                
                                # 创建包含文件的目录结构
                                include_file_path = os.path.join(temp_dir, include_path)
                                os.makedirs(os.path.dirname(include_file_path), exist_ok=True)
                                
                                # 下载包含的文件
                                include_response = requests.get(include_url)
                                if include_response.status_code == 200:
                                    with open(include_file_path, "wb") as f:
                                        f.write(include_response.content)
                                else:
                                    print(f"Failed to download included file: {include_path}")
                        
                        # 将模型导入到 Blender
                        if file_format == "gltf" or file_format == "glb":
                            bpy.ops.import_scene.gltf(filepath=main_file_path)
                        elif file_format == "fbx":
                            bpy.ops.import_scene.fbx(filepath=main_file_path)
                        elif file_format == "obj":
                            bpy.ops.import_scene.obj(filepath=main_file_path)
                        elif file_format == "blend":
                            # 对于 blend 文件，我们需要附加或链接
                            with bpy.data.libraries.load(main_file_path, link=False) as (data_from, data_to):
                                data_to.objects = data_from.objects
                            
                            # 将对象链接到场景中
                            for obj in data_to.objects:
                                if obj is not None:
                                    bpy.context.collection.objects.link(obj)
                        else:
                            return {"error": f"Unsupported model format: {file_format}"}
                        
                        # 获取导入对象的名称
                        imported_objects = [obj.name for obj in bpy.context.selected_objects]
                        
                        return {
                            "success": True, 
                            "message": f"Model {asset_id} imported successfully",
                            "imported_objects": imported_objects
                        }
                    except Exception as e:
                        return {"error": f"Failed to import model: {str(e)}"}
                    finally:
                        # 清理临时目录
                        try:
                            shutil.rmtree(temp_dir)
                        except:
                            print(f"Failed to clean up temporary directory: {temp_dir}")
                else:
                    return {"error": f"Requested format or resolution not available for this model"}
                
            else:
                return {"error": f"Unsupported asset type: {asset_type}"}
                
        except Exception as e:
            return {"error": f"Failed to download asset: {str(e)}"}

    def set_texture(self, object_name, texture_id):
        """
        通过创建一个新的材质，将之前下载的 Polyhaven 纹理应用到指定对象上。

        参数:
            object_name (str): 要应用纹理的对象名称。
            texture_id (str): 要应用的 Polyhaven 纹理 ID（必须已下载）。

        返回:
            dict: 包含应用纹理结果的字典或错误信息。
        """
        try:
            # 获取指定名称的对象
            obj = bpy.data.objects.get(object_name)
            if not obj:
                # 如果对象不存在，返回错误信息
                return {"error": f"Object not found: {object_name}"}
            
            # 确保对象可以接受材质
            if not hasattr(obj, 'data') or not hasattr(obj.data, 'materials'):
                # 如果对象无法接受材质，返回错误信息
                return {"error": f"Object {object_name} cannot accept materials"}
            
            # 查找所有与该纹理相关的图像，并确保它们已正确加载
            # 用于存储找到的纹理图像
            texture_images = {}
            for img in bpy.data.images:
                if img.name.startswith(texture_id + "_"):
                    # 从图像名称中提取地图类型
                    map_type = img.name.split('_')[-1].split('.')[0]
                    
                    # 强制重新加载图像
                    img.reload()
                    
                    # 确保正确的颜色空间
                    if map_type.lower() in ['color', 'diffuse', 'albedo']:
                        try:
                            # 设置为 sRGB 颜色空间
                            img.colorspace_settings.name = 'sRGB'
                        except:
                            pass
                    else:
                        try:
                            # 设置为非颜色颜色空间
                            img.colorspace_settings.name = 'Non-Color'
                        except:
                            pass
                    
                    # 确保图像已打包到 .blend 文件中
                    if not img.packed_file:
                        # 将图像打包到 .blend 文件中
                        img.pack()
                    
                    # 将图像存储在字典中
                    texture_images[map_type] = img
                    print(f"Loaded texture map: {map_type} - {img.name}")
                    
                    # 调试信息
                    print(f"Image size: {img.size[0]}x{img.size[1]}")
                    print(f"Color space: {img.colorspace_settings.name}")
                    print(f"File format: {img.file_format}")
                    print(f"Is packed: {bool(img.packed_file)}")

            if not texture_images:
                # 如果没有找到相关图像，返回错误信息
                return {"error": f"No texture images found for: {texture_id}. Please download the texture first."}
            
            # 创建一个新的材质名称
            new_mat_name = f"{texture_id}_material_{object_name}"
            
            # 移除任何已存在的同名材质以避免冲突
            existing_mat = bpy.data.materials.get(new_mat_name)
            if existing_mat:
                # 移除已存在的材质
                bpy.data.materials.remove(existing_mat)
            
            # 创建一个新的材质
            new_mat = bpy.data.materials.new(name=new_mat_name)
            # 启用节点系统
            new_mat.use_nodes = True
            
            # 设置材质节点
            # 获取节点列表
            nodes = new_mat.node_tree.nodes
            # 获取链接列表
            links = new_mat.node_tree.links
            
            # 清除默认节点
            nodes.clear()
            
            # 创建输出节点
            output = nodes.new(type='ShaderNodeOutputMaterial')
            # 设置节点位置
            output.location = (600, 0)
            
            # 创建原理化 BSDF 节点
            principled = nodes.new(type='ShaderNodeBsdfPrincipled')
            # 设置节点位置
            principled.location = (300, 0)
            # 连接节点
            links.new(principled.outputs[0], output.inputs[0])
            
            # 根据可用的贴图添加纹理节点
            # 创建纹理坐标节点
            tex_coord = nodes.new(type='ShaderNodeTexCoord')
            # 设置节点位置
            tex_coord.location = (-800, 0)
            
            # 创建映射节点
            mapping = nodes.new(type='ShaderNodeMapping')
            # 设置节点位置
            mapping.location = (-600, 0)
            # 将默认的 'POINT' 更改为 'TEXTURE'
            mapping.vector_type = 'TEXTURE'  # Changed from default 'POINT' to 'TEXTURE'
            # 连接节点
            links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])
            
            # 纹理节点的定位偏移
            x_pos = -400
            y_pos = 300
            
            # 连接不同的纹理贴图
            for map_type, image in texture_images.items():
                # 创建纹理图像节点
                tex_node = nodes.new(type='ShaderNodeTexImage')
                # 设置节点位置
                tex_node.location = (x_pos, y_pos)
                # 设置图像
                tex_node.image = image
                
                # 根据地图类型设置颜色空间
                if map_type.lower() in ['color', 'diffuse', 'albedo']:
                    try:
                        # 设置为 sRGB 颜色空间
                        tex_node.image.colorspace_settings.name = 'sRGB'
                    except:
                        pass  # 如果设置失败，则使用默认设置
                else:
                    try:
                        # 设置为非颜色颜色空间
                        tex_node.image.colorspace_settings.name = 'Non-Color'
                    except:
                        pass  # 如果设置失败，则使用默认设置
                
                # 连接节点
                links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])
                
                # 将纹理连接到原理化 BSDF 的适当输入
                if map_type.lower() in ['color', 'diffuse', 'albedo']:
                    # 连接基础颜色
                    links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                elif map_type.lower() in ['roughness', 'rough']:
                    # 连接粗糙度
                    links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
                elif map_type.lower() in ['metallic', 'metalness', 'metal']:
                    # 连接金属度
                    links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
                elif map_type.lower() in ['normal', 'nor', 'dx', 'gl']:
                    # 创建法线贴图节点
                    normal_map = nodes.new(type='ShaderNodeNormalMap')
                    # 设置节点位置
                    normal_map.location = (x_pos + 200, y_pos)
                    # 连接节点
                    links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                    # 连接节点
                    links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                elif map_type.lower() in ['displacement', 'disp', 'height']:
                    # 创建位移节点
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    # 设置节点位置
                    disp_node.location = (x_pos + 200, y_pos - 200)
                    # 减少位移强度
                    disp_node.inputs['Scale'].default_value = 0.1  
                    # 连接节点
                    links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                    # 连接节点
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                
                # 调整垂直位置
                y_pos -= 250
            
            # 第二遍连接节点，正确处理特殊情况
            texture_nodes = {}  # 用于存储纹理节点
            
            # 首先找到所有纹理节点并按地图类型存储
            for node in nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    for map_type, image in texture_images.items():
                        if node.image == image:
                            texture_nodes[map_type] = node
                            break
            
            # 现在使用节点而不是图像进行连接
            # 处理基础颜色（漫反射）
            for map_name in ['color', 'diffuse', 'albedo']:
                if map_name in texture_nodes:
                    # 连接基础颜色
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Base Color'])
                    print(f"Connected {map_name} to Base Color")
                    break
            
            # 处理粗糙度
            for map_name in ['roughness', 'rough']:
                if map_name in texture_nodes:
                    # 连接粗糙度
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Roughness'])
                    print(f"Connected {map_name} to Roughness")
                    break
            
            # 处理金属度
            for map_name in ['metallic', 'metalness', 'metal']:
                if map_name in texture_nodes:
                    # 连接金属度
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Metallic'])
                    print(f"Connected {map_name} to Metallic")
                    break
            
            # 处理法线贴图
            for map_name in ['gl', 'dx', 'nor']:
                if map_name in texture_nodes:
                    # 创建法线贴图节点
                    normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                    # 设置节点位置
                    normal_map_node.location = (100, 100)
                    # 连接节点
                    links.new(texture_nodes[map_name].outputs['Color'], normal_map_node.inputs['Color'])
                    # 连接节点
                    links.new(normal_map_node.outputs['Normal'], principled.inputs['Normal'])
                    print(f"Connected {map_name} to Normal")
                    break
            
            # 处理位移
            for map_name in ['displacement', 'disp', 'height']:
                if map_name in texture_nodes:
                    # 创建位移节点
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    # 设置节点位置
                    disp_node.location = (300, -200)
                    # 减少位移强度
                    disp_node.inputs['Scale'].default_value = 0.1  
                    # 连接节点
                    links.new(texture_nodes[map_name].outputs['Color'], disp_node.inputs['Height'])
                    # 连接节点
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                    print(f"Connected {map_name} to Displacement")
                    break
            
            # 处理 ARM 纹理（环境光遮蔽、粗糙度、金属度）
            if 'arm' in texture_nodes:
                # 创建分离 RGB 节点
                separate_rgb = nodes.new(type='ShaderNodeSeparateRGB')
                # 设置节点位置
                separate_rgb.location = (-200, -100)
                # 连接节点
                links.new(texture_nodes['arm'].outputs['Color'], separate_rgb.inputs['Image'])
                
                # 如果没有专用的粗糙度贴图，则连接 ARM.G 到粗糙度
                if not any(map_name in texture_nodes for map_name in ['roughness', 'rough']):
                    # 连接粗糙度
                    links.new(separate_rgb.outputs['G'], principled.inputs['Roughness'])
                    print("Connected ARM.G to Roughness")
                
                # 如果没有专用的金属度贴图，则连接 ARM.B 到金属度
                if not any(map_name in texture_nodes for map_name in ['metallic', 'metalness', 'metal']):
                    # 连接金属度
                    links.new(separate_rgb.outputs['B'], principled.inputs['Metallic'])
                    print("Connected ARM.B to Metallic")
                
                # 对于 AO（R 通道），如果我们有基础颜色，则进行混合
                base_color_node = None
                for map_name in ['color', 'diffuse', 'albedo']:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break
                
                if base_color_node:
                    # 创建混合 RGB 节点
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    # 设置节点位置
                    mix_node.location = (100, 200)
                    # 设置混合类型为乘法
                    mix_node.blend_type = 'MULTIPLY'
                    # 设置混合因子为 80%
                    mix_node.inputs['Fac'].default_value = 0.8 
                    
                    # 断开与基础颜色的直接连接
                    for link in base_color_node.outputs['Color'].links:
                        if link.to_socket == principled.inputs['Base Color']:
                            links.remove(link)
                    
                    # 通过混合节点进行连接
                    # 连接节点
                    links.new(base_color_node.outputs['Color'], mix_node.inputs[1])
                    links.new(separate_rgb.outputs['R'], mix_node.inputs[2])
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])
                    print("Connected ARM.R to AO mix with Base Color")
            
            # 如果有单独的环境光遮蔽 (AO) 贴图，则进行混合
            if 'ao' in texture_nodes:
                base_color_node = None
                for map_name in ['color', 'diffuse', 'albedo']:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break
                
                if base_color_node:
                    # 创建混合 RGB 节点
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.location = (100, 200)
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 0.8  # 80% influence
                    
                    # 断开与基础颜色的直接连接
                    for link in base_color_node.outputs['Color'].links:
                        if link.to_socket == principled.inputs['Base Color']:
                            links.remove(link)
                    
                    # 通过混合节点进行连接
                    links.new(base_color_node.outputs['Color'], mix_node.inputs[1])
                    links.new(texture_nodes['ao'].outputs['Color'], mix_node.inputs[2])
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])
                    print("Connected AO to mix with Base Color")
            
            # 关键步骤：确保清除对象上所有现有的材质
            while len(obj.data.materials) > 0:
                # 移除所有现有材质
                obj.data.materials.pop(index=0)
            
            # 将新材质分配给对象
            obj.data.materials.append(new_mat)  # 添加新材质
            
            # 关键步骤：使对象成为活动对象并选择它
            bpy.context.view_layer.objects.active = obj  # 设置活动对象
            obj.select_set(True)  # 选择对象
            
            # 关键步骤：强制 Blender 更新材质
            bpy.context.view_layer.update()  # 更新视图层
            
            # 获取纹理贴图列表
            texture_maps = list(texture_images.keys())
            
            # 获取材质节点信息以供调试
            material_info = {
                "name": new_mat.name,  # 材质名称
                "has_nodes": new_mat.use_nodes,  # 是否使用节点
                "node_count": len(new_mat.node_tree.nodes),  # 节点数量
                "texture_nodes": []  # 纹理节点列表
            }
            
            for node in new_mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    # 连接列表
                    connections = []
                    for output in node.outputs:
                        for link in output.links:
                            # 添加连接信息
                            connections.append(f"{output.name} → {link.to_node.name}.{link.to_socket.name}")
                    
                    material_info["texture_nodes"].append({
                        "name": node.name,  # 节点名称
                        "image": node.image.name,  # 图像名称
                        "colorspace": node.image.colorspace_settings.name,  # 颜色空间
                        "connections": connections  # 连接列表
                    })
            
            return {
                "success": True,  # 操作成功
                "message": f"Created new material and applied texture {texture_id} to {object_name}",  # 成功消息
                "material": new_mat.name,  # 新材质名称
                "maps": texture_maps,  # 纹理贴图列表
                "material_info": material_info  # 材质节点信息
            }
            
        except Exception as e:
            print(f"Error in set_texture: {str(e)}")
            # 打印详细的错误堆栈跟踪
            traceback.print_exc()
            return {"error": f"Failed to apply texture: {str(e)}"}

    def get_polyhaven_status(self):
        """
        获取 PolyHaven 集成的当前状态。

        返回:
            dict: 包含 PolyHaven 集成状态的字典。
        """
        # 从场景属性中获取 PolyHaven 是否启用的标志
        enabled = bpy.context.scene.blendermcp_use_polyhaven
        if enabled:
            # 如果启用，返回启用消息
            return {"enabled": True, "message": "PolyHaven integration is enabled and ready to use."}
        else:
            # 如果未启用，返回禁用消息和启用步骤
            return {
                "enabled": False, 
                "message": """PolyHaven integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use assets from Poly Haven' checkbox
                            3. Restart the connection to Claude"""
        }


# Blender UI 面板类：用于在 Blender 界面中显示 BlenderMCP 面板
class BLENDERMCP_PT_Panel(bpy.types.Panel):
    """
    BlenderMCP 面板类，用于在 Blender 界面中显示相关设置和操作按钮。
    """
    bl_label = "Blender MCP"  # 面板的标题
    bl_idname = "BLENDERMCP_PT_Panel"  # 面板的唯一标识符
    bl_space_type = 'VIEW_3D'  # 面板所在的区域类型，这里是 3D 视口
    bl_region_type = 'UI'  # 面板所在的区域，这里是侧边栏
    bl_category = 'BlenderMCP'  # 面板在侧边栏中的标签
    
    def draw(self, context):
        """
        绘制面板内容。

        参数:
            context (bpy.context): Blender 的上下文对象。
        """
        # 获取面板布局
        layout = self.layout
        # 获取当前场景
        scene = context.scene
        
        # 在面板中添加端口号属性
        layout.prop(scene, "blendermcp_port")
        # 在面板中添加使用 Poly Haven 资产的复选框
        layout.prop(scene, "blendermcp_use_polyhaven", text="Use assets from Poly Haven")
        
        # 如果服务器未运行，则显示“启动 MCP 服务器”按钮
        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Start MCP Server")
        else:
            # 如果服务器正在运行，则显示“停止 MCP 服务器”按钮和运行信息
            layout.operator("blendermcp.stop_server", text="Stop MCP Server")
            layout.label(text=f"Running on port {scene.blendermcp_port}")


# 操作符类：用于启动 MCP 服务器
class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    """
    操作符类，用于启动 BlenderMCP 服务器并连接到 Claude。
    """
    bl_idname = "blendermcp.start_server"  # 操作符的唯一标识符
    bl_label = "Connect to Claude"  # 操作符的显示名称
    bl_description = "Start the BlenderMCP server to connect with Claude"  # 操作符的描述
    
    def execute(self, context):
        """
        执行启动服务器的操作。

        参数:
            context (bpy.context): Blender 的上下文对象。

        返回:
            dict: 操作执行结果。
        """
        # 获取当前场景
        scene = context.scene
        
        # 创建新的服务器实例（如果尚未创建）
        if not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server:
            bpy.types.blendermcp_server = BlenderMCPServer(port=scene.blendermcp_port)
        
        # 启动服务器
        bpy.types.blendermcp_server.start()
        # 更新场景属性以指示服务器正在运行
        scene.blendermcp_server_running = True

        # 返回操作完成标志
        return {'FINISHED'}


# 操作符类：用于停止 MCP 服务器
class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    """
    操作符类，用于停止 BlenderMCP 服务器并断开与 Claude 的连接。
    """
    bl_idname = "blendermcp.stop_server"  # 操作符的唯一标识符
    bl_label = "Stop the connection to Claude"  # 操作符的显示名称
    bl_description = "Stop the connection to Claude"  # 操作符的描述
    
    def execute(self, context):
        """
        执行停止服务器的操作。

        参数:
            context (bpy.context): Blender 的上下文对象。

        返回:
            dict: 操作执行结果。
        """
        # 获取当前场景
        scene = context.scene
        
        # 如果服务器存在，则停止服务器
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            # 调用服务器的停止方法
            bpy.types.blendermcp_server.stop()
            # 删除服务器实例
            del bpy.types.blendermcp_server
        # 更新场景属性以指示服务器已停止
        scene.blendermcp_server_running = False
        
        # 返回操作完成标志
        return {'FINISHED'}


# 注册函数：用于注册 Blender 插件的类和属性
def register():
    """
    注册 Blender 插件的类和属性。
    """
    # 注册场景属性：端口号
    bpy.types.Scene.blendermcp_port = IntProperty(
        name="Port",
        description="Port for the BlenderMCP server",
        default=9876,
        min=1024,
        max=65535
    )
    
    # 注册场景属性：服务器是否正在运行
    bpy.types.Scene.blendermcp_server_running = bpy.props.BoolProperty(
        name="Server Running",
        default=False
    )
    
    # 注册场景属性：是否使用 Poly Haven 资产
    bpy.types.Scene.blendermcp_use_polyhaven = bpy.props.BoolProperty(
        name="Use Poly Haven",
        description="Enable Poly Haven asset integration",
        default=False
    )
    
    # 注册面板类
    bpy.utils.register_class(BLENDERMCP_PT_Panel)
    # 注册启动服务器的操作符类
    bpy.utils.register_class(BLENDERMCP_OT_StartServer)
    # 注册停止服务器的操作符类
    bpy.utils.register_class(BLENDERMCP_OT_StopServer)

    # 在控制台打印注册完成信息
    print("BlenderMCP addon registered")


# 反注册函数：用于反注册 Blender 插件的类和属性
def unregister():
    """
    反注册 Blender 插件的类和属性。
    """
    # 如果服务器正在运行，则停止服务器
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        # 调用服务器的停止方法
        bpy.types.blendermcp_server.stop()
        # 删除服务器实例
        del bpy.types.blendermcp_server
    
    # 反注册面板类
    bpy.utils.unregister_class(BLENDERMCP_PT_Panel)
    # 反注册启动服务器的操作符类
    bpy.utils.unregister_class(BLENDERMCP_OT_StartServer)
    # 反注册停止服务器的操作符类
    bpy.utils.unregister_class(BLENDERMCP_OT_StopServer)
    
    # 删除场景属性
    del bpy.types.Scene.blendermcp_port
    del bpy.types.Scene.blendermcp_server_running
    del bpy.types.Scene.blendermcp_use_polyhaven
    
    # 在控制台打印反注册完成信息
    print("BlenderMCP addon unregistered")


# 主函数：插件的入口点
if __name__ == "__main__":
    # 调用注册函数以注册插件的类和属性
    register()
