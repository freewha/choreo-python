import asyncio
import uuid
import struct
import ipaddress
import socket
import os
from datetime import datetime
from websockets.server import serve, ServerConnection
from typing import Dict, Any
from cloudflare_tunnel import init_tunnel
import logging
from wsrv import app as flask_app
logging.getLogger("websockets").setLevel(logging.CRITICAL)
from threading import Thread
from datetime import datetime
CONFIG = {
    "UUID": os.environ.get("UUID", "7cf345cc-4713-43c4-aa99-c67644f9f749"),
    "HOST": "0.0.0.0",
    "PORT": 59999,
    "WS_PATH": "/vless",
    "VLESS_HEADER_SIZE": 24,
    "MAX_MESSAGE_SIZE": 100 * 1024 * 1024,
    "TCP_TIMEOUT": 30,
    "DEBUG": True
}

def log(message: str, level: str = "INFO"):
    pass
    # timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    # print(f"{timestamp} - {level} - {message}")

def validate_uuid(uuid_str: str) -> bool:
    try:
        uuid.UUID(uuid_str)
        return True
    except ValueError:
        return False

def uuid_buffer_to_string(data: bytes, offset: int = 0) -> str:
    return (data[offset:offset+4].hex() + "-" +
            data[offset+4:offset+6].hex() + "-" +
            data[offset+6:offset+8].hex() + "-" +
            data[offset+8:offset+10].hex() + "-" +
            data[offset+10:offset+16].hex())

def get_validated_uuid(data: bytes, offset: int = 0) -> str:
    uuid_str = uuid_buffer_to_string(data, offset)
    if not validate_uuid(uuid_str):
        raise ValueError(f"Invalid UUID: {uuid_str}")
    return uuid_str

async def resolve_domain(domain: str) -> str:
    try:
        loop = asyncio.get_running_loop()
        addr_info = await loop.run_in_executor(None, lambda: socket.getaddrinfo(domain, None, socket.AF_INET)[0])
        return addr_info[4][0]
    except socket.gaierror as e:
        log(f"DNS resolution failed for {domain}: {str(e)}", "ERROR")
        raise

def process_vless_header(buffer: bytes, user_id: str) -> Dict[str, Any]:
    if len(buffer) < CONFIG["VLESS_HEADER_SIZE"]:
        return {"has_error": True, "message": f"Header too short"}

    try:
        version = buffer[0]
        header_user_id = get_validated_uuid(buffer, 1)
        if header_user_id != user_id:
            return {"has_error": True, "message": "User authentication failed"}

        opt_length = buffer[17]
        cmd_pos = 18 + opt_length
        command = buffer[cmd_pos]
        if command != 1:
            return {"has_error": True, "message": f"Unsupported command: {command}"}

        port_remote = struct.unpack("!H", buffer[cmd_pos + 1:cmd_pos + 3])[0]
        addr_type_pos = cmd_pos + 3
        address_type = buffer[addr_type_pos]
        addr_value_pos = addr_type_pos + 1
        address_value = ""

        if address_type == 1:
            address_value = str(ipaddress.IPv4Address(buffer[addr_value_pos:addr_value_pos + 4]))
            addr_length = 4
        elif address_type == 2:
            addr_length = buffer[addr_value_pos]
            addr_value_pos += 1
            address_value = buffer[addr_value_pos:addr_value_pos + addr_length].decode("utf-8")
        elif address_type == 3:
            address_value = str(ipaddress.IPv6Address(buffer[addr_value_pos:addr_value_pos + 16]))
            addr_length = 16
        else:
            return {"has_error": True, "message": f"Invalid address type: {address_type}"}

        return {
            "has_error": False,
            "address_remote": address_value,
            "port_remote": port_remote,
            "raw_data_index": addr_value_pos + addr_length,
            "vless_version": version,
            "address_type": address_type
        }
    except Exception as e:
        return {"has_error": True, "message": f"Header parse error: {str(e)}"}

async def handle_ws_connection(connection: ServerConnection):
    path = connection.path
    if path != CONFIG["WS_PATH"]:
        log(f"Invalid WebSocket path: {path}", "ERROR")
        await connection.close(code=1008, reason="Invalid path")
        return

    try:
        header_data = await asyncio.wait_for(connection.recv(), timeout=CONFIG["TCP_TIMEOUT"])
        if isinstance(header_data, str):
            header_data = header_data.encode("utf-8")

        header_info = process_vless_header(header_data, CONFIG["UUID"])
        if header_info["has_error"]:
            log(f"VLESS header error: {header_info['message']}", "ERROR")
            await connection.close(code=1008, reason="Invalid VLESS header")
            return

        address = header_info["address_remote"]
        if header_info["address_type"] == 2:
            address = await resolve_domain(address)

        client_data = header_data[header_info["raw_data_index"]:] if len(header_data) > header_info["raw_data_index"] else b""

        remote_reader, remote_writer = await asyncio.wait_for(
            asyncio.open_connection(address, header_info["port_remote"]),
            timeout=CONFIG["TCP_TIMEOUT"]
        )

        await connection.send(bytes([header_info["vless_version"], 0]))
        if client_data:
            remote_writer.write(client_data)
            await remote_writer.drain()

        async def client_to_remote():
            try:
                while True:
                    data = await asyncio.wait_for(connection.recv(), timeout=CONFIG["TCP_TIMEOUT"])
                    if isinstance(data, str):
                        data = data.encode("utf-8")
                    if not data:
                        break
                    remote_writer.write(data)
                    await remote_writer.drain()
            except Exception as e:
                log(f"Client to remote error: {str(e)}", "ERROR")
            finally:
                remote_writer.close()
                await remote_writer.wait_closed()

        async def remote_to_client():
            try:
                while True:
                    data = await asyncio.wait_for(remote_reader.read(CONFIG["MAX_MESSAGE_SIZE"]), timeout=CONFIG["TCP_TIMEOUT"])
                    if not data:
                        break
                    await connection.send(data)
            except Exception as e:
                log(f"Remote to client error: {str(e)}", "ERROR")
            finally:
                await connection.close(code=1000)

        await asyncio.gather(client_to_remote(), remote_to_client())

    except Exception as e:
        log(f"WebSocket connection failed: {str(e)}", "ERROR")
        await connection.close(code=1008, reason="Connection failed")

# 假设你已有 handle_ws_connection 函数
async def start_ws():
    try:
        tunnel_domain = init_tunnel(CONFIG['PORT'])
        CONFIG['DISGUISED_DOMAIN'] = tunnel_domain
    except Exception as e:
        log(f"隧道初始化失败: {e}", "ERROR")
        return

    async with serve(
        handle_ws_connection,
        CONFIG["HOST"],
        CONFIG["PORT"],
        max_size=CONFIG["MAX_MESSAGE_SIZE"],
        ping_interval=None,
        ping_timeout=CONFIG["TCP_TIMEOUT"]
    ):
        log(f"WebSocket proxy server started on ws://{CONFIG['HOST']}:{CONFIG['PORT']}{CONFIG['WS_PATH']}", "INFO")
        await asyncio.Future()

def run_ws():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(start_ws())
    except Exception as e:
        log(f"WebSocket failed: {str(e)}", "ERROR")
    finally:
        loop.close()

# # 启动 WebSocket 服务线程
# Thread(target=run_ws, daemon=True).start()
# # ✅ 关键补充
# app = flask_app
if __name__ == "__main__":
    # 启动 WebSocket 服务线程
    Thread(target=run_ws, daemon=True).start()

    # 启动 Flask 服务
    flask_app.run()