import asyncio
import base64

from fastapi import APIRouter, Cookie, WebSocket, WebSocketDisconnect

import config
import docker_utils
from auth import _get_session, _user_can_access_bot
from utils import sanitize_name

router = APIRouter()


@router.websocket("/api/bots/{name}/terminal")
async def ws_terminal(name: str, websocket: WebSocket, cfm_session: str | None = Cookie(None)):
    """Interactive terminal into a running bot container via Docker exec."""
    # Auth (WebSocket can't use Depends)
    if config.AUTH_DISABLED:
        session = {"username": "dev", "role": "admin", "bots": ["*"]}
    else:
        session = _get_session(cfm_session) if cfm_session else None
        if not session:
            await websocket.close(code=4401, reason="Not authenticated")
            return
    try:
        sname = sanitize_name(name)
    except ValueError as e:
        await websocket.close(code=4400, reason=str(e))
        return
    if not _user_can_access_bot(session, sname):
        await websocket.close(code=4403, reason="Access denied")
        return

    await websocket.accept()

    # Container lookup
    client = docker_utils._get_client()
    container_name = f"openclaw-bot-{sname}"
    try:
        import docker
        container = client.containers.get(container_name)
        if container.status != "running":
            await websocket.send_json({"type": "error", "message": "Container is not running"})
            await websocket.close()
            return
    except docker.errors.NotFound:
        await websocket.send_json({"type": "error", "message": "Container not found"})
        await websocket.close()
        return
    except Exception:
        await websocket.send_json({"type": "error", "message": "Docker error"})
        await websocket.close()
        return

    # Create exec with PTY
    api_client = client.api
    exec_id = api_client.exec_create(
        container_name, "/bin/bash",
        stdin=True, stdout=True, stderr=True, tty=True,
        user="node", workdir="/home/node",
    )["Id"]
    sock = api_client.exec_start(exec_id, tty=True, socket=True, demux=False)
    # docker-py returns a SocketIO wrapper; ._sock is the underlying socket.
    # This is the standard pattern — no public API exists for bidirectional exec I/O.
    raw_sock = sock._sock
    raw_sock.settimeout(2.0)  # Prevent blocking forever when WS disconnects

    loop = asyncio.get_running_loop()
    closed = asyncio.Event()

    async def _read_from_container():
        """Read PTY output and forward to WebSocket as base64."""
        try:
            while not closed.is_set():
                try:
                    data = await loop.run_in_executor(None, lambda: raw_sock.recv(4096))
                except OSError:
                    break  # Socket timeout or closed
                if not data:
                    break
                await websocket.send_json({
                    "type": "data",
                    "data": base64.b64encode(data).decode("ascii"),
                })
        except Exception:
            pass
        finally:
            closed.set()
            # Unblock _read_from_websocket by closing the WS
            try:
                await websocket.close()
            except Exception:
                pass

    async def _read_from_websocket():
        """Read WebSocket messages and forward to PTY / handle resize."""
        try:
            while not closed.is_set():
                msg = await websocket.receive_json()
                if msg.get("type") == "data":
                    raw = base64.b64decode(msg["data"])
                    await loop.run_in_executor(None, lambda r=raw: raw_sock.sendall(r))
                elif msg.get("type") == "resize":
                    cols = msg.get("cols", 80)
                    rows = msg.get("rows", 24)
                    try:
                        api_client.exec_resize(exec_id, height=rows, width=cols)
                    except Exception:
                        pass
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:
            pass
        finally:
            closed.set()
            # Unblock _read_from_container by closing the socket
            try:
                raw_sock.shutdown(2)
            except Exception:
                pass

    try:
        await asyncio.gather(_read_from_container(), _read_from_websocket())
    finally:
        try:
            sock.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
